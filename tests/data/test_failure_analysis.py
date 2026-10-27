"""Failure analysis stays read-only and separates planning, execution and unknown."""
import json
import tempfile
import unittest
from pathlib import Path

from lastmile_dataflow.data.evaluation.failure_analysis import analyze_runs


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def scene(scene_id, house, category='Cup', operation='pick', stations=('S0', 'S1')):
    return {'scene_id': scene_id, 'task_id': f'{operation}-{scene_id}', 'house': house,
            'status': 'accepted', 'proposal_intent': 'no_edit', 'scene': 'scene-files/x/baseline',
            'task': {'task_id': f'{operation}-{scene_id}', 'category': category,
                     'operation': operation, 'instance_id': scene_id, 'asset_id': category},
            'stations': [{'station_id': station} for station in stations]}


def trial(scene, station, index, status, reason=None, *, steps=0, planning_ref=None):
    outcome = {'status': 'success' if status == 'success' else
               'unknown' if status == 'planning_no_solution' else 'failure',
               'reason': reason or status}
    return {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id'],
            'station_id': station, 'trial': index, 'seed': 42 + index,
            'rollout_id': f"r-{scene['scene_id']}-{station}-{index}",
            'status': status, 'reason': reason or status, 'executed_steps': steps,
            'recording_complete': True, 'failure_scope': None,
            'task_outcome': outcome, 'execution_termination': {'status': 'completed', 'phase': 'hold'},
            'planning_ref': planning_ref}


def planning_entry(trace, component='ik', *, batch_seeds=(42, 43, 44), index=None):
    return {'phase': 'native_batch_grasp', 'batch_slot': 0, 'batch_seeds': list(batch_seeds),
            'seed': 42, 'side': 'right', 'index': index,
            'phase_evidence': [{'phase': 'goalset', 'query_index': 1, 'input_eligible': True,
                                'status': 'failure', 'failure_component': component,
                                'ik_found': component != 'ik', 'trajopt_found': False,
                                'solver_calls': [1]}],
            'queries': [{'query_index': 1, 'goal_count': 4}],
            'solver_trace_ref': str(trace)}


def ik_trace(*, position, rotation, feasible):
    return [{'solver': 'ik', 'phase': 'goalset', 'query_index': 1, 'solve_index': 1,
             'success': [[False, False]], 'position_error': [[position, position]],
             'rotation_error': [[rotation, rotation]], 'feasible': [[feasible, feasible]]}]


class FailureAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dataset = self.root / 'dataset'
        self.run = self.root / 'run'
        self.run.mkdir()
        write_rows(self.dataset / 'scenes.jsonl', [scene('a', 0)])
        self.trace = self.run / 'planning/q/solver_trace.json'
        self.trace.parent.mkdir(parents=True)
        self.trace.write_text(json.dumps(ik_trace(position=0.1, rotation=0.2, feasible=False)))
        self.planning = self.run / 'rollouts/r-plan/planning.json'
        self.planning.parent.mkdir(parents=True)
        self.planning.write_text(json.dumps(planning_entry(self.trace)))
        rows = [
            trial(scene('a', 0), 'S0', 0, 'planning_no_solution', planning_ref='rollouts/r-plan/planning.json'),
            trial(scene('a', 0), 'S0', 1, 'planning_no_solution', planning_ref='rollouts/r-plan/planning.json'),
            trial(scene('a', 0), 'S0', 2, 'planning_no_solution', planning_ref='rollouts/r-plan/planning.json'),
            trial(scene('a', 0), 'S1', 0, 'success', steps=30),
            trial(scene('a', 0), 'S1', 1, 'success', steps=30),
            trial(scene('a', 0), 'S1', 2, 'failure', 'lift_contact_hold_or_slip', steps=20),
        ]
        write_rows(self.run / 'trials.jsonl', rows)
        (self.run / 'config.json').write_text(json.dumps({'dataset': str(self.dataset)}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_eligible_scene_planning_and_execution_rows(self):
        out = self.root / 'analysis'
        result = analyze_runs([self.run], out, min_trials=100, min_houses=10)
        self.assertEqual(result['counts']['trials'], 6)
        self.assertEqual(result['task_classes']['valid_scene'], 1)
        labels = {row['row']: row['合计'] for row in result['main_table']}
        self.assertEqual(labels['success'], 2)
        self.assertEqual(labels['IK (goalset) 目标位姿不可达'], 3)
        self.assertEqual(labels['lift_contact_hold_or_slip'], 1)
        planning = [json.loads(line) for line in (out / 'planning_failures.jsonl').read_text().splitlines()]
        self.assertEqual(len(planning), 3)
        self.assertTrue(planning[0]['ik_unreachable'])
        execution = [json.loads(line) for line in (out / 'execution_failures.jsonl').read_text().splitlines()]
        self.assertEqual(execution[0]['reason'], 'lift_contact_hold_or_slip')
        self.assertEqual({row['kind'] for row in result['replay_manifest']},
                         {'failure', 'success', 'S1'})
        self.assertFalse([row for row in result['replay_manifest'] if row['kind'] == 'S0'])

    def test_constraint_and_trajopt_are_distinct(self):
        self.trace.write_text(json.dumps(ik_trace(position=0.0, rotation=0.0, feasible=False)))
        out = self.root / 'analysis-constraint'
        result = analyze_runs([self.run], out, min_trials=100, min_houses=10)
        labels = {row['row'] for row in result['main_table']}
        self.assertIn('IK (goalset) 位姿收敛但约束不可行', labels)

        self.planning.write_text(json.dumps(planning_entry(self.trace, component='trajopt')))
        self.trace.write_text(json.dumps(ik_trace(position=0.0, rotation=0.0, feasible=True)))
        out = self.root / 'analysis-trajopt'
        result = analyze_runs([self.run], out, min_trials=100, min_houses=10)
        labels = {row['row'] for row in result['main_table']}
        self.assertIn('TrajOpt (goalset) IK 有解、轨迹不可行', labels)

    def test_zero_success_category_is_excluded_for_later_batches(self):
        write_rows(self.dataset / 'scenes.jsonl', [
            scene('a', 0), scene('bad', 1, category='Pillow', stations=('S0', 'S1', 'S2', 'S3', 'S4'))])
        rows = [trial(scene('a', 0), 'S0', i, 'planning_no_solution', planning_ref='rollouts/r-plan/planning.json')
                for i in range(3)]
        rows += [trial(scene('a', 0), 'S1', i, 'success') for i in range(3)]
        for station in ('S0', 'S1', 'S2', 'S3', 'S4'):
            rows += [trial(scene('bad', 1, category='Pillow', stations=('S0', 'S1', 'S2', 'S3', 'S4')),
                           station, i, 'failure', 'tracking_timeout') for i in range(3)]
        write_rows(self.run / 'trials.jsonl', rows)
        result = analyze_runs([self.run], self.root / 'analysis-exclude', min_trials=1, min_houses=1)
        self.assertEqual(result['exclude_categories'], ['Pillow'])
        self.assertIn('Pillow', [row['category'] for row in result['target_categories']])


if __name__ == '__main__':
    unittest.main()
