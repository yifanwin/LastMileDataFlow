from types import SimpleNamespace
import unittest
import torch
from lastmile_dataflow.station_map.rl.env.terms import reset_grasp_targets


class GraspResetTests(unittest.TestCase):
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
