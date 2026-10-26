"""Replay measured dynamics and nonuniform clocks without another rollout."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.data.rendering.replay import render_rollout
from lastmile_dataflow.runtime.simulation import Simulation
from tests.helpers import make_sim


class ReplayTests(unittest.TestCase):
    def test_replay_restores_objects_cameras_and_actual_clock_without_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, robot = make_sim(root)
            self.addCleanup(source.close)
            source.freeze(root / 'scene')
            sim = Simulation.from_snapshot(root / 'scene', robot, target='target')
            self.addCleanup(sim.close)
            collection = CollectionConfig(record_images=False, record_video=False, width=32, height=32)
            recorder = AttemptRecorder(root, {'collection': asdict(collection)}, attempt_id='r0')
            recorder.bind_simulation(sim)
            sim.begin()
            for _ in range(2):
                sim.step(sim.robot.neutral_action())
            recorder.finish('failure', 'fixture')
            self.assertTrue(recorder.result['replay_complete'])
            before = sim.state_vector().copy()
            renderer, writer = Mock(), Mock()
            renderer.__enter__ = Mock(return_value=renderer)
            renderer.__exit__ = Mock(return_value=False)
            writer.__enter__ = Mock(return_value=writer)
            writer.__exit__ = Mock(return_value=False)
            renderer.render.return_value = np.zeros((32, 32, 3), np.uint8)
            observed = []
            renderer.update_scene.side_effect = lambda data, **kw: observed.append(
                (float(data.time), data.qpos.copy(), data.cam_xpos.copy(), kw['camera']))
            with (patch('lastmile_dataflow.data.rendering.replay.mujoco.Renderer', return_value=renderer),
                  patch('lastmile_dataflow.data.rendering.replay.imageio.get_writer', return_value=writer),
                  patch('mujoco.mj_step', side_effect=AssertionError('replay must not integrate'))):
                result = render_rollout(recorder.path, root / 'video', camera='head_camera')
            self.assertEqual(result['source'], 'measured_state_replay')
            self.assertEqual(result['source_frame_indices'], [0, 1, 2])
            np.testing.assert_allclose(result['measured_times_s'], [0, .048, .1])
            for index, (clock, qpos, camera, name) in enumerate(observed):
                self.assertEqual(clock, recorder.samples[index]['time_s'])
                np.testing.assert_array_equal(qpos, recorder.samples[index]['qpos'])
                np.testing.assert_array_equal(camera, recorder.samples[index]['camera_xpos'])
                self.assertEqual(name, sim.robot.camera_names['head_camera'])
            np.testing.assert_array_equal(sim.state_vector(), before)
            self.assertTrue(sim.started)
            self.assertEqual(writer.append_data.call_count, 3)

    def test_no_execution_does_not_create_video(self):
        from lastmile_dataflow.utils.io import write_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / 'recording.json', {'steps': 0})
            with self.assertRaisesRegex(ValueError, 'no executed steps'):
                render_rollout(root, root / 'video', camera='head_camera')
            self.assertFalse((root / 'video').exists())

    def test_training_observations_render_each_measured_boundary_without_physics(self):
        from lastmile_dataflow.data.rendering.replay import render_observations
        from lastmile_dataflow.utils.io import read_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, robot = make_sim(root)
            self.addCleanup(source.close)
            source.freeze(root / 'scene')
            sim = Simulation.from_snapshot(root / 'scene', robot, target='target')
            self.addCleanup(sim.close)
            collection = CollectionConfig(record_images=False, record_video=False, width=32, height=32)
            recorder = AttemptRecorder(root, {'collection': asdict(collection)}, attempt_id='r0')
            recorder.bind_simulation(sim)
            sim.begin()
            sim.step(sim.robot.neutral_action())
            recorder.finish('failure', 'synthetic_observation_fixture')
            before = sim.state_vector().copy()
            renderer = Mock()
            renderer.__enter__ = Mock(return_value=renderer)
            renderer.__exit__ = Mock(return_value=False)
            renderer.render.return_value = np.zeros((32, 32, 3), np.uint8)
            with (patch('lastmile_dataflow.data.rendering.replay.mujoco.Renderer', return_value=renderer),
                  patch('mujoco.mj_step', side_effect=AssertionError('observation replay must not integrate'))):
                observed = render_observations(recorder.path, root/'observations', cameras=list(sim.robot.camera_names))
            self.assertEqual(observed['frames'], 2)
            self.assertEqual(observed['new_physical_steps'], 0)
            self.assertEqual(len(list((root/'observations/images').rglob('*.png'))), 6)
            self.assertFalse(read_json(recorder.path/'recording.json')['observation_complete'])
            np.testing.assert_array_equal(sim.state_vector(), before)
