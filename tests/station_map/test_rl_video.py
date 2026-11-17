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
                                      mocap_pos=torch.zeros(3, 1, 3), mocap_quat=torch.tensor([[[1., 0., 0., 0.]]]).expand(3, -1, -1),
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
            with np.load(capture.videos[0]["replay"]) as saved:
                np.testing.assert_array_equal(saved["mocap_pos"], [[0., 0., 0.]])
                np.testing.assert_array_equal(saved["mocap_quat"], [[1., 0., 0., 0.]])
            np.testing.assert_array_equal(render.call_args.kwargs["mocap_pos"], [[0., 0., 0.]])

    def test_cube_video_uses_clearance_force_gate_and_configured_peak_scale(self):
        position = torch.tensor([[0., 0., 0.785], [0., 0., 0.785]])
        tilt = torch.tensor([[[1., 0., 0.], [0., 2**-0.5, -2**-0.5],
                              [0., 2**-0.5, 2**-0.5]]]).expand(2, -1, -1)
        task = SimpleNamespace(
            contact_protocol=True, table_height_m=0.75,
            poses=lambda: (None, None, None, None, position, tilt),
            goal_error=lambda: (torch.zeros(2, 3), torch.zeros(2, 3)),
            robot=SimpleNamespace(data=SimpleNamespace(joint_pos=torch.zeros(2, 2))),
            fingers=[0, 1], forces=torch.tensor([[0.5, 1.], [0.6, 0.7]]),
            elapsed=torch.zeros(2), lift_peak=torch.full((2,), 0.8),
            hold=torch.zeros(2), step_collision=torch.zeros(2),
        )
        env = SimpleNamespace(task=task, num_envs=2, device="cpu",
                              research_config={"success": {"finger_force_min_n": 0.5, "lift_m": 0.01},
                                               "scene": {"cube": {"size_m": 0.04}}},
                              sim=SimpleNamespace(data=SimpleNamespace(qpos=torch.zeros(2, 3), mocap_pos=torch.zeros(2, 1, 3), mocap_quat=torch.ones(2, 1, 4))))
        capture = RolloutCapture("unused", 100, {})
        capture.initialize(env, [0, 1], 42, False)
        self.assertEqual(capture.facts[0][:, 4].tolist(), [0., 1.])
        torch.testing.assert_close(capture.facts[0][:, 5], torch.full((2,), 0.785 - 0.02 * 2**0.5 - 0.75))
        torch.testing.assert_close(capture.facts[0][:, 6], torch.full((2,), 0.008))

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
            capture.mocap_pos, capture.mocap_quat = torch.zeros(1, 1, 3), torch.ones(1, 1, 4)
            row = {"success": True, "collision_free": False, "infrastructure_failure": False, "failed": False}
            with patch(
                "lastmile_dataflow.station_map.rl.capture.render_rollout", return_value={"path": "video.mp4"}
            ) as render:
                capture.finish(env, [row])
                capture.finish(env, [row])
            self.assertEqual(render.call_count, 1)
            self.assertEqual(env._representative_video_categories, {"success", "collision"})
            self.assertEqual(capture.videos[0]["categories"], ["collision", "success"])


class IndependentCaptureTests(unittest.TestCase):
    def test_all_twenty_trials_at_one_station_have_distinct_videos_and_seeds(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "views").mkdir()
            env = SimpleNamespace(task=SimpleNamespace(stations=[{"station_id": "S0246", "split": "train"}]),
                                  sim=SimpleNamespace(mj_model=None), step_dt=0.05,
                                  prepared=SimpleNamespace(target_body="cube"))
            capture = RolloutCapture(output, 100, {"video": {}}, selected_slots=list(range(20)),
                                     trial_ids=list(range(20)), trial_seeds=list(range(42, 62)))
            capture.stations, capture.seed, capture.mode = [0] * 20, 42, "stochastic"
            capture.states = [[np.full(2, i)] for i in range(20)]
            capture.metrics = [["terminal frame"] for _ in range(20)]
            capture.mocap_pos, capture.mocap_quat = torch.zeros(20, 1, 3), torch.ones(20, 1, 4)
            row = dict(success=False, collision_free=True, infrastructure_failure=False, failed=False)
            with patch("lastmile_dataflow.station_map.rl.capture.render_rollout", return_value={}) as render:
                capture.finish(env, [row] * 20)
            self.assertEqual(render.call_count, 20)
            self.assertEqual(len({str(call.args[3]) for call in render.call_args_list}), 20)
            self.assertEqual([v["seed"] for v in capture.videos], list(range(42, 62)))
            self.assertEqual([v["trial"] for v in capture.videos], list(range(20)))
            self.assertEqual(len(list((output / "replays").glob("*.npz"))), 20)

    def test_full_replay_is_cut_at_its_own_terminal_step(self):
        from lastmile_dataflow.station_map.rl.capture import EpisodeReplays

        data = SimpleNamespace(qpos=torch.zeros(2, 3), qvel=torch.zeros(2, 3), ctrl=torch.zeros(2, 2),
                               mocap_pos=torch.zeros(2, 1, 3), mocap_quat=torch.ones(2, 1, 4))
        env = SimpleNamespace(sim=SimpleNamespace(data=data))
        replay = EpisodeReplays()
        replay.initialize(env)
        for step in (1, 2):
            data.qpos.fill_(step)
            data.qvel.fill_(step * 10)
            data.ctrl.fill_(step * 100)
            data.mocap_pos.fill_(step)
            replay.sample(env, torch.full((2, 7), float(step)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.npz"
            replay.save(0, 1, path)
            with np.load(path) as saved:
                self.assertEqual(saved["qpos"].shape, (2, 3))
                self.assertEqual(saved["qvel"].shape, (2, 3))
                self.assertEqual(saved["action"].shape, (1, 7))
                self.assertEqual(saved["ctrl"].shape, (1, 2))
                self.assertEqual(saved["qpos"][-1, 0], 1)
                self.assertEqual(saved["ctrl"][-1, 0], 100)
                self.assertEqual(saved["mocap_pos"].shape, (2, 1, 3))
                self.assertEqual(saved["mocap_pos"][-1, 0, 0], 1)
