"""Opt-in ProcTHOR/RBY-1 verification. No hand-written role bindings or edit poses."""
import argparse
from dataclasses import asdict, replace
import multiprocessing
from pathlib import Path
import time
import uuid

from lastmile_dataflow.config import CollectionConfig, RobotConfig, load_config
from lastmile_dataflow.construction.case_schema import CaseEditRequest, GenerationConfig
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.recording.edit_views import ViewConfig, render_edit_pair
from lastmile_dataflow.runtime.preparation import prepare_scene, SettleConfig
from lastmile_dataflow.scenes.source import SceneSource
from lastmile_dataflow.workflows.case_edit_supervisor import supervised_case_edit


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTIONS = {
    'distance': '让一个小物体仍被支撑，但与机器人初始站位在水平面上明显拉开距离，布局变化应可见；这只是距离困难的几何代理，不要求宣称真正不可达。',
    'direction': '让两个仍被支撑的可见物体呈现明显不同的朝向，以其局部坐标轴夹角作为几何代理；物体形状无法显示朝向时不能判定视觉通过。',
}


def _prepare_worker(source, robot, output, snapshot, timeout_s, settle_config):
    output = Path(output)
    report = {'scope': 'real_cached_scene_unbound_preparation_and_RGB_only_not_case_acceptance', 'status': 'failed'}
    try:
        with prepare_scene(SceneSource(**source), RobotConfig(**robot), initial_snapshot=snapshot,
                           deadline=time.monotonic()+timeout_s, path=output/'baseline',
                           settle_config=SettleConfig(**settle_config)) as prepared:
            names = {k for k, n in prepared.graph.nodes.items() if n.get('kind') == 'object' and n.get('root_motion') == 'free'}
            packet = render_edit_pair(prepared.sim, prepared.sim, prepared.graph, prepared.graph,
                names, output/'rgb', sample_id='baseline_only', config=ViewConfig(width=320, height=240))
            report.update(status='prepared', target_binding=None, initialization=prepared.initialization,
                          settling=prepared.settling, node_count=len(prepared.graph.nodes),
                          support_edge_count=sum(e['predicate'] == 'supported_by' for e in prepared.graph.edges),
                          real_rgb_count=len(packet['images']), baseline_graph_id=prepared.graph.to_dict()['graph_id'])
    except Exception as exc:
        report.update(error_type=type(exc).__name__, reason=str(exc)[:2000])
        if hasattr(exc, 'details'):
            report['details'] = exc.details
    write_json(output/'result.json', report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-settings', type=Path)
    parser.add_argument('--provider', help='auto or configured provider name')
    parser.add_argument('--prepare-only', action='store_true', help='no external model or accepted samples')
    parser.add_argument('--diagnostic', action='store_true', help='allow one-house retries; never full gate')
    parser.add_argument('--cases', nargs='+', choices=list(DESCRIPTIONS), default=list(DESCRIPTIONS))
    parser.add_argument('--expected-only', action='store_true', help='diagnostic expected failures, not full acceptance gate')
    parser.add_argument('--skip-expected-failures', action='store_true')
    parser.add_argument('--houses', nargs='+', type=int, default=[0, 2])
    parser.add_argument('--dataset-dir', type=Path, default=ROOT.parent/'molmospaces_data/assets/scenes/procthor-10k-train')
    parser.add_argument('--robot-config', type=Path, default=ROOT/'configs/robots/rby1.json')
    parser.add_argument('--snapshot-root', type=Path, help='optional root with phase1-final-trainN/scene; no target or pose input')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--settle-config', type=Path)
    parser.add_argument('--prefix', default='case-edit-real-' + uuid.uuid4().hex[:8])
    args = parser.parse_args()
    if not args.prepare_only and args.api_settings is None:
        parser.error('external-model acceptance requires explicitly authorized --api-settings')
    if not args.prepare_only and not args.diagnostic and len(args.houses) < 2:
        parser.error('S7 acceptance requires at least two real scenes')
    robot = load_config(RobotConfig, args.robot_config)
    settle_config = SettleConfig(**read_json(args.settle_config)) if args.settle_config else SettleConfig()
    report_path = ROOT/'reports/checks'/(args.prefix+'.json')
    report = {'scope': 'preparation_only' if args.prepare_only else 'real_three_agent_scene_acceptance_not_robot_task',
              'runs': [], 'completed': False}
    for house in ([] if args.expected_only else args.houses):
        source = SceneSource.procthor(args.dataset_dir, house)
        snapshot = args.snapshot_root/f'phase1-final-train{house}/scene' if args.snapshot_root else None
        if args.prepare_only:
            output = ROOT/'outputs/case_edits'/f'{args.prefix}-train{house}'
            output.mkdir(parents=True, exist_ok=False)
            process = multiprocessing.get_context('spawn').Process(target=_prepare_worker,
                args=(asdict(source), asdict(robot), str(output), str(snapshot) if snapshot else None, args.timeout, asdict(settle_config)))
            process.start()
            process.join(args.timeout)
            if process.is_alive():
                process.terminate(); process.join(2)
                if process.is_alive(): process.kill(); process.join(2)
            result = read_json(output/'result.json') if (output/'result.json').exists() else {'status': 'infrastructure_or_timeout', 'exitcode': process.exitcode}
            report['runs'].append({'house': house, 'output': str(output), 'result': result})
            print(house, result['status'], flush=True)
            write_json(report_path, report)
            continue
        for name in args.cases:
            description = DESCRIPTIONS[name]
            request = CaseEditRequest(description, asdict(source), generation=GenerationConfig(
                target_scene_count=3, max_rounds=4, max_samples=48, max_agent_calls=24, timeout_s=args.timeout))
            path = supervised_case_edit(request, robot, CollectionConfig(output_dir=str(ROOT/'outputs')),
                api_settings=args.api_settings, provider=args.provider, initial_snapshot=snapshot, run_id=f'{args.prefix}-train{house}-{name}', settle_config=settle_config)
            result = read_json(path/'result.json')
            report['runs'].append({'house': house, 'case': name, 'output': str(path), 'result': result})
            print(house, name, result['status'], result['accepted_count'], flush=True)
            write_json(report_path, report)
    if not args.prepare_only and not args.skip_expected_failures:
        source = SceneSource.procthor(args.dataset_dir, args.houses[0])
        snapshot = args.snapshot_root/f'phase1-final-train{args.houses[0]}/scene' if args.snapshot_root else None
        failures = [('missing_role', '场景中必须有一颗钻石，编辑它的布局，若没有钻石则不适用。', GenerationConfig(target_scene_count=1, max_rounds=1, max_samples=3, max_agent_calls=4, timeout_s=args.timeout)),
                    ('no_candidates', '把一个仍被支撑的小物体放在水平距离机器人一百万米以外；若场景不支持，必须说明没有候选。', GenerationConfig(target_scene_count=1, max_rounds=1, max_samples=3, max_agent_calls=4, timeout_s=args.timeout)),
                    ('budget', DESCRIPTIONS['distance'], GenerationConfig(target_scene_count=3, max_samples=0, max_agent_calls=0, timeout_s=args.timeout))]
        for name, description, generation in failures:
            path = supervised_case_edit(CaseEditRequest(description, asdict(source), generation=generation), robot,
                CollectionConfig(output_dir=str(ROOT/'outputs')), api_settings=args.api_settings, provider=args.provider, initial_snapshot=snapshot,
                run_id=f'{args.prefix}-expected-{name}', settle_config=settle_config)
            result = read_json(path/'result.json')
            report['runs'].append({'expected_failure': name, 'output': str(path), 'result': result,
                                  'matched': False})
            from verify_case_edit_delivery import expected_failure
            report['runs'][-1]['matched'] = expected_failure(report['runs'][-1])
            write_json(report_path, report)
    report['completed'] = all(r['result']['status'] == 'prepared' for r in report['runs']) if args.prepare_only else (
        all(r.get('matched', r['result']['status'] == 'completed' and r['result']['accepted_count'] >= 3) for r in report['runs']))
    report['acceptance_gate'] = report['completed'] and not args.prepare_only and len(set(args.houses)) >= 2 and len(set(args.cases)) >= 2 and not args.skip_expected_failures and not args.expected_only and not args.diagnostic
    write_json(report_path, report)
    print(report_path, flush=True)
    return 0 if report['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
