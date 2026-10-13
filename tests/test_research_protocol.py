"""Research contract regressions: real toy MuJoCo stepping, MOCK RGB, not GPU acceptance."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from helpers import make_sim
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.io import digest, read_json, write_json
from lastmile_dataflow.recording.protocol import rows
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.validation.canonical import episode_record
from lastmile_dataflow.workflows.lastmile_datasets import collect_views, generate_summary, summarize_episodes
from lastmile_dataflow.construction.scene_dataset_config import SceneDatasetRecipe
from lastmile_dataflow.workflows.scene_datasets import _save_sample, build_scene_dataset


class ResearchEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sim, self.robot = make_sim(self.root); self.addCleanup(self.sim.close)
        self.child = self.root / 'child'
        self.scene = self.child / 'scenes/val_1'; self.scene.mkdir(parents=True)
        self.sim.freeze(self.scene / 'scene')
        self.binding = {'scene_dataset_id': 'dataset', 'sample_id': 'sample',
                        'robot_sensor_config_id': 'rs-test', 'task_id': 't', 'package_identity': 'source-test'}
        self.unit = {**{k: self.binding[k] for k in ('scene_dataset_id', 'sample_id', 'robot_sensor_config_id', 'task_id')},
            'source_package_identity': 'source-test', 'compiled_model_sha256': self.sim.model_hash,
            'run_path': str(self.child), 'house': 1, 'unit_id': 'u', 'selected_points': 2}
        self.parent = self.root / 'research'; self.parent.mkdir()
        self.start = {'station_id': 'low', 'xy': [0, 0], 'initial': {'base': [0, 0, 0]}}
        self.goal = {'station_id': 'high', 'xy': [.1, 0], 'initial': {'base': [.1, 0, 0]}}
        task = self.scene / 'tasks/t'
        write_json(task / 'task.json', {'task_id': 't', 'operation': 'pick'})
        write_json(task / 'result.json', {'status': 'retained', 'dataset_eligible': True})
        write_json(task / 'selection.json', {'start_groups': [{'s0': 'low'}]})
        write_json(self.parent / 'manifest.json', {'status': 'complete', 'stage': 'rollout'})
        write_json(self.parent / 'plan.json', {'units': [self.unit], 'scheduled_calibration_trials': 10,
                                             'diagnostic_subset': False})

    def sensors(self, width, height, *, depth):
        return {name: {'rgb': np.zeros((height, width, 3), np.uint8), 'depth': None,
            'calibration': {'resolution': [width, height], 'near_m': .01, 'far_m': 20.,
                'intrinsics': {'K': [[20, 0, width/2], [0, 20, height/2], [0, 0, 1]]},
                'T_world_camera_optical': np.eye(4).tolist()}}
                for name in self.sim.robot.camera_names}

    def execute(self, *, bad_sensor=False, trace=False):
        config = {'robot': asdict(self.robot),
            'collection': asdict(CollectionConfig(record_video=False, record_depth=False,
                width=32, height=24, record_physics_trace=trace)),
            'task': {'task_id': 't', 'operation': 'pick', 'instruction': 'Synthetic test only'},
            'station': self.start, 'goal': self.goal, 'path': {'xy': [[0, 0], [.1, 0]]},
            'seed': 1, 'protocol': {'lastmile_selection': True}, 'scene_dataset_binding': self.binding}
        recorder = AttemptRecorder(self.scene, config, attempt_id='rollout-low-high-abc123',
                                   attempt_group=('pick', 'low'))
        self.addCleanup(recorder.abort_close)
        recorder.bind_simulation(self.sim)
        with patch.object(self.sim, 'capture_sensors', side_effect=RuntimeError('MOCK missing camera') if bad_sensor else self.sensors):
            self.sim.begin(); before = self.sim.observe_state()
            action = self.sim.robot.neutral_action()
            command, stopped = self.sim.step(action)
            recorder.record_step(raw_action=action, command=command, before=before,
                after=self.sim.observe_state(), check={'valid': True}, observations={})
        return recorder

    def finish(self, recorder):
        recorder.finish('success', 'synthetic_test_only', {'task_completion': {'status': 'success'}},
            extra={'navigation': {'status': 'success'}, 'continuous_simulation': True,
                   'trajectory_metrics': {'task_success': True, 'simulation_valid': True}})
        return recorder.path

    def test_committed_orphan_rebuilds_unique_index_and_summary(self):
        path = self.finish(self.execute())
        record = episode_record(path)
        self.assertTrue(record['recording_complete'])
        self.assertFalse((path / 'raw/scene/model.mjb').exists())  # Source model is shared, not copied per episode.
        self.assertFalse((path / 'artifacts.json').exists())
        self.assertEqual(rows(path / 'raw/contacts.jsonl'), [])
        view = collect_views(self.parent, [self.unit], export_state_images=False)
        self.assertEqual(view['train_episodes'], 1)
        original = generate_summary(self.parent)
        (self.parent / 'episodes.jsonl').unlink(); (self.parent / 'summary.json').unlink()
        collect_views(self.parent, [self.unit], export_state_images=False)
        self.assertEqual(generate_summary(self.parent), original)
        self.assertEqual(len(rows(self.parent / 'episodes.jsonl')), 1)
        for name in ('train_index.jsonl', 'lastmile_index.jsonl', 'all_attempts_index.jsonl', 'exports.jsonl'):
            self.assertFalse((self.parent / name).exists())

    def test_sensor_gap_keeps_task_success_but_blocks_training_and_trial(self):
        path = self.finish(self.execute(bad_sensor=True))
        record = episode_record(path)
        self.assertTrue(record['task_success'])
        self.assertFalse(record['recording_complete'])
        self.assertEqual(record['termination_status'], 'recording_error')
        self.assertEqual(collect_views(self.parent, [self.unit], export_state_images=False)['train_episodes'], 0)

    def test_interruption_before_commit_is_not_a_completed_episode(self):
        recorder = self.execute()
        def write(path, value):
            if Path(path).name == 'episode.json':
                raise RuntimeError('injected before atomic commit')
            return write_json(path, value)
        with patch('lastmile_dataflow.recording.recorder.write_json', side_effect=write):
            with self.assertRaisesRegex(RuntimeError, 'atomic commit'):
                self.finish(recorder)
        self.assertFalse((recorder.path / 'episode.json').exists())
        view = collect_views(self.parent, [self.unit], export_state_images=False)
        self.assertEqual(view['all_attempts'], 1); self.assertEqual(view['train_episodes'], 0)
        self.assertFalse(rows(self.parent / 'episodes.jsonl')[0]['committed'])

    def test_deleted_observation_is_detected_without_a_checksum_tree(self):
        path = self.finish(self.execute())
        observation = rows(path / 'raw/observations.jsonl')[0]
        (path / observation['path']).unlink()
        with self.assertRaisesRegex(ValueError, 'missing recorded observation'):
            collect_views(self.parent, [self.unit], export_state_images=False)

    def test_trace_is_explicit_opt_in(self):
        path = self.finish(self.execute(trace=True))
        self.assertGreater(len(rows(path / 'raw/contacts.jsonl')), 0)
        self.assertTrue(episode_record(path)['recording_complete'])

    def test_export_failure_does_not_relabel_collection(self):
        path = self.finish(self.execute())
        before = (path / 'episode.json').read_bytes()
        with patch('lastmile_dataflow.integrations.molmobot_dataset.export_molmobot', side_effect=ValueError('unsupported conversion')):
            view = collect_views(self.parent, [self.unit], export_state_images=True)
        self.assertEqual(view['train_episodes'], 1)
        self.assertEqual((path / 'episode.json').read_bytes(), before)
        self.assertEqual(read_json(self.parent / 'manifest.json')['status'], 'complete')
        self.assertEqual(len(list((self.parent / 'exports/molmobot').glob('*.error.json'))), 1)

    def test_duplicate_valid_trial_rejected_errors_and_timeouts_count_correctly(self):
        base = {'role': 'calibration', 'committed': True, 'sample_id': 's', 'robot_sensor_config_id': 'r',
                'trial_id': 'one', 'termination_status': 'success', 'task_success': True,
                'recording_complete': True, 'data_valid': True}
        error = {**base, 'committed': False, 'termination_status': 'infrastructure_error', 'task_success': None}
        timeout = {**base, 'trial_id': 'two', 'termination_status': 'timeout',
                   'termination_reason': 'attempt_wall_clock_budget', 'task_success': None}
        value = summarize_episodes([error, base, timeout])
        self.assertEqual(value['valid_calibration_trials'], 2)
        self.assertEqual(value['observed_calibration_success_rate'], .5)
        self.assertEqual(value['calibration_error_attempts'], 1)
        with self.assertRaisesRegex(ValueError, 'duplicate valid'):
            summarize_episodes([base, base])
        self.assertIsNone(summarize_episodes([])['observed_calibration_success_rate'])


class ResearchSceneTests(unittest.TestCase):
    def test_save_and_build_have_no_package_seals_or_physical_reaudit(self):
        # Accepted station/check inputs are MOCK fixtures; snapshot serialization is real toy MuJoCo.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); sim, robot = make_sim(root)
            self.addCleanup(sim.close)
            snapshot = root / 'source'; sim.freeze(snapshot)
            robot_config = root / 'robot.json'; write_json(robot_config, asdict(robot))
            recipe = SceneDatasetRecipe('fixture-dataset', 'fixture-assets', str(robot_config), [1],
                cases=['Case3'], variants_per_case=1)
            task = {'task_id': 'open-t', 'operation': 'open', 'target_body': 'target',
                    'instance_id': 'target', 'asset_id': 'fixture', 'anchor_world': [0, 0, 1]}
            stations = [{'station_id': f'S{i}', 'xy': [.1*i, .2], 'grid_index': [i, 0],
                'geometry': 'valid', 'independent_simulation': True, 'non_robot_qpos_unchanged': True,
                'new_solver_warnings': [], 'visibility': {'target': 'target', 'visible_pixels': 100},
                'initial': {'base': [.1*i, .2, 0]}} for i in range(5)]
            selected = {'house': 1, 'task': task, 'source': sim.source.provenance(),
                        'prefilter_count': 5, 'input_snapshot': str(snapshot)}
            plan = {'recipe_sha256': digest(asdict(recipe)), 'selected': [selected],
                    'exclusions': [], 'elapsed_s': 0}
            discovery = root / 'discovery.json'; write_json(discovery, plan)
            recipe_path = root / 'recipe.json'; write_json(recipe_path, asdict(recipe))
            out = root / 'output'
            dataset = out / 'scene_datasets/research'
            def launch(*args, **kwargs):
                sample = _save_sample(dataset, recipe, {**selected, 'case': 'Case3', 'construction_unit': 0},
                    task, snapshot, stations, stations, {'valid': True}, root / 'evidence', 1, 0, {})
                write_json(out / 'diagnostics/research-unit-000/result.json',
                    {'status': 'completed', 'samples': [sample], 'exclusions': [], 'elapsed_s': 0})
                return type('FinishedWorker', (), {'poll': lambda self: 0})()
            with patch('lastmile_dataflow.workflows.scene_datasets.load_scene_recipe', return_value=recipe), \
                 patch('lastmile_dataflow.workflows.scene_datasets.subprocess.Popen', side_effect=launch):
                actual = build_scene_dataset(recipe_path, discovery, out, 'research', [0])
                self.assertEqual(read_json(actual / 'manifest.json')['status'], 'complete')
                self.assertFalse((actual / 'checksums.json').exists())
                self.assertFalse((actual / 'packages.jsonl').exists())
                sample = rows(actual / 'samples.jsonl')[0]
                destination = actual / sample['path']
                package = next((destination / 'robot_sensor_configs').iterdir())
                self.assertTrue((package / 'stations.json').is_file())
                self.assertFalse((package / 'checksums.json').exists())
                self.assertFalse((destination / 'checksums.json').exists())
                self.assertFalse((destination / 'construction/control_scene').exists())
                with patch('lastmile_dataflow.workflows.scene_datasets.code_identity', return_value={'code_commit': 'changed'}):
                    self.assertEqual(build_scene_dataset(recipe_path, discovery, out, 'research', [0], resume=True), actual)

    def test_unit_worker_checkpoint_and_resume_keep_committed_samples(self):
        from lastmile_dataflow.workflows.scene_datasets import run_scene_dataset_unit
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); dataset = root / 'scene_datasets/test'
            recipe = SceneDatasetRecipe('dataset', 'assets', 'robot', [1], cases=['Case3'])
            write_json(dataset / 'build.json', {'recipe': asdict(recipe), 'output_dir': str(root),
                'units': [{'house': 1, 'case': 'Case3', 'task': {'task_id': 't'}}]})
            sample = {'sample_id': 'sample', 'construction_unit': 0}
            with patch('lastmile_dataflow.workflows.scene_datasets._open_sample', return_value=sample) as construct:
                first = run_scene_dataset_unit(dataset, 0)
                second = run_scene_dataset_unit(dataset, 0)
            self.assertEqual(first['status'], 'completed'); self.assertEqual(second['samples'], [sample])
            construct.assert_called_once()
            self.assertEqual(len(list((root / 'diagnostics/test-unit-000').glob('attempt-*'))), 2)

    def test_station_recipe_requires_coverage_and_limit_twenty(self):
        for changes in ({'station_selection': 'all_valid'}, {'max_final_stations': 21}):
            with self.assertRaises(ValueError):
                SceneDatasetRecipe('dataset', 'library', 'robot', [1], **changes)
