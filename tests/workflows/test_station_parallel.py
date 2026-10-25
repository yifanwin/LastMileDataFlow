"""Actual spawned processes with a synthetic episode executor; no GPU acceptance."""
from itertools import count
from unittest.mock import patch

from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.workflows.collection import _station_trials, _station_worker, _trial_record
from lastmile_dataflow.utils.io import rows
from tests.helpers import DatasetTestCase


def fixture_worker(worker, gpu, egl_device, requests, results, stop, scene, *args):
    def execute(baseline, task, station, *unused, **options):
        if scene.get('shared_fault'):
            if station['station_id'] != 's0':
                stop.wait(5.)
            return {'status': 'infrastructure_error', 'reason': 'synthetic CUDA error',
                    'failure_scope': 'shared', 'rollout_id': options['attempt_id']}
        return {'status': 'planning_no_solution', 'reason': 'synthetic no solution',
                'rollout_id': options['attempt_id'], 'executed_steps': 0}

    with (patch('lastmile_dataflow.workflows.collection.run_curobo_attempt', side_effect=execute),
          patch('lastmile_dataflow.workflows.collection.grasp_candidates', return_value=[])):
        _station_worker(worker, gpu, egl_device, requests, results, stop, scene, *args)


def crashed_worker(*args):
    raise SystemExit(7)


def error_and_inflight_worker(worker, gpu, egl_device, requests, results, stop, scene,
                              assets_dir, config, collection, root, reuse_planners):
    index, ids = requests.get()
    if worker == 0:
        results.put(('error', worker, 'synthetic startup error'))
        stop.set()
    else:
        stop.wait(5.)
        record = _trial_record({'status': 'planning_no_solution', 'reason': 'inflight result',
                                'rollout_id': f'r{ids[0]:06d}'}, scene, scene['stations'][index],
                               0, config.seed + index * config.trials_per_station)
        record['station_index'] = index
        results.put(('trial', worker, record))
        results.put(('done', worker, None))


class StationParallelTests(DatasetTestCase):
    def frozen_scene(self):
        snapshot = self.root / 'snapshot'
        self.sim.freeze(snapshot)
        return {'scene_id': 'one', 'task': self.task, 'scene_path': str(snapshot),
                'robot_config': self.robot, 'stations': self.points}

    def test_spawn_preserves_global_seed_ids_and_partial_budget_across_scenes(self):
        scene = self.frozen_scene()
        config = ExecutionConfig(seed=17, trials_per_station=3)
        ids = count(10)
        for number in range(2):
            root = self.root / str(number)
            root.mkdir()
            with patch('lastmile_dataflow.workflows.collection._station_worker', fixture_worker):
                records = _station_trials(self.sim, scene, self.root, config, self.collection, root,
                    budget=5, attempt_ids=ids, workers=2, gpu_ids=[0, 1], egl_devices=[1, 0])
            self.assertEqual(len(records), 5)
            self.assertEqual(rows(root / 'trials.jsonl'), records)
            records.sort(key=lambda row: row['seed'])
            self.assertEqual([r['seed'] for r in records], list(range(17, 22)))
            self.assertEqual([r['station_index'] for r in records], [0, 0, 0, 1, 1])
            self.assertEqual([r['trial'] for r in records], [0, 1, 2, 0, 1])
            self.assertEqual([r['rollout_id'] for r in records], [f'r{i:06d}' for i in range(10+5*number, 15+5*number)])

    def test_shared_fault_keeps_observed_records_and_stops_other_trials(self):
        scene = {**self.frozen_scene(), 'shared_fault': True}
        with patch('lastmile_dataflow.workflows.collection._station_worker', fixture_worker):
            records = _station_trials(self.sim, scene, self.root, ExecutionConfig(trials_per_station=5),
                self.collection, self.root, attempt_ids=count(), workers=2, gpu_ids=[0], egl_devices=[0])
        self.assertTrue(records)
        self.assertTrue(all(row['status'] == 'infrastructure_error' for row in records))
        self.assertTrue(all(row['trial'] == 0 for row in records))
        self.assertEqual(rows(self.root / 'trials.jsonl'), records)

    def test_worker_exit_is_an_error_without_fabricated_trial(self):
        with patch('lastmile_dataflow.workflows.collection._station_worker', crashed_worker):
            with self.assertRaisesRegex(RuntimeError, 'exited with code 7'):
                _station_trials(self.sim, self.frozen_scene(), self.root, ExecutionConfig(), self.collection,
                    self.root, attempt_ids=count(), workers=2, gpu_ids=[0], egl_devices=[0])
        self.assertFalse((self.root / 'trials.jsonl').exists())

    def test_worker_error_drains_inflight_records_before_reporting_failure(self):
        with patch('lastmile_dataflow.workflows.collection._station_worker', error_and_inflight_worker):
            with self.assertRaisesRegex(RuntimeError, 'synthetic startup error'):
                _station_trials(self.sim, self.frozen_scene(), self.root, ExecutionConfig(), self.collection,
                    self.root, attempt_ids=count(), workers=2, gpu_ids=[0], egl_devices=[0])
        records = rows(self.root / 'trials.jsonl')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['rollout_id'], 'r000005')
        self.assertEqual(records[0]['reason'], 'inflight result')
