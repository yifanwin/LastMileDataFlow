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
