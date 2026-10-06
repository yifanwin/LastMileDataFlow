"""One-off acceptance verification; not an online approval/audit step."""
import argparse
from pathlib import Path
import imageio.v2 as imageio
import numpy as np
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.construction.case_schema import RunResult, Review, required_checks_pass
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.runtime.simulation import Simulation


def verify_run(path, restore=False):
    path = Path(path)
    result = RunResult.from_dict(read_json(path/'result.json'))
    assert result.status == 'completed' and result.accepted_count >= 3
    calls = [read_json(f) for f in (path/'agents').glob('*.json')]
    assert {'normalizer', 'proposer', 'reviewer'} <= {c['role'] for c in calls if c['status'] == 'parsed'}
    assert len([c for c in calls if c['role'] == 'normalizer' and c['status'] == 'parsed']) == 1
    configuration = read_json(path/'configuration.json')
    assert configuration['backend'] == 'http', 'mock acceptance cannot be real-model evidence'
    robot = RobotConfig(**configuration['robot'])
    identities = set()
    for sample_id in result.accepted_samples:
        root = path/'samples'/sample_id
        sample = read_json(root/'sample.json')
        assert sample['status'] == 'accepted' and required_checks_pass(sample['checks'])
        assert Review.from_dict(sample['review']).verdict == 'pass'
        assert sample['layout_id'] not in identities
        identities.add(sample['layout_id'])
        pairs = [read_json(f) for f in root.glob('rgb*/pair.json')]
        packet = next(p for p in pairs if p['pair_id'] == sample['view_pair_id'])
        assert packet['sample_id'] == sample_id and len(packet['images']) == 6
        assert {i['view'] for i in packet['images']} == {s+'/'+v for s in ('before', 'after') for v in ('top', 'oblique_a', 'oblique_b')}
        for im in packet['images']:
            pixels = imageio.imread(im['path'])
            assert pixels.shape == (packet['rig']['height'], packet['rig']['width'], 3)
        assert (root/'scene/model.mjb').is_file()
        if restore:
            sim = Simulation.from_snapshot(root/'scene', robot)
            try:
                graph = read_json(root/'graph_after.json')
                for node in graph['nodes'].values():
                    if node.get('mjcf_body') and node.get('pose'):
                        b = sim.model.body(node['mjcf_body']).id
                        np.testing.assert_allclose(sim.data.xpos[b], node['pose']['position'], atol=1e-9, rtol=0)
                        q = np.array(node['pose']['quaternion_wxyz'])
                        assert min(np.linalg.norm(sim.data.xquat[b]-q), np.linalg.norm(sim.data.xquat[b]+q)) < 1e-8
            finally:
                sim.close()
    return {'run_id': result.run_id, 'accepted': result.accepted_count, 'attempted': result.attempted_count,
            'rgb_count': 6*result.accepted_count, 'independent_restore': restore, 'verified': True}


def expected_failure(run):
    path = Path(run['output'])
    result = read_json(path/'result.json')
    if result['accepted_count'] or result['status'] in ('completed', 'partial', 'infrastructure_error'):
        return False
    if run['expected_failure'] == 'budget':
        return result['status'] == 'budget_exhausted' and 'budget' in result['reason']
    samples = [read_json(p) for p in (path/'samples').glob('*/sample.json')]
    feedback = read_json(path/'progress.json').get('last_feedback', [])
    if run['expected_failure'] == 'missing_role':
        return any(f['status'] == 'no_proposals' for f in feedback) or any(s['status'] == 'binding_not_applicable' for s in samples)
    return any(s['status'] == 'search_exhausted' for s in samples) or any(f['status'] == 'no_proposals' for f in feedback)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--reports', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--restore', action='store_true')
    args = p.parse_args()
    groups, failures, attempts = {}, {}, []
    for manifest in args.reports:
        for run in read_json(manifest)['runs']:
            result = read_json(Path(run['output'])/'result.json')
            if 'case' in run:
                attempts.append({'house': run['house'], 'case': run['case'], 'run_id': result['run_id'],
                                 'status': result['status'], 'accepted': result['accepted_count']})
                if result['status'] == 'completed' and result['accepted_count'] >= 3:
                    groups[(run['house'], run['case'])] = run
            elif 'expected_failure' in run:
                failures[run['expected_failure']] = {'matched': expected_failure(run), 'run_id': result['run_id']}
    houses, cases = {h for h, c in groups}, {c for h, c in groups}
    verified = []
    for (house, case), run in sorted(groups.items()):
        source = read_json(Path(run['output'])/'request.json')['scene_source']
        assert source['dataset'] == 'procthor-10k-train' and source['scene_id'] == f'procthor-10k-train/train_{house}'
        verified.append({'house': house, 'case': case, **verify_run(run['output'], args.restore)})
    gate = len(houses) >= 2 and len(cases) >= 2 and len(groups) == len(houses)*len(cases) and (
        {'missing_role', 'no_candidates', 'budget'} <= set(failures) and all(f['matched'] for f in failures.values()))
    write_json(args.output, {'gate_passed': gate, 'scope': 'real_scene_editing_not_robot_task',
                            'groups': verified, 'expected_failures': failures, 'attempts': attempts})
    print(args.output, 'gate_passed='+str(gate))
    return 0 if gate else 1


if __name__ == '__main__':
    raise SystemExit(main())
