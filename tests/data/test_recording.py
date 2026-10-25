"""实际控制边界、唯一采样和轨迹读写。"""

import tempfile
import json
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import mujoco

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.execution.context import ContinuousContext
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from tests.helpers import DatasetTestCase, make_sim


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sim, _ = make_sim(self.root)
        self.addCleanup(self.sim.close)

    def recorder(self, **options):
        collection = CollectionConfig(width=32, height=32, **options)
        recorder = AttemptRecorder(self.root, {"collection": asdict(collection)}, attempt_id="test")
        recorder.bind_simulation(self.sim)
        self.addCleanup(recorder.abort_close)
        return recorder

    def test_one_capture_per_boundary_and_phase_before_dispatch(self):
        recorder = self.recorder(record_images=True, record_video=True)
        context = ContinuousContext(self.sim, recorder, ExecutionConfig(width=32, height=32))
        self.addCleanup(context.finish)
        sensors = {
            name: {"rgb": np.zeros((32, 32, 3), dtype=np.uint8)} for name in self.sim.robot.camera_names
        }
        writers = [Mock() for _ in sensors]
        with (
            patch(
                "lastmile_dataflow.data.rendering.camera_calibration.capture_sensors", return_value=sensors
            ) as capture,
            patch("lastmile_dataflow.data.recorder.imageio.get_writer", side_effect=writers),
        ):
            for phase in ("navigation", "operation"):
                context.phase = phase
                context.tick(self.sim.robot.neutral_action())
            recorder.finish("failure", "fixture")
        self.assertEqual(capture.call_count, 3)  # Initial state + two actual intervals.
        self.assertEqual(len(list(recorder.path.glob("images/*/*.png"))), 3 * len(sensors))
        self.assertTrue(all(writer.append_data.call_count == 3 for writer in writers))
        self.assertTrue(all(writer.close.call_count == 1 for writer in writers))
        with np.load(recorder.path / "trajectory.npz") as trajectory:
            self.assertEqual(trajectory["phases"].tolist(), ["navigation", "operation"])
            self.assertEqual(len(trajectory["time_s"]), 3)
            self.assertEqual(trajectory["actions"].shape, (2, 20))
        self.assertTrue(recorder.result["recording_complete"])

    def test_partial_physics_interval_is_recorded_once(self):
        recorder = self.recorder(record_images=False, record_video=False)
        self.sim.begin()
        checks = 0

        def check():
            nonlocal checks
            checks += 1
            if checks == 2:
                raise RuntimeError("interrupted physics interval")
            return {"valid": True}

        with self.assertRaisesRegex(RuntimeError, "interrupted physics"):
            self.sim.step(self.sim.robot.neutral_action(), substep_check=check)
        recorder.finish("failure", "fixture")
        with np.load(recorder.path / "trajectory.npz") as trajectory:
            self.assertEqual(trajectory["actions"].shape, (1, 20))
            self.assertEqual(len(trajectory["time_s"]), 2)
            self.assertAlmostEqual(trajectory["time_s"][-1], 0.008)
        self.assertTrue(recorder.result["recording_complete"])

    def test_selected_video_views_keep_one_shared_frame_clock(self):
        from lastmile_dataflow.utils.io import read_json

        recorder = self.recorder(record_images=False, record_video=True,
            video_cameras={'head_camera': 'head', 'third_person_camera': 'third_person'})
        frames = {k: np.zeros((32, 32, 3), dtype=np.uint8)
                  for k in ('head_camera', 'third_person_camera', 'wrist_camera_l')}
        writers = [Mock(), Mock()]
        with (
            patch('lastmile_dataflow.data.recorder.imageio.get_writer', side_effect=writers) as create,
            patch.object(self.sim, 'render', return_value=frames),
        ):
            self.sim.begin()
            recorder.video_frame(frames)
            self.sim.step(self.sim.robot.neutral_action())
            recorder.video_frame(frames)
            recorder.finish('failure', 'fixture')
        self.assertEqual([Path(c.args[0]).name for c in create.call_args_list], ['head.mp4', 'third_person.mp4'])
        metadata = read_json(recorder.path / 'recording.json')['videos']
        self.assertEqual(metadata['head']['sample_times_s'], metadata['third_person']['sample_times_s'])
        self.assertEqual(metadata['head']['frames'], 2)
        self.assertTrue(all(w.append_data.call_count == 2 for w in writers))
        self.assertFalse((recorder.path / 'images').exists())


class TrajectoryTests(DatasetTestCase):
    def test_numeric_record_restores_full_measured_boundaries_without_stepping(self):
        recorder = AttemptRecorder(self.root, {'collection': asdict(CollectionConfig(
            record_images=False, record_video=False))}, attempt_id='measured')
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        self.sim.step(self.sim.robot.neutral_action())
        measured = self.sim.state_vector().copy()
        controls = self.sim.robot.snapshot_control()
        recorder.finish('failure', 'fixture')
        replay = mujoco.MjData(self.sim.model)
        with np.load(recorder.path / 'trajectory.npz', allow_pickle=False) as data:
            self.assertEqual(len(data['integration_state']), recorder.steps + 1)
            mujoco.mj_setState(self.sim.model, replay, data['integration_state'][-1],
                               int(data['state_spec']))
            mujoco.mj_forward(self.sim.model, replay)
            restored = np.empty_like(measured)
            mujoco.mj_getState(self.sim.model, replay, restored, int(data['state_spec']))
            np.testing.assert_array_equal(restored, measured)
            np.testing.assert_allclose(replay.cam_xpos, data['camera_xpos'][-1])
            self.assertEqual(replay.time, data['time_s'][-1])
            self.assertEqual(json.loads(str(data['control_json'][-1])), controls)
        self.assertIsNone(self.sim.renderer)

    def test_single_live_trajectory_and_native_state_layout(self):
        recorder = AttemptRecorder(
            self.root, {"collection": asdict(self.collection), "task": self.task, "seed": 3}, attempt_id="r0"
        )
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        for _ in range(2):
            self.sim.step(self.sim.robot.neutral_action())
        recorder.finish("failure", "fixture")
        with np.load(recorder.path / "trajectory.npz") as data:
            self.assertEqual(data["qpos"].shape[0], 3)
            self.assertEqual(data["actions"].shape, (2, 20))
            self.assertEqual(data["state22"].shape, (3, 22))
            np.testing.assert_allclose(np.diff(data["time_s"]), [0.048, 0.052])
        self.assertTrue(recorder.result["recording_complete"])
        self.assertEqual({p.name for p in recorder.path.iterdir()}, {"trajectory.npz", "recording.json"})
        with self.assertRaises(RuntimeError):
            self.sim.restore_snapshot(self.root / "initial.npz")

    def test_invalid_actions_do_not_create_executed_rows(self):
        recorder = AttemptRecorder(
            self.root, {"collection": asdict(self.collection), "task": self.task}, attempt_id="r0"
        )
        recorder.bind_simulation(self.sim)
        self.sim.begin()
        with self.assertRaises(ValueError):
            self.sim.step([0.0] * 19)
        recorder.finish("failure", "invalid_action")
        with np.load(recorder.path / "trajectory.npz") as data:
            self.assertEqual(data["actions"].shape, (0, 20))
            self.assertEqual(len(data["time_s"]), 1)
