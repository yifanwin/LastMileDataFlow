"""Offline initial observations, frozen loading and Monte Carlo soft-value arithmetic."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import torch

from lastmile_dataflow.station_map.rl.env.offline import initial_observations
from lastmile_dataflow.station_map.rl.sac.agent import SAC, SACInference
from lastmile_dataflow.station_map.rl.soft_value import sample_soft_values, SoftValueSamples, summarize_values


class OfflineValueTests(unittest.TestCase):
    def test_fixed_home_frame_transform_uses_task_configuration(self):
        config = {
            "scene": {"cube": {"size_m": 0.04}, "home": [0.] * 7, "lift": 0.2},
            "observation": {
                "world_state": True, "base_height": 0.5,
                "tcp_position_base": [0.2, 0., 0.3], "tcp_rotation_base": np.eye(3).tolist(),
                "cube_position_world": [2., 3., 1.], "cube_rotation_world": np.eye(3).tolist(),
                "gripper_width": 0.08, "lift_range": [0., 0.4], "open_grip": 0.08,
            },
        }
        station = {"x": 1., "y": 2., "yaw": np.pi / 2}
        obs = initial_observations(config, [station], "cpu")
        self.assertEqual(obs.shape, (1, 69))
        torch.testing.assert_close(obs[0, :3], torch.tensor([1., -1., 0.5]))
        torch.testing.assert_close(obs[0, 41:44], torch.tensor([0.2, 0., 0.3]))
        torch.testing.assert_close(obs[0, 57:65], torch.tensor([0.] * 8))
        torch.testing.assert_close(obs[0, 65:], torch.tensor([1., 2., np.pi / 2, 0.2]))
        torch.testing.assert_close(obs[0, 13:16], torch.tensor([40., -50., 10.]))
        # Physical zero velocities remain zero; previous action is not an Actor mean action.
        torch.testing.assert_close(obs[0, 19:25], torch.zeros(6))
        config["observation"]["world_state"] = False
        torch.testing.assert_close(initial_observations(config, [station], "cpu"), obs[:, :65])

    def test_inference_loader_uses_online_networks_without_optimizers(self):
        agent = SAC(69, "cpu", mode="flat")
        with torch.no_grad():
            agent.log_alpha.fill_(-2)
            next(agent.target1.parameters()).fill_(123)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            agent.save(path, metadata={}, transitions=42)
            with patch("torch.optim.Adam", side_effect=AssertionError("optimizer constructed")):
                frozen, saved = SACInference.load(path, "cpu")
        self.assertEqual(saved["transitions"], 42)
        self.assertFalse(hasattr(frozen, "target1"))
        torch.testing.assert_close(frozen.alpha, agent.alpha)
        obs = torch.randn(3, 69)
        action = torch.randn(3, 7).tanh()
        for name in ("q1", "q2"):
            torch.testing.assert_close(getattr(frozen, name)(obs, action), getattr(agent, name)(obs, action))
        self.assertTrue(all(not p.requires_grad for p in frozen.actor.parameters()))
        self.assertFalse(frozen.actor.training)

    def test_samples_keep_identity_across_batch_boundaries(self):
        agent = SAC(69, "cpu", mode="flat")
        observations = torch.randn(3, 69)
        one = sample_soft_values(agent, observations, samples=5, batch_size=15, seed=7)
        tail = sample_soft_values(agent, observations, samples=5, batch_size=4, seed=7)
        self.assertEqual(one.action.shape, (3, 5, 7))
        for name in ("action", "log_prob", "q1", "q2"):
            torch.testing.assert_close(getattr(one, name), getattr(tail, name), atol=1e-6, rtol=1e-5)
        self.assertFalse(one.action.requires_grad)

    def test_minimum_precedes_average_and_entropy_sign_and_invalid_mask(self):
        sampled = SoftValueSamples(
            action=torch.zeros(2, 3, 7), log_prob=torch.tensor([[-2., 1., -3.], [1., 1., 1.]]),
            q1=torch.tensor([[1., 10., 3.], [4., 5., 6.]]),
            q2=torch.tensor([[5., 2., 9.], [9., 1., 8.]]),
        )
        rows = summarize_values([
            {"station_id": "a", "valid": True}, {"station_id": "blocked", "valid": False},
            {"station_id": "b", "valid": True},
        ], sampled, 0.5)
        values = torch.tensor([2., 1.5, 4.5], dtype=torch.float64)
        self.assertAlmostEqual(rows[0]["v_mean"], float(values.mean()))
        self.assertAlmostEqual(rows[0]["v_std"], float(values.std()))
        self.assertAlmostEqual(rows[0]["v_sem"], float(values.std()) / 3**0.5)
        self.assertAlmostEqual(rows[0]["q_min_mean"], 2.)
        self.assertAlmostEqual(rows[0]["v_mean"], rows[0]["q_min_mean"] + rows[0]["entropy_term_mean"])
        self.assertIsNone(rows[1]["v_mean"])
        self.assertAlmostEqual(rows[2]["v_mean"], (4 + 1 + 6) / 3 - 0.5)

    def test_invalid_sample_count_or_batch_size_is_rejected(self):
        for samples, batch in ((1, 10), (128, 0)):
            with self.assertRaises(ValueError):
                sample_soft_values(SimpleNamespace(), torch.zeros(1, 69), samples=samples, batch_size=batch, seed=1)
