"""Training-success selection requires a full 100-episode window."""
from collections import deque
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import unittest
from lastmile_dataflow.station_map.rl.training import save_training_checkpoint


class BestTests(unittest.TestCase):
    def test_strict_improvement_and_final_does_not_overwrite_best(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            for transition, values in [(10, [False, True] * 50), (20, [True, False] * 50),
                                       (30, [False] * 100), (40, [True] * 100)]:
                save_training_checkpoint(agent, output, metadata, transition, deque(values, maxlen=100), final=True)
        best_calls = [c for c in agent.save.call_args_list if c.args[0].name == "best.pt"]
        self.assertEqual([c.kwargs["transitions"] for c in best_calls], [10, 40])
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 1)
        self.assertEqual(metadata["recent_episode_successes"], [True] * 100)

    def test_partial_window_cannot_select_best(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            for count in (1, 99):
                save_training_checkpoint(agent, output, metadata, count, [True] * count)
                self.assertNotIn("best_training_success_rate", metadata)
            self.assertTrue(all(c.args[0].name == "latest.pt" for c in agent.save.call_args_list))
            save_training_checkpoint(agent, output, metadata, 100, [True] * 99 + [False])
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 0.99)
        self.assertEqual(metadata["best_training_success_rate"]["count"], 100)

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

    def test_incomplete_window_only_falls_back_at_end(self):
        for count in (0, 1, 99):
            with self.subTest(count=count), TemporaryDirectory() as output:
                agent, metadata = Mock(), {}
                save_training_checkpoint(agent, output, metadata, 1, [True] * count)
                self.assertNotIn("best_training_success_rate", metadata)
                save_training_checkpoint(agent, output, metadata, 2, [True] * count, final=True)
                self.assertIsNone(metadata["best_training_success_rate"]["rate"])
                self.assertEqual(metadata["best_training_success_rate"]["count"], count)
                self.assertIsNotNone(metadata["best_training_success_rate"]["fallback"])

    def test_scored_model_replaces_no_episode_fallback(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            save_training_checkpoint(agent, output, metadata, 1, [], final=True)
            save_training_checkpoint(agent, output, metadata, 2, [False] * 100)
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 0)
        self.assertIsNone(metadata["best_training_success_rate"]["fallback"])

    def test_legacy_small_window_score_does_not_block_full_window(self):
        agent = Mock()
        metadata = {"best_training_success_rate": {"rate": 1.0, "count": 1}}
        with TemporaryDirectory() as output:
            save_training_checkpoint(agent, output, metadata, 100, [True] * 98 + [False] * 2)
        self.assertEqual(metadata["best_training_success_rate"]["rate"], 0.98)
        self.assertEqual(metadata["best_training_success_rate"]["count"], 100)
        self.assertEqual(agent.save.call_args_list[0].args[0].name, "best.pt")
