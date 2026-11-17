"""Training-success model selection, including the no-episode fallback."""
from collections import deque
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import unittest
from lastmile_dataflow.station_map.rl.training import save_training_checkpoint


class BestTests(unittest.TestCase):
    def test_strict_improvement_and_final_does_not_overwrite_best(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            for transition, values in [(10, [False, True]), (20, [True, False]), (30, [False]), (40, [True])]:
                save_training_checkpoint(agent, output, metadata, transition, deque(values, maxlen=100), final=True)
        best_calls = [c for c in agent.save.call_args_list if c.args[0].name == "best.pt"]
        self.assertEqual([c.kwargs["transitions"] for c in best_calls], [10, 40])
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 1)
        self.assertEqual(metadata["recent_episode_successes"], [True])

    def test_rolling_window_and_legacy_return_score_is_ignored(self):
        agent = Mock()
        metadata = {"best_training_return": {"mean": 10000}}
        successes = deque([True] * 100, maxlen=100)
        successes.extend([False] * 25)
        with TemporaryDirectory() as output:
            save_training_checkpoint(agent, output, metadata, 100, successes)
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 0.75)
        self.assertEqual(metadata["best_training_success_rate"]["count"], 100)
        self.assertEqual(len(metadata["recent_episode_successes"]), 100)
        self.assertEqual(agent.save.call_args_list[0].args[0].name, "best.pt")

    def test_no_eligible_episodes_only_falls_back_at_end(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            save_training_checkpoint(agent, output, metadata, 1, [])
            self.assertNotIn("best_training_success_rate", metadata)
            save_training_checkpoint(agent, output, metadata, 2, [], final=True)
        self.assertIsNone(metadata["best_training_success_rate"]["rate"])
        self.assertIsNotNone(metadata["best_training_success_rate"]["fallback"])

    def test_scored_model_replaces_no_episode_fallback(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            save_training_checkpoint(agent, output, metadata, 1, [], final=True)
            save_training_checkpoint(agent, output, metadata, 2, [False])
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 0)
        self.assertIsNone(metadata["best_training_success_rate"]["fallback"])
