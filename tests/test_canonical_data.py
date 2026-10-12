"""Synthetic protocol fixtures, real MuJoCo stepping, MOCK sensors (not GPU evidence)."""
from dataclasses import asdict, replace
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from helpers import make_sim
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.io import file_digest, read_json, write_json
from lastmile_dataflow.recording.calibration import rendered_camera
from lastmile_dataflow.recording.protocol import files_digest, rows, safe_path
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.integrations.molmobot_dataset import export_molmobot, state22, UnsupportedMolmoBotData
from lastmile_dataflow.integrations.molmobot_images import fisheye, transform_point
from lastmile_dataflow.exporting.canonical_index import rebuild_canonical_index
from lastmile_dataflow.validation.canonical import audit_canonical


class CanonicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim, self.robot = make_sim(self.root)
        self.collection = CollectionConfig(output_dir=str(self.root), width=32, height=24, record_video=False)
        self.task = {'task_id': 'synthetic-pick', 'operation': 'pick', 'instruction': 'Pick up the object.', 'instance_id': 'target'}
        self.recorder = None

    def tearDown(self):
        if self.recorder is not None and not self.recorder.finished:
            self.finish()
        self.sim.close()
        self.tmp.cleanup()

    def sensors(self, width, height, *, depth):
        result = {}
        cameras = list(self.sim.robot.camera_names)
        if self.sim.third_person_camera:
            cameras.append('third_person_camera')
        for name in cameras:
            pose = np.eye(4)
            pose[:3, 3] = [*self.sim.robot.group('base')[:2], 1.]
            values = np.ones((height, width), np.float32)
            values[0, :4] = [np.nan, np.inf, 0., 20.]
            result[name] = {'rgb': np.full((height, width, 3), int(self.sim.data.time * 1000) % 256, np.uint8),
                'depth': values if depth else None, 'depth_error': None,
                'calibration': {'resolution': [width, height], 'projection_type': 'pinhole',
                    'intrinsics': {'K': [[20, 0, width / 2], [0, 20, height / 2], [0, 0, 1]]},
                    'fovy': {'value': 1., 'unit': 'rad'}, 'near_m': .01, 'far_m': 20.,
                    'distortion': {'model': 'none', 'coefficients': []},
                    'T_world_camera_optical': pose.tolist(), 'source': 'MOCK_sensor_fixture'}}
        return result

    def bind(self, *, native=False, depth=True, observer=False):
        if native:
            self.sim.robot.config = replace(self.robot, control_profile='molmobot-native-v1', control_hz=10)
            for side in ('left', 'right'):
                i = self.sim.robot.act_ids[side + '_finger_act']
                self.sim.model.actuator_ctrlrange[i] = [-100, 100]
                self.sim.model.actuator_biastype[i] = 0
                self.sim.model.actuator_gainprm[i, :] = 0
                self.sim.model.actuator_gainprm[i, 0] = 1
            self.robot = self.sim.robot.config
        if observer:
            from types import SimpleNamespace
            self.sim.third_person_camera = SimpleNamespace(name='third_person_camera')
        collection = replace(self.collection, record_depth=depth)
        config = {'robot': asdict(self.robot), 'collection': asdict(collection), 'task': self.task,
                  'contact_policy': 'runtime-record-only-v1', 'seed': 3}
        self.recorder = AttemptRecorder(self.root, config, attempt_id='synthetic-episode')
        self.recorder.bind_simulation(self.sim)
        self.sensor_patch = patch.object(self.sim, 'capture_sensors', side_effect=self.sensors)
        self.sensor_patch.start()
        self.addCleanup(self.sensor_patch.stop)

    def tick(self, *, native=False, stop=None):
        before = self.sim.observe_state()
        action = self.sim.robot.neutral_action()
        action[0] = .01
        action[3] = .005
        command, stopped = (self.sim.step_policy(action, substep_check=stop) if native else self.sim.step(action, substep_check=stop))
        self.recorder.record_step(raw_action=action, command=command, before=before,
                                  after=self.sim.observe_state(), check=stopped or {'valid': True}, observations={})
        return command

    def finish(self, status='execution_complete'):
        self.recorder.finish(status, status, {'task_completion': {'status': 'unknown'}},
                             extra={'trajectory_metrics': {'simulation_valid': True, 'task_success': None}})
        return self.recorder.path

    def assertAudit(self, path):
        report = audit_canonical(path)
        self.assertTrue(report['valid'], report)

    def test_12_13_ticks_full_states_depth_masks_and_dynamic_observer(self):
        self.bind(observer=True)
        self.sim.begin()
        first, second = self.tick(), self.tick()
        self.assertEqual((first['actual_physics_steps'], second['actual_physics_steps']), (12, 13))
        path = self.finish()
        self.assertAudit(path)
        frames = rows(path / 'canonical/frames.jsonl')
        self.assertEqual(len(frames), 3)
        self.assertAlmostEqual(frames[-1]['timestamp'], .1)
        actions = rows(path / 'canonical/executed-actions.jsonl')
        np.testing.assert_allclose([r['actual_dt'] for r in actions], [.048, .052])
        np.testing.assert_allclose([r['requested_dt'] for r in actions], [.05, .05])
        with np.load(path / frames[0]['raw_state_ref'], allow_pickle=False) as z:
            self.assertEqual(z['qpos'].shape, (self.sim.model.nq,))
            self.assertEqual(z['qvel'].shape, (self.sim.model.nv,))
            self.assertNotEqual(len(z['qpos']), len(z['qvel']))
        observations = rows(path / 'raw/observations.jsonl')
        self.assertEqual(len(observations), 12)
        with np.load(path / observations[0]['depth']['path'], allow_pickle=False) as z:
            self.assertTrue(np.isnan(z['depth'][0, 0]))
            np.testing.assert_array_equal(z['valid_mask'][0, :4], [False] * 4)
        poses = [r for r in rows(path / 'canonical/camera-poses.jsonl') if r['camera_id'] == 'third_person_camera']
        self.assertNotEqual(poses[0]['T_world_camera_optical'], poses[-1]['T_world_camera_optical'])
        self.assertIsNone(read_json(path / 'canonical/outcomes.json')['success'])

    def test_partial_tick_does_not_pad_actual_time(self):
        self.bind()
        self.sim.begin()
        command = self.tick(stop=lambda: {'valid': False, 'reason': 'synthetic_stop'})
        self.assertEqual(command['actual_physics_steps'], 1)
        path = self.finish()
        self.assertAudit(path)
        action = rows(path / 'canonical/executed-actions.jsonl')[0]
        self.assertEqual(action['completion'], 'partial')
        self.assertAlmostEqual(action['actual_dt'], .004)
        self.assertEqual(action['physics_steps'], 1)

    def test_callback_exception_retains_executed_ticks(self):
        self.bind()
        self.sim.begin()
        def fail():
            raise RuntimeError('synthetic callback failure')
        with self.assertRaisesRegex(RuntimeError, 'callback failure'):
            self.sim.step(self.sim.robot.neutral_action(), substep_check=fail)
        path = self.finish('infrastructure_error')
        self.assertAudit(path)
        action = rows(path / 'canonical/executed-actions.jsonl')[0]
        self.assertEqual(action['completion'], 'partial')
        self.assertAlmostEqual(action['actual_dt'], .004)

    def test_rejected_nan_command_is_raw_and_not_executed(self):
        self.bind()
        self.sim.begin()
        action = self.sim.robot.neutral_action()
        action[0] = np.nan
        with self.assertRaises(ValueError):
            self.sim.step(action)
        path = self.finish('invalid_control')
        self.assertAudit(path)
        commands = rows(path / 'raw/commands.jsonl')
        self.assertEqual(len(commands), 1)
        self.assertIsNone(commands[0]['value'][0])
        with np.load(path / commands[0]['array_ref'], allow_pickle=False) as z:
            self.assertTrue(np.isnan(z['command_raw'][0]))
        self.assertEqual(rows(path / 'canonical/executed-actions.jsonl'), [])
        self.assertFalse((path / 'videos').exists())

    def test_sensor_error_preserves_actual_controls_and_quarantines_adapter(self):
        self.bind()
        self.sensor_patch.stop()
        with patch.object(self.sim, 'capture_sensors', side_effect=RuntimeError('synthetic rendering error')):
            self.sim.begin()
            self.tick()
        path = self.finish()
        self.assertEqual(len(rows(path / 'canonical/executed-actions.jsonl')), 1)
        self.assertFalse(audit_canonical(path)['valid'])
        with self.assertRaises(UnsupportedMolmoBotData):
            export_molmobot(path, self.root / 'derived', state_images_only=True)

    def test_depth_disabled_reason_is_explicit(self):
        self.bind(depth=False)
        self.sim.begin()
        self.tick()
        path = self.finish()
        self.assertAudit(path)
        self.assertEqual(read_json(path / 'canonical/manifest.json')['capabilities']['depth']['status'], 'disabled')

    def test_live_clock_mutation_is_rejected_before_actuation(self):
        self.bind()
        self.sim.begin()
        self.sim.model.opt.timestep = .002
        with self.assertRaisesRegex(ValueError, 'freezes physics_dt'):
            self.sim.step(self.sim.robot.neutral_action())
        self.sim.model.opt.timestep = .004
        path = self.finish('invalid_control')
        self.assertEqual(rows(path / 'canonical/executed-actions.jsonl'), [])
        self.assertFalse(audit_canonical(path)['valid'])

    def test_no_edit_png_disabled_keeps_raw_full_state_and_images(self):
        from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext
        from lastmile_dataflow.stations.no_edit_config import NoEditConfig
        self.sim.freeze(self.root / 'frozen')
        self.bind()
        ctx = ContinuousContext(self.sim, self.recorder, NoEditConfig(third_person_enabled=False), record_rgb=False, record_png=False)
        ctx.tick(self.sim.robot.neutral_action())
        self.assertIn('qpos', ctx.state())
        ctx.finish()
        path = self.finish()
        self.assertAudit(path)
        self.assertEqual(len(rows(path / 'raw/observations.jsonl')), 6)
        self.assertEqual(len(rows(path / 'raw/intent-events.jsonl')), 1)

    def test_symlink_or_noncanonical_artifact_tampering_is_rejected(self):
        self.bind()
        path = self.finish('planning_no_solution')
        result = path / 'source.json'
        result.write_text('{"tampered":true}\n')
        self.assertIn('whole_attempt_artifact_checksum_mismatch', audit_canonical(path)['issues'])
        (path / 'raw/evil').symlink_to('/tmp')
        self.assertFalse(audit_canonical(path)['valid'])

    def test_native_three_clocks_state22_action_chunks_and_immutable_export(self):
        self.bind(native=True)
        self.sim.begin()
        self.tick(native=True)
        self.tick(native=True)
        path = self.finish()
        self.assertAudit(path)
        controls = rows(path / 'raw/controls.jsonl')
        self.assertEqual(len(controls), 10)
        self.assertEqual([r['physics_tick_start'] for r in controls], list(range(0, 50, 5)))
        manifest = read_json(path / 'canonical/manifest.json')
        self.assertEqual((manifest['policy_hz'], manifest['control_hz'], manifest['physics_dt']), (10, 50, .004))
        before = files_digest(path)
        output = export_molmobot(path, self.root / 'molmobot', seed=3)
        self.assertEqual(files_digest(path), before)
        with np.load(output / 'data.npz', allow_pickle=False) as z:
            self.assertEqual(z['molmobot_22d_state'].shape, (2, 22))
            self.assertEqual(z['molmobot_native_action'].shape, (2, 20))
            self.assertEqual(z['action_chunks'].shape, (2, 16, 20))
            np.testing.assert_array_equal(z['action_chunk_valid_mask'][0, :3], [True, True, False])
            raw_groups = rows(path / 'canonical/robot-state.jsonl')[0]['groups']
            np.testing.assert_array_equal(z['molmobot_22d_state'][0], state22(raw_groups))
            np.testing.assert_allclose(z['molmobot_native_action'][0, :3], [.01, 0, 0], atol=1e-7)
        sample = rows(output / 'samples.jsonl')[0]
        self.assertEqual(len(sample['images']), 3)
        self.assertEqual(sample['image_transforms']['head_camera']['kind'], 'derived')
        with self.assertRaises(FileExistsError):
            export_molmobot(path, output)
        with self.assertRaises(ValueError):
            export_molmobot(path, path / 'derived')

    def test_position_actions_require_mapping_and_state_only_remains_available(self):
        self.bind()
        self.sim.begin()
        self.tick()
        self.tick()
        path = self.finish()
        self.assertAudit(path)
        with self.assertRaisesRegex(UnsupportedMolmoBotData, 'position gripper'):
            export_molmobot(path, self.root / 'invalid')
        output = export_molmobot(path, self.root / 'state-only', state_images_only=True)
        with np.load(output / 'data.npz', allow_pickle=False) as z:
            self.assertNotIn('molmobot_native_action', z.files)
        mapping = {'source_controller_profile': 'position-v1', 'target_controller_profile': 'molmobot-native-v1',
                   'calibration_source': 'SYNTHETIC_TEST_ONLY_NOT_REAL_CALIBRATION',
                   **{s: {'position_targets_m': [-.05, 0.], 'motor_commands': [-100, 100]} for s in ('left', 'right')}}
        output = export_molmobot(path, self.root / 'explicit-map', gripper_map=mapping)
        with np.load(output / 'data.npz', allow_pickle=False) as z:
            np.testing.assert_array_equal(z['molmobot_native_action'][:, [10, 18]], [[-100, -100]])

    def test_partial_adapter_window_is_masked_not_resampled(self):
        self.bind(native=True)
        self.sim.begin()
        self.tick(native=True, stop=lambda: {'valid': False})
        path = self.finish()
        output = export_molmobot(path, self.root / 'partial')
        with np.load(output / 'data.npz', allow_pickle=False) as z:
            np.testing.assert_array_equal(z['action_valid_mask'], [False])
            self.assertAlmostEqual(z['actual_dt'][0], .004)

    def test_resampled_native_increment_exceeding_bounds_is_masked_not_clipped(self):
        self.bind()
        self.sim.begin()
        for _ in range(2):
            before = self.sim.observe_state()
            action = self.sim.robot.neutral_action()
            action[0] = .1
            command, _ = self.sim.step(action)
            self.recorder.record_step(raw_action=action, command=command, before=before,
                                      after=self.sim.observe_state(), check={'valid': True}, observations={})
        path = self.finish()
        mapping = {'source_controller_profile': 'position-v1', 'target_controller_profile': 'molmobot-native-v1',
                   'calibration_source': 'SYNTHETIC_NOT_REAL_CALIBRATION',
                   **{s: {'position_targets_m': [-.05, 0.], 'motor_commands': [-100, 100]} for s in ('left', 'right')}}
        output = export_molmobot(path, self.root / 'bounded', gripper_map=mapping)
        with np.load(output / 'data.npz', allow_pickle=False) as z:
            self.assertGreater(z['molmobot_native_action'][0, 0], .1)
            self.assertFalse(z['action_valid_mask'][0])
        self.assertFalse(rows(output / 'samples.jsonl')[0]['native_safe_bounds_valid'])

    def test_checksum_and_alignment_tampering_are_rejected(self):
        self.bind()
        self.sim.begin()
        self.tick()
        path = self.finish()
        poses = path / 'canonical/camera-poses.jsonl'
        original = poses.read_bytes()
        poses.write_bytes(original + b'{}\n')
        self.assertFalse(audit_canonical(path)['valid'])
        poses.write_bytes(original)
        self.assertAudit(path)
        self.assertRaises(ValueError, safe_path, path, '../escape')
        self.assertRaises(ValueError, safe_path, path, '/tmp/escape')

    def test_all_outcome_index_includes_no_execution_and_unknown(self):
        self.bind()
        path = self.finish('planning_no_solution')
        self.assertAudit(path)
        self.assertEqual(rebuild_canonical_index(self.root), 1)
        entry = rows(self.root / 'canonical_index.jsonl')[0]
        self.assertEqual(entry['termination_status'], 'planning_no_solution')
        self.assertIsNone(entry['success'])
        self.assertEqual(entry['canonical_status'], 'sealed')

    def test_open_requires_explicit_point_and_saved_first_head_conditioning(self):
        self.task.update(operation='open', instruction='Open the door.')
        self.bind(native=True)
        self.sim.begin()
        self.tick(native=True)
        self.tick(native=True)
        path = self.finish()
        with self.assertRaisesRegex(UnsupportedMolmoBotData, 'conditioning-point'):
            export_molmobot(path, self.root / 'missing-point')
        output = export_molmobot(path, self.root / 'open', conditioning_point=[.5, .5])
        samples = rows(output / 'samples.jsonl')
        self.assertIn('<points coords=', samples[0]['prompt'])
        self.assertEqual(samples[0]['images'][-1], samples[1]['images'][-1])


class ImageAndCalibrationTests(unittest.TestCase):
    def test_fisheye_reproducible_mapping_and_point_crop(self):
        image = np.random.RandomState(1).randint(0, 256, (48, 80, 3), dtype=np.uint8)
        first, meta, mapping = fisheye(image, seed=4)
        second, other_meta, other_mapping = fisheye(image, seed=4)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(meta, other_meta)
        self.assertEqual(first.shape, (20, 26, 3))
        self.assertEqual(mapping['valid_mask'].shape, first.shape[:2])
        point = transform_point([.5, .5], meta)
        np.testing.assert_allclose(point, [.5, .5], atol=.05)
        with self.assertRaisesRegex(ValueError, 'outside fisheye crop'):
            transform_point([0, 0], meta)

    def test_GL_calibration_optical_direction_and_projection(self):
        from types import SimpleNamespace
        a, b = mujoco.MjvGLCamera(), mujoco.MjvGLCamera()
        for camera in (a, b):
            camera.pos = [1, 2, 3]
            camera.forward = [0, 0, -1]
            camera.up = [0, 1, 0]
            camera.frustum_near = .1
            camera.frustum_far = 10
            camera.frustum_bottom = -.05
            camera.frustum_top = .05
            camera.frustum_center = 0
            camera.orthographic = 0
        calibration = rendered_camera(SimpleNamespace(scene=SimpleNamespace(camera=[a, b])), 100, 80)
        T = np.asarray(calibration['T_world_camera_optical'])
        np.testing.assert_allclose(T @ [0, 0, 1, 1], [1, 2, 2, 1])
        K = np.asarray(calibration['intrinsics']['K'])
        np.testing.assert_allclose(K @ [0, 0, 1], [49.5, 39.5, 1])
        self.assertAlmostEqual(np.linalg.det(T[:3, :3]), 1)

    def test_fisheye_against_readonly_upstream_when_available(self):
        upstream = Path(__file__).resolve().parents[2] / 'MolmoBot/MolmoBot/olmo/data/image_warping_utils.py'
        if not upstream.is_file():
            self.skipTest('optional local upstream unavailable')
        try:
            spec = importlib.util.spec_from_file_location('readonly_molmobot_warp', upstream)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except ImportError:
            self.skipTest('optional Torch for upstream comparison unavailable')
        image = np.random.RandomState(1).randint(0, 256, (48, 80, 3), dtype=np.uint8)
        state = np.random.get_state()
        try:
            np.random.seed(4)
            reference = module.apply_fisheye_warping(image)
        finally:
            np.random.set_state(state)
        actual, _, _ = fisheye(image, seed=4)
        self.assertEqual(actual.shape, reference.shape)
        self.assertLessEqual(np.abs(actual.astype(int) - reference.astype(int)).max(), 1)


if __name__ == '__main__':
    unittest.main()
