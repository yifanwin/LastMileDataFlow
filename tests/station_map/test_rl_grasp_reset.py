from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np
import torch
from lastmile_dataflow.station_map.rl.env.terms import TaskState, reset_grasp_targets
from lastmile_dataflow.operation.grasps import sample_grasps


class GraspResetTests(unittest.TestCase):
    def test_preprocessed_pool_loads_without_fps_and_applies_roll_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "grasps.npz"
            poses = np.repeat(np.eye(4)[None], 128, axis=0)
            poses[:, 0, 3] = np.arange(128) / 1000
            np.savez(path, transforms=poses, source_rows=np.arange(128) * 2, source="original.npz")
            scene = dict(data_root=directory, scene_xml="unused", scene_metadata="unused", robot_xml="unused", robot_scale=.9)
            task = TaskState.__new__(TaskState)
            task.device, task.stations = "cpu", [{}, {}]
            task.contact_protocol = True
            task.env = SimpleNamespace(num_envs=4, prepared=SimpleNamespace(target_body="target"), research_config={
                "scene": scene, "grasp": {"preprocessed_path": str(path), "roll_rad": np.pi / 2,
                                          "sample_count": 16, "sample_seed": 42},
            })
            with patch("lastmile_dataflow.station_map.rl.env.terms.select_grasps", side_effect=AssertionError("FPS repeated")):
                task.prepare_corpus_grasps()
            expected = sample_grasps(task.grasps, 16, seed=42)
            self.assertEqual([task.grasps[i]["row"] for i in task.grasp_pools[0]], [c["row"] for c in expected])
            self.assertEqual(task.grasp_pools[0], task.grasp_pools[1])
            torch.testing.assert_close(task.grasp_pool_counts, torch.tensor([16, 16]))
            np.testing.assert_allclose(task.grasp_rotations[0], [[0, -1, 0], [1, 0, 0], [0, 0, 1]], atol=1e-7)
            np.testing.assert_allclose(task.grasp_positions, poses[:, :3, 3], atol=1e-7)

    def test_uniform_sampling_and_partial_reset(self):
        for count in (5, 16, 23):
            with self.subTest(count=count):
                self.check_sampling(count)

    def check_sampling(self, count):
        n = count * 1000
        task = SimpleNamespace(
            device='cpu', requested_grasp_ids=None,
            grasp_pool_counts=torch.tensor([count]),
            grasp_pool_table=torch.arange(count)[None],
            grasp_positions=torch.arange(count * 3).reshape(count, 3).float(),
            grasp_rotations=torch.eye(3).repeat(count, 1, 1),
            grasp_ids=torch.zeros(n, dtype=torch.long),
            grasp_position=torch.zeros(n, 3),
            grasp_rotation=torch.zeros(n, 3, 3),
        )
        torch.manual_seed(42)
        reset_grasp_targets(task, torch.zeros(n, dtype=torch.long), torch.arange(n))
        counts = torch.bincount(task.grasp_ids, minlength=count)
        self.assertTrue(((counts - 1000).abs() < 150).all())
        original = task.grasp_ids.clone()
        positions = task.grasp_position.clone()
        task.requested_grasp_ids = torch.tensor([count - 1])
        reset_grasp_targets(task, torch.tensor([0]), torch.tensor([0]))
        self.assertEqual(task.grasp_ids[0], count - 1)
        torch.testing.assert_close(task.grasp_ids[1:], original[1:])
        torch.testing.assert_close(task.grasp_position[1:], positions[1:])
        torch.testing.assert_close(task.grasp_position[0], task.grasp_positions[count - 1])


class RealObjectGraspTests(unittest.TestCase):
    def test_contact_protocol_rejects_raw_library(self):
        task = TaskState.__new__(TaskState)
        task.contact_protocol = True
        task.device = "cpu"
        task.env = SimpleNamespace(num_envs=1, research_config={
            "scene": dict(data_root="data", scene_xml="s", scene_metadata="m", robot_xml="r", robot_scale=.9),
            "grasp": {"path": "raw.npz", "roll_rad": 0},
        })
        with self.assertRaisesRegex(ValueError, "preprocessed_path"):
            task.prepare_corpus_grasps()

    def test_trial_grasps_independent_of_batching_and_global_rng(self):
        from lastmile_dataflow.station_map.rl.evaluation import request_episode_grasps, episode_generators
        task = SimpleNamespace(device="cpu", grasp_pools=[list(range(16))])
        seeds = [42 + i for i in range(20)]
        request_episode_grasps(task, [0] * 20, episode_generators(seeds, "cpu"))
        expected = task.requested_grasp_ids.clone()
        torch.manual_seed(999)
        actual = []
        for start in range(0, 20, 3):
            batch = seeds[start:start + 3]
            request_episode_grasps(task, [0] * len(batch), episode_generators(batch, "cpu"))
            actual.extend(task.requested_grasp_ids.tolist())
        self.assertEqual(expected.tolist(), actual)


    def test_goal_tracks_object_pose_and_local_grasp(self):
        from lastmile_dataflow.station_map.rl.env.observation import pose_goal_error
        z90 = torch.tensor([[[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]])
        y90 = torch.tensor([[[0., 0., 1.], [0., 1., 0.], [-1., 0., 0.]]])
        local_p = torch.tensor([[.02, -.03, .01]])
        for body_r in (torch.eye(3)[None], y90):
            body_p = torch.tensor([[1., 2., 3.]])
            tcp_p = body_p + torch.einsum("bij,bj->bi", body_r, local_p)
            poses = (tcp_p, body_r @ z90, torch.zeros(1, 3), torch.eye(3)[None], body_p, body_r)
            dp, dr = pose_goal_error(poses, local_p, z90)
            torch.testing.assert_close(dp, torch.zeros_like(dp), atol=1e-6, rtol=0)
            torch.testing.assert_close(dr, torch.zeros_like(dr), atol=1e-6, rtol=0)
