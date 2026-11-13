from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch

from lastmile_dataflow.station_map.rl.capture import RolloutCapture


class VideoSelectionTests(unittest.TestCase):
    def test_capture_copies_only_selected_slots_and_preserves_terminal_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "views").mkdir()
            task = SimpleNamespace(
                stations=[{"station_id": "S1", "split": "train"}],
                goal_error=lambda: (torch.zeros(3, 3), torch.zeros(3, 3)),
                robot=SimpleNamespace(data=SimpleNamespace(joint_pos=torch.zeros(3, 2))),
                fingers=[0, 1], forces=torch.zeros(3, 2), target=0,
                height_reference=torch.zeros(3), elapsed=torch.zeros(3),
                lift_peak=torch.zeros(3), hold=torch.zeros(3), step_collision=torch.zeros(3),
            )
            env = SimpleNamespace(task=task, num_envs=3, device="cpu", step_dt=0.05,
                                  prepared=SimpleNamespace(target_body="cup"),
                                  sim=SimpleNamespace(mj_model=None, data=SimpleNamespace(
                                      qpos=torch.arange(6.).reshape(3, 2), xpos=torch.zeros(3, 1, 3))))
            capture = RolloutCapture(output, 100, {"video": {}}, selected_slots=[1])
            capture.initialize(env, [0, 0, 0], 42, False)
            self.assertEqual(tuple(capture.frames[0].shape), (1, 2))
            env.sim.data.qpos += 10
            capture.sample(env, torch.tensor([True, True, True]))  # Terminal frame included.
            capture.sample(env, torch.tensor([True, False, True]))  # Finished video lane excluded.
            row = {"success": False, "collision_free": True, "infrastructure_failure": False, "failed": False}
            with patch("lastmile_dataflow.station_map.rl.capture.render_rollout", return_value={"path": "video.mp4"}) as render:
                capture.finish(env, [None, row, None])
            self.assertEqual(render.call_count, 1)
            np.testing.assert_array_equal(render.call_args.args[1], [[2, 3], [12, 13]])
            self.assertEqual(capture.states[0], [])
            self.assertEqual(capture.states[2], [])

    def test_success_with_collision_shares_one_representative_video(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "views").mkdir()
            env = SimpleNamespace(
                task=SimpleNamespace(stations=[{"station_id": "S1", "split": "validation"}]),
                sim=SimpleNamespace(mj_model=None),
                step_dt=0.05,
                prepared=SimpleNamespace(target_body="cup"),
            )
            capture = RolloutCapture(output, 100, {"video": {}}, representatives=True)
            capture.stations, capture.seed, capture.mode = [0], 42, "stochastic"
            capture.states, capture.metrics = [[np.zeros(2)]], [["measured facts"]]
            row = {"success": True, "collision_free": False, "infrastructure_failure": False, "failed": False}
            with patch(
                "lastmile_dataflow.station_map.rl.capture.render_rollout", return_value={"path": "video.mp4"}
            ) as render:
                capture.finish(env, [row])
                capture.finish(env, [row])
            self.assertEqual(render.call_count, 1)
            self.assertEqual(env._representative_video_categories, {"success", "collision"})
            self.assertEqual(capture.videos[0]["categories"], ["collision", "success"])
