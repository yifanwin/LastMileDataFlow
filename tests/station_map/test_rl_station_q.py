import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import torch

from lastmile_dataflow.station_map.rl.station_q import select_acceptance_stations, summarize_trials


class StationQTests(unittest.TestCase):
    def test_action_samples_are_batched_without_changing_mean_or_variance(self):
        from lastmile_dataflow.station_map.rl.station_q import action_sampled_q
        from tests.station_map.test_rl_sac import observation

        actor = Mock()
        actor.sample.side_effect = lambda obs: (torch.arange(len(obs.nodes)).float()[:, None], None)
        q1 = Mock(side_effect=lambda obs, actions: actions[:, 0])
        q2 = Mock(side_effect=lambda obs, actions: actions[:, 0] + 1)
        mean, std = action_sampled_q(SimpleNamespace(actor=actor, q1=q1, q2=q2), observation(2), 3)
        torch.testing.assert_close(mean, torch.tensor([1.0, 4.0]))
        torch.testing.assert_close(std, torch.full((2,), (2 / 3) ** 0.5))
        actor.sample.assert_called_once()
        q1.assert_called_once()
        q2.assert_called_once()

    def test_predeclared_groups_are_disjoint_and_held_out(self):
        rows = [
            {"station_id": str(i), "valid": i != 0, "split": "train" if i == 1 else "acceptance", "q_mean": float(i)}
            for i in range(40)
        ]
        config = {"seed": 42, "top": 4, "middle": 4, "low": 4, "uniform": 8}
        selection = select_acceptance_stations(rows, config)
        self.assertEqual(len(selection), 20)
        self.assertEqual(len({r["station_id"] for r in selection}), 20)
        self.assertEqual([r["station_id"] for r in selection[:4]], ["39", "38", "37", "36"])
        self.assertTrue(all(r["valid"] for r in selection))
        self.assertEqual(selection, select_acceptance_stations(rows, config))


    def test_infrastructure_failures_remain_in_total_denominator(self):
        selection = [{"station_id": "s1", "group": "top", "q_mean": 1.0}]
        trials = [
            {
                "station_id": "s1",
                "success": True,
                "collision_free": True,
                "infrastructure_failure": False,
                "return": 20.0,
            },
            {
                "station_id": "s1",
                "success": False,
                "collision_free": False,
                "infrastructure_failure": True,
                "return": 0.0,
            },
        ]
        summary = summarize_trials(selection, trials, seed=42)
        self.assertEqual(summary["infrastructure_failures"], 1)
        self.assertEqual(summary["stations"][0]["trials"], 2)
        self.assertEqual(summary["stations"][0]["healthy_trials"], 1)
        self.assertEqual(summary["stations"][0]["success_rate"], 1.0)
        self.assertIsNone(summary["q_success_spearman"])
