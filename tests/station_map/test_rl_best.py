"""Training-return model selection, including the no-episode fallback."""
from collections import deque
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import unittest
from lastmile_dataflow.station_map.rl.training import save_training_checkpoint


class BestTests(unittest.TestCase):
    def test_strict_improvement_and_final_does_not_overwrite_best(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            for transition, values in [(10, [1, 3]), (20, [2]), (30, [0]), (40, [3])]:
                save_training_checkpoint(agent, output, metadata, transition, deque(values, maxlen=100), final=True)
        best_calls = [c for c in agent.save.call_args_list if c.args[0].name == "best.pt"]
        self.assertEqual([c.kwargs["transitions"] for c in best_calls], [10, 40])
        self.assertEqual(metadata["best_training_return"]["mean"], 3)
        self.assertEqual(metadata["recent_episode_returns"], [3])

    def test_no_eligible_episodes_only_falls_back_at_end(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            save_training_checkpoint(agent, output, metadata, 1, [])
            self.assertNotIn("best_training_return", metadata)
            save_training_checkpoint(agent, output, metadata, 2, [], final=True)
        self.assertIsNone(metadata["best_training_return"]["mean"])
        self.assertIsNotNone(metadata["best_training_return"]["fallback"])
