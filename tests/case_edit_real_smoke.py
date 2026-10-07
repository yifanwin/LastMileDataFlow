"""Explicit real-asset/HTTP acceptance; not part of unittest or mobile task proof."""
import argparse
from pathlib import Path
import time

from lastmile_dataflow.config import RobotConfig, CollectionConfig, load_config
from lastmile_dataflow.construction.scene_request import load_scene_request
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.recording.edit_views import ViewConfig
from lastmile_dataflow.runtime.preparation import prepare_scene, SettleConfig
from lastmile_dataflow.runtime.visible_initialization import prepare_visible, VisibleInitializationUnavailable
from lastmile_dataflow.scenes.local_context import sample_contexts
from lastmile_dataflow.workflows.case_edit_supervisor import supervised_case_edit

ROOT = Path(__file__).resolve().parents[1]


def prepare_only(request, robot, collection, settle, views):
    path = Path(collection.output_dir)/'case_construction'/('prepare-' + str(time.time_ns()))
    path.mkdir(parents=True, exist_ok=False)
    write_json(path/'expectation.json', {'question': 'Can the exact source produce an automatically selected legal target-visible initial state?',
        'expected': 'actual head RGB, measured visibility, no manual instance/base binding', 'task_completion': 'not_tested'})
    deadline = time.monotonic()+request.budgets.timeout_s
    try:
        with prepare_scene(request.scene_input.resolve(), robot, collection, path=path/'source_baseline',
                           settle_config=settle, deadline=deadline) as prepared:
            contexts, rejected = sample_contexts(prepared.graph, request.construction, seed=request.seed)
            write_json(path/'contexts.json', {'contexts': [c.to_dict() for c in contexts], 'rejected': rejected})
            for index, context in enumerate(contexts):
                try:
                    visible, observation = prepare_visible(prepared, context, request.construction, collection,
                        path/('init-' + str(index)), seed=request.seed+index, deadline=deadline, settle_config=settle, view_config=views)
                    try:
                        result = {'status': 'head_observation_ready', 'target': context.target,
                                  'observation': observation, 'case_condition': 'unknown', 'task_completion': 'unknown'}
                    finally:
                        visible.close()
                    break
                except VisibleInitializationUnavailable:
                    continue
            else:
                result = {'status': 'no_visible_initialization_found_within_budget'}
    except Exception as exc:
        result = {'status': 'failed', 'reason': type(exc).__name__ + ':' + str(exc), 'details': getattr(exc, 'details', {})}
    write_json(path/'result.json', result)
    return path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--request', type=Path, nargs='+', required=True)
    p.add_argument('--api-settings', type=Path)
    p.add_argument('--provider', default='auto')
    p.add_argument('--model', help='explicit comparison model; local settings remain unchanged')
    p.add_argument('--robot-config', type=Path, default=ROOT/'configs/robots/rby1.json')
    p.add_argument('--collection-config', type=Path, default=ROOT/'configs/collection/smoke.json')
    p.add_argument('--settle-config', type=Path, default=ROOT/'configs/case_edits/settling.json')
    p.add_argument('--view-config', type=Path, default=ROOT/'configs/case_edits/views.json')
    p.add_argument('--prepare-only', action='store_true')
    args = p.parse_args()
    if not args.prepare_only and not args.api_settings:
        p.error('--api-settings is required for real model calls')
    robot = load_config(RobotConfig, args.robot_config)
    collection = load_config(CollectionConfig, args.collection_config)
    settle = SettleConfig(**read_json(args.settle_config))
    views = ViewConfig(**read_json(args.view_config))
    passed = True
    for file in args.request:
        request = load_scene_request(file)
        path = prepare_only(request, robot, collection, settle, views) if args.prepare_only else supervised_case_edit(
            request, robot, collection, api_settings=args.api_settings, provider=args.provider, model=args.model,
            settle_config=settle, view_config=views)
        result = read_json(path/'result.json')
        print(result['status'], path, flush=True)
        passed &= result['status'] in ('head_observation_ready', 'completed')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
