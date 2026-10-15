"""轻量协议回归。真实 stepping 使用简化模型，任务/策略结果的 mock 不代表真实成功。"""
from dataclasses import asdict, replace
from itertools import count
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from helpers import make_sim
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.io import create_run, append_row, read_json, rows
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.data.evaluation.selection import station_calibration, recovery_choices
from lastmile_dataflow.scenes.dataset import load_scenes
from lastmile_dataflow.workflows.collection import evaluate_stations, collect_lastmile, _recovery_trials
from lastmile_dataflow.data.export.molmobot_dataset import export_molmobot
from lastmile_dataflow.scenes.augmentation.config import CaseConstruction


class LightDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim, self.robot = make_sim(self.root)
        self.collection = CollectionConfig(record_video=False, record_images=False)
        self.task = {'task_id': 'pick-target', 'target_body': 'target', 'instance_id': 'target',
                     'operation': 'pick', 'asset_id': 'fixture', 'category': 'Cup',
                     'anchor_world': [2., 0., .15], 'instruction': 'pick the target'}
        initial = self.robot.initial
        self.points = [{'station_id': 's0', 'geometry': 'valid', 'xy': [0., 0.],
                        'base': [0., 0., 0.], 'initial': initial},
                       {'station_id': 's1', 'geometry': 'valid', 'xy': [.4, 0.],
                        'base': [.4, 0., 0.], 'initial': {**initial, 'base': [.4, 0., 0.]}}]
        self.scene = {'scene_id': 'one', 'task': self.task, 'stations': self.points,
                      'scene': 'scene-files/one', 'status': 'accepted'}

    def tearDown(self):
        self.sim.close()
        self.tmp.cleanup()

    def dataset(self):
        dataset = create_run(self.root / 'dataset', {'geometry': {}, 'seed': 3})
        self.sim.freeze(dataset / self.scene['scene'])
        append_row(dataset / 'scenes.jsonl', self.scene)
        return dataset

    def test_scene_roundtrip_and_xml_payload(self):
        dataset = self.dataset()
        record = load_scenes(dataset)[0]
        restored = Simulation.from_snapshot(record['scene_path'], self.robot, target='target')
        try:
            np.testing.assert_array_equal(restored.state_vector(), self.sim.state_vector())
            model = mujoco.MjModel.from_xml_path(str(Path(record['scene_path']) / 'model.xml'))
            self.assertEqual(model.nq, self.sim.model.nq)
            self.assertEqual({p.name for p in Path(record['scene_path']).iterdir()},
                             {'config.json', 'model.xml', 'model.mjb', 'initial.npz'})
        finally:
            restored.close()

    def test_single_live_trajectory_and_native_state_layout(self):
        recorder = AttemptRecorder(self.root, {'collection': asdict(self.collection), 'task': self.task, 'seed': 3}, attempt_id='r0')
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        for _ in range(2):
            self.sim.step(self.sim.robot.neutral_action())
        recorder.finish('failure', 'fixture')
        with np.load(recorder.path / 'trajectory.npz') as data:
            self.assertEqual(data['qpos'].shape[0], 3)
            self.assertEqual(data['actions'].shape, (2, 20))
            self.assertEqual(data['state22'].shape, (3, 22))
            np.testing.assert_allclose(np.diff(data['time_s']), [.048, .052])
        self.assertTrue(recorder.result['recording_complete'])
        self.assertEqual({p.name for p in recorder.path.iterdir()}, {'trajectory.npz', 'recording.json'})
        with self.assertRaises(RuntimeError):
            self.sim.restore_snapshot(self.root / 'initial.npz')

    def test_invalid_actions_do_not_create_executed_rows(self):
        recorder = AttemptRecorder(self.root, {'collection': asdict(self.collection), 'task': self.task}, attempt_id='r0')
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        with self.assertRaises(ValueError):
            self.sim.step([0.] * 19)
        recorder.finish('failure', 'invalid_action')
        with np.load(recorder.path / 'trajectory.npz') as data:
            self.assertEqual(data['actions'].shape, (0, 20))
            self.assertEqual(len(data['time_s']), 1)

    def test_rates_exclude_errors_and_require_complete_points(self):
        config = ExecutionConfig(trials_per_station=2, start_count=1)
        trials = [{'station_id': s, 'trial': i, 'status': 'failure' if s == 's0' else 'success'}
                  for s in ('s0', 's1') for i in range(2)]
        binding = {'scene_id': 'one', 'task_id': 'pick-target'}
        rates = station_calibration(self.points, trials, 2, binding, config)
        self.assertEqual(len(recovery_choices(self.points, rates, binding, config)), 1)
        incomplete = station_calibration(self.points, trials[:-1] + [{'station_id': 's1', 'trial': 1, 'status': 'simulation_failure'}], 2, binding, config)
        self.assertFalse(incomplete['complete'])
        self.assertEqual(recovery_choices(self.points, incomplete, binding, config), [])

    def test_scene_collector_writes_direct_records_no_child_run(self):
        dataset = self.dataset()
        def execute(baseline, scene, station, *args, **kwargs):
            return {'status': 'success', 'reason': 'mocked', 'recording_complete': True,
                    'rollout_id': 'mock-' + station['station_id'], 'executed_steps': 1}
        with patch('lastmile_dataflow.workflows.collection._execute', side_effect=execute), \
             patch('lastmile_dataflow.workflows.collection.export_heatmap'), \
             patch('lastmile_dataflow.workflows.collection._recovery_trials') as recovery:
            output = evaluate_stations(dataset, self.root, ExecutionConfig(trials_per_station=1),
                self.collection, self.root, run_id='run', gpu_ids=[0])
        recovery.assert_not_called()
        self.assertEqual(len(rows(output / 'trials.jsonl')), 2)
        self.assertEqual({p.name for p in output.iterdir()}, {'config.json', 'trials.jsonl', 'summary.json'})
        self.assertTrue(read_json(output / 'summary.json')['scenes'][0]['calibration_complete'])

    def test_reused_calibration_does_not_repeat_trials(self):
        dataset = self.dataset()
        config = ExecutionConfig(trials_per_station=1)
        def execute(baseline, scene, station, *args, **kwargs):
            return {'status': 'failure' if station['station_id'] == 's0' else 'success',
                    'reason': 'mocked', 'rollout_id': station['station_id'], 'recording_complete': True}
        with patch('lastmile_dataflow.workflows.collection._execute', side_effect=execute), \
             patch('lastmile_dataflow.workflows.collection.export_heatmap'):
            calibration = evaluate_stations(dataset, self.root, config, self.collection, self.root,
                run_id='calibration', gpu_ids=[0])
        with patch('lastmile_dataflow.workflows.collection._station_trials') as station_trials, \
             patch('lastmile_dataflow.workflows.collection._recovery_trials', return_value=[]), \
             patch('lastmile_dataflow.workflows.collection.export_heatmap'):
            output = collect_lastmile(dataset, self.root, config, self.collection, self.root,
                run_id='recovery', gpu_ids=[0], calibration=calibration)
        station_trials.assert_not_called()
        self.assertFalse((output / 'trials.jsonl').exists())
        with self.assertRaisesRegex(ValueError, 'calibration'):
            collect_lastmile(dataset, self.root, replace(config, attempt_timeout_s=9.), self.collection,
                self.root, run_id='mismatched', gpu_ids=[0], calibration=calibration)

    def test_continuous_run_calibrates_once_in_the_same_directory(self):
        dataset = self.dataset()
        config = ExecutionConfig(trials_per_station=1)
        with patch('lastmile_dataflow.workflows.collection._execute', return_value={
                'status': 'failure', 'reason': 'mocked', 'recording_complete': True}) as execute, \
             patch('lastmile_dataflow.workflows.collection._recovery_trials', return_value=[]) as recovery, \
             patch('lastmile_dataflow.workflows.collection.export_heatmap'):
            output = collect_lastmile(dataset, self.root, config, self.collection, self.root,
                run_id='continuous', gpu_ids=[0])
        self.assertEqual(execute.call_count, len(self.points))
        recovery.assert_called_once()
        self.assertEqual(recovery.call_args.args[-1], output)
        self.assertEqual(len(rows(output / 'trials.jsonl')), len(self.points))
        self.assertEqual({p.name for p in output.iterdir()}, {'config.json', 'trials.jsonl', 'summary.json'})

    def test_incomplete_calibration_never_executes_recovery(self):
        dataset = self.dataset()
        config = ExecutionConfig(trials_per_station=2)
        with patch('lastmile_dataflow.workflows.collection._execute', return_value={
                'status': 'failure', 'reason': 'mocked', 'recording_complete': True}), \
             patch('lastmile_dataflow.workflows.collection.export_heatmap'):
            calibration = evaluate_stations(dataset, self.root, config, self.collection, self.root,
                run_id='partial', gpu_ids=[0], max_trials=1)
        with patch('lastmile_dataflow.workflows.collection._station_trials') as trials, \
             patch('lastmile_dataflow.workflows.collection._execute') as execute, \
             patch('lastmile_dataflow.workflows.collection.export_heatmap'):
            output = collect_lastmile(dataset, self.root, config, self.collection, self.root,
                run_id='no-recovery', gpu_ids=[0], calibration=calibration)
        trials.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(rows(output / 'episodes.jsonl'), [])
        self.assertFalse(read_json(output / 'summary.json')['scenes'][0]['calibration_complete'])

    def test_no_path_is_a_negative_record_without_rollout(self):
        config = ExecutionConfig(trials_per_station=1, start_count=1)
        trials = [{'station_id': 's0', 'trial': 0, 'status': 'failure'},
                  {'station_id': 's1', 'trial': 0, 'status': 'success'}]
        binding = {'scene_id': 'one', 'task_id': 'pick-target'}
        rates = station_calibration(self.points, trials, 1, binding, config)
        choices = recovery_choices(self.points, rates, binding, config)
        with patch('lastmile_dataflow.workflows.collection.search', return_value=None), \
             patch('lastmile_dataflow.workflows.collection._execute') as execute:
            records = _recovery_trials(self.sim, self.scene, choices, self.root, config, self.collection, self.root, attempt_ids=count())
        execute.assert_not_called()
        self.assertEqual(records[0]['status'], 'path_unavailable')
        self.assertIsNone(records[0]['rollout_id'])
        self.assertEqual(rows(self.root / 'episodes.jsonl'), records)

    def test_model_export_keeps_source_and_requires_motor_calibration(self):
        recorder = AttemptRecorder(self.root, {'collection': asdict(self.collection), 'task': self.task}, attempt_id='r0')
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        self.sim.step(self.sim.robot.neutral_action())
        recorder.finish('failure', 'fixture')
        with self.assertRaisesRegex(ValueError, 'motor map'):
            export_molmobot(recorder.path, self.root / 'bad')
        output = export_molmobot(recorder.path, self.root / 'state', state_images_only=True)
        with np.load(output / 'data.npz') as data:
            self.assertNotIn('actions', data.files)
            self.assertEqual(data['state'].shape, (2, 22))
        self.assertTrue((recorder.path / 'trajectory.npz').is_file())

    def test_capacity_shortfall_is_not_a_recipe_exception(self):
        with self.assertRaises(TypeError):
            CaseConstruction(allow_surface_capacity_shortfall=True)


if __name__ == '__main__':
    unittest.main()
