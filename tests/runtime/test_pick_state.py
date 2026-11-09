"""One fact sequence must produce the same NumPy and Torch hold windows."""
import unittest

import numpy as np
import torch

from lastmile_dataflow.runtime.outcomes.pick_state import update_hold_window


class WindowTests(unittest.TestCase):
    def test_slip_release_and_partial_reset_match_numpy(self):
        t_start = torch.full((2,), -1., dtype=torch.float64)
        t_position = torch.zeros(2, 3, dtype=torch.float64)
        t_rotation = torch.eye(3, dtype=torch.float64).repeat(2, 1, 1)
        n_start, n_position, n_rotation = t_start.numpy().copy(), t_position.numpy().copy(), t_rotation.numpy().copy()
        for step in range(1100):
            time = torch.full((2,), step * .002, dtype=torch.float64)
            held = torch.tensor([True, step != 700])
            position = torch.zeros_like(t_position)
            position[0, 0] = .02 if step >= 500 else 0
            t_start, t_position, t_rotation, t_duration = update_hold_window(
                time, held, position, torch.eye(3).double().repeat(2, 1, 1),
                t_start, t_position, t_rotation, xp=torch)
            n_start, n_position, n_rotation, n_duration = update_hold_window(
                time.numpy(), held.numpy(), position.numpy(), np.tile(np.eye(3), (2, 1, 1)),
                n_start, n_position, n_rotation, xp=np)
            np.testing.assert_allclose(t_duration.numpy(), n_duration)
            if step == 900:
                t_start[1] = n_start[1] = -1
        self.assertAlmostEqual(float(t_duration[0]), 1.198)
        self.assertAlmostEqual(float(t_duration[1]), .396)
