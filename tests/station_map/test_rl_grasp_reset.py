from types import SimpleNamespace
import unittest
import torch
from lastmile_dataflow.station_map.rl.env.terms import reset_grasp_targets


class GraspResetTests(unittest.TestCase):
    def test_uniform_sampling_and_partial_reset(self):
        n = 16000
        task = SimpleNamespace(
            device='cpu', requested_grasp_ids=None,
            grasp_pool_counts=torch.tensor([16]),
            grasp_pool_table=torch.arange(16)[None],
            grasp_positions=torch.arange(48).reshape(16, 3).float(),
            grasp_rotations=torch.eye(3).repeat(16, 1, 1),
            grasp_ids=torch.zeros(n, dtype=torch.long),
            grasp_position=torch.zeros(n, 3),
            grasp_rotation=torch.zeros(n, 3, 3),
        )
        torch.manual_seed(42)
        reset_grasp_targets(task, torch.zeros(n, dtype=torch.long), torch.arange(n))
        counts = torch.bincount(task.grasp_ids, minlength=16)
        self.assertTrue(((counts - 1000).abs() < 150).all())
        original = task.grasp_ids.clone()
        positions = task.grasp_position.clone()
        task.requested_grasp_ids = torch.tensor([15])
        reset_grasp_targets(task, torch.tensor([0]), torch.tensor([0]))
        self.assertEqual(task.grasp_ids[0], 15)
        torch.testing.assert_close(task.grasp_ids[1:], original[1:])
        torch.testing.assert_close(task.grasp_position[1:], positions[1:])
        torch.testing.assert_close(task.grasp_position[0], task.grasp_positions[15])
