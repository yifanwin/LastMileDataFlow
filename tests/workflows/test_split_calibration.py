"""Real spawn and MuJoCo stepping, synthetic planning; no Panda/GPU acceptance."""
from dataclasses import asdict
from functools import partial
from itertools import count
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.execution.context import clone_sim, ContinuousContext
from lastmile_dataflow.runtime.execution.lifecycle import isolated_episode
from lastmile_dataflow.runtime.execution.protocol import PlanningResponse
from lastmile_dataflow.runtime.execution.workers import execution_worker
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.resources import RunResources
from lastmile_dataflow.utils.io import read_json, rows
from lastmile_dataflow.workflows.station_calibration import calibrate_stations
from tests.helpers import DatasetTestCase


class FixtureService:
    def execute(self, request):
        if request.inputs['task'].get('shared_fault'):
            raise MemoryError('synthetic shared planning fault')
        time.sleep(.01)
        responses = []
        for identity, budget in zip(request.identities, request.budgets):
            no_solution = request.inputs['task'].get('no_solution', False)
            if request.stage == 'preclose':
                plan = {'success': not no_solution, 'index': None if no_solution else 0,
                        'stages': {}, 'diagnostics': {'batch_slot': identity.trial}}
                count = budget.plan_count + 3
            else:
                plan = {'result': {'status': 'success', 'joint_names': ('a',),
                                   'positions': ((0.,),), 'diagnostics': {}}}
                count = budget.plan_count + 1
            responses.append(PlanningResponse(request.query_id, identity,
                'planning_no_solution' if no_solution else 'success', .01, count, plan,
                queue_s=time.monotonic() - request.enqueued_at - .01))
        return responses

    def close(self):
        pass


@isolated_episode
def fixture_runner(baseline, task, station, candidates, assets_dir, config, collection, *,
                   root, attempt_id, seed, prepared, planning_client):
    sim = clone_sim(baseline, target=task['target_body'])
    prepared.state.initialize(sim)
    recorder = AttemptRecorder(root, {'collection': asdict(collection)}, attempt_id=attempt_id)
    recorder.bind_simulation(sim)
    ctx = ContinuousContext(sim, recorder, config, budget=planning_client.budget)
    planning_client.bind(ctx)
    ctx.tick(sim.robot.neutral_action())
    before = sim.state_vector().copy()
    planning_client.query(SimpleNamespace(sim=sim, side='right', single_phase='approach_contact',
                                         attached=False, names=('a',)), np.eye(4))
    np.testing.assert_array_equal(sim.state_vector(), before)
    for _ in range(2):
        ctx.tick(sim.robot.neutral_action())
    recorder.finish('failure', 'synthetic_fixture')
    ctx.finish()
    result = {**recorder.result, 'rollout_id': attempt_id, 'task_outcome': {'status': 'failure'},
              'budget': asdict(ctx.budget)}
    from lastmile_dataflow.utils.io import write_json
    write_json(recorder.path / 'fixture-result.json', result)
    return result


class SplitCalibrationTests(DatasetTestCase):
    def scenes(self, **task_fields):
        snapshot = self.root / 'snapshot'
        self.sim.freeze(snapshot)
        return [{**self.scene, 'scene_id': identity, 'scene_path': str(snapshot),
                 'robot_config': self.robot, 'task': {**self.task, **task_fields}}
                for identity in ('one', 'two')]

    def run_calibration(self, scenes, output, *, cpus=3):
        config = ExecutionConfig(shared_station_preparation=True, initial_settle_s=.1, trials_per_station=3)
        resources = RunResources((0,), cpu_workers=cpus, preparation_workers=2)
        output.mkdir()
        with patch('lastmile_dataflow.workflows.station_calibration.grasp_candidates', return_value=[]):
            return calibrate_stations(scenes, self.root, config, self.collection, output, resources,
                attempt_ids=count(), service_type=FixtureService,
                trial_worker=partial(execution_worker, runner=fixture_runner))

    def test_one_planner_supplies_multiple_independent_cpu_trials_across_scenes(self):
        output = self.root / 'run'
        records, summary = self.run_calibration(self.scenes(), output)
        self.assertEqual(len(records), 12)
        self.assertEqual(rows(output / 'trials.jsonl'), records)
        self.assertIsNone(summary['failure'])
        self.assertEqual(summary['peaks']['cpu_trials'], 3)
        self.assertEqual(summary['peaks']['gpu_queries'], 1)
        self.assertEqual({r['rollout_id'] for r in records}, {f'r{i:06d}' for i in range(12)})
        for scene in ('one', 'two'):
            observed = sorted((r for r in records if r['scene_id'] == scene), key=lambda r: r['seed'])
            self.assertEqual([r['seed'] for r in observed], list(range(42, 48)))
        for record in records:
            self.assertEqual(record['executed_steps'], 3)
            path = output / 'rollouts' / record['rollout_id']
            evidence = read_json(path / 'fixture-result.json')
            self.assertEqual(evidence['budget']['plan_count'], 4)
            self.assertAlmostEqual(evidence['budget']['planning_s'], .02)
            self.assertGreater(evidence['budget']['execution_s'], 0.)
            with np.load(path / 'trajectory.npz') as trajectory:
                self.assertEqual(len(trajectory['time_s']), 4)
                np.testing.assert_allclose(trajectory['time_s'], [.1, .148, .2, .248])

    def test_no_solution_records_initial_state_without_entering_physical_pool(self):
        output = self.root / 'no-solution'
        records, summary = self.run_calibration(self.scenes(no_solution=True)[:1], output)
        self.assertEqual(len(records), 6)
        self.assertEqual(summary['peaks']['cpu_trials'], 0)
        self.assertTrue(all(r['status'] == 'planning_no_solution' and r['executed_steps'] == 0 for r in records))
        for record in records:
            with np.load(output / 'rollouts' / record['rollout_id'] / 'trajectory.npz') as trajectory:
                self.assertEqual(len(trajectory['time_s']), 1)
                self.assertEqual(len(trajectory['actions']), 0)

    def test_shared_fault_retains_unknown_results_and_stops_new_physics(self):
        output = self.root / 'shared-fault'
        records, summary = self.run_calibration(self.scenes(shared_fault=True), output)
        self.assertTrue(records)
        self.assertEqual(summary['failure']['failure_scope'], 'shared')
        self.assertEqual(summary['peaks']['cpu_trials'], 0)
        self.assertTrue(all(r['status'] == 'infrastructure_error' for r in records))
        self.assertTrue(all(r['task_outcome']['status'] == 'unknown' for r in records))
