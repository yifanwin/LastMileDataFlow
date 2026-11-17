"""Best selection uses the same last-100-episode window as TensorBoard."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

from lastmile_dataflow.station_map.rl.monitor import SuccessWindow, TrainingMonitor
from lastmile_dataflow.station_map.rl.training import (
    flush_training_monitor, save_best_checkpoint, save_periodic_checkpoint,
)


class BestTests(unittest.TestCase):
    def test_best_matches_tensorboard_rolling_window_and_excludes_warmup_and_unhealthy(self):
        agent, metadata = Mock(), {}
        clock = Mock(return_value=0)
        with TemporaryDirectory() as output, patch(
            "lastmile_dataflow.station_map.rl.monitor.time", SimpleNamespace(monotonic=clock)
        ):
            monitor = TrainingMonitor(output, every_seconds=30)
            try:
                for success, warmup, unhealthy in ((False, False, False), (True, False, False),
                                                   (True, True, False), (True, False, True)):
                    monitor.record_episode({"success": success, "random_warmup": warmup,
                                            "infrastructure_failure": unhealthy})
                clock.return_value = 29
                flush_training_monitor(agent, output, metadata, 10, monitor)
                agent.save.assert_not_called()
                clock.return_value = 30
                flush_training_monitor(agent, output, metadata, 20, monitor)
                self.assertEqual(metadata["best_training_success_rate"]["count"], 2)
                self.assertEqual(metadata["best_training_success_rate"]["rate"], 0.5)
                # Empty intervals retain the rolling history without selecting a new model.
                clock.return_value = 60
                flush_training_monitor(agent, output, metadata, 30, monitor)
                for now, transition, values in ((90, 40, [False, True]), (120, 50, [False]),
                                                (150, 60, [True] * 100)):
                    for value in values:
                        monitor.record_episode({"success": value, "infrastructure_failure": False})
                    clock.return_value = now
                    flush_training_monitor(agent, output, metadata, transition, monitor)
                save_best_checkpoint(agent, output, metadata, 70, None, final=True)
                self.assertEqual([c.kwargs["transitions"] for c in agent.save.call_args_list], [20, 60])
                self.assertTrue(all(c.args[0].name == "best.pt" for c in agent.save.call_args_list))
                self.assertEqual(metadata["best_training_success_rate"]["count"], 100)
                self.assertEqual(metadata["best_training_success_rate"]["rate"], 1)
                reader = EventAccumulator(str(Path(output) / "tensorboard")).Reload()
                points = reader.Scalars("train/success")
                self.assertEqual([p.step for p in points], [20, 30, 40, 50, 60])
                for point, expected in zip(points, [0.5, 0.5, 0.5, 0.4, 1], strict=True):
                    self.assertAlmostEqual(point.value, expected)
                self.assertEqual([p.step for p in reader.Scalars("train_interval/success")], [20, 40, 50, 60])
            finally:
                monitor.close(70)

    def test_final_partial_interval_competes_with_best(self):
        clock = Mock(return_value=0)
        with TemporaryDirectory() as output, patch(
            "lastmile_dataflow.station_map.rl.monitor.time", SimpleNamespace(monotonic=clock)
        ):
            agent, metadata = Mock(), {}
            monitor = TrainingMonitor(output, every_seconds=30)
            monitor.record_episode({"success": True, "infrastructure_failure": False})
            clock.return_value = 5
            flush_training_monitor(agent, output, metadata, 10, monitor, force=True)
            monitor.close(10)
            self.assertEqual(metadata["best_training_success_rate"]["rate"], 1)
            reader = EventAccumulator(str(Path(output) / "tensorboard")).Reload()
            self.assertEqual(reader.Scalars("train/success")[0].step, 10)
            agent.save.assert_called_once()

    def test_no_episodes_falls_back_only_at_end_and_scored_window_replaces_it(self):
        agent, metadata = Mock(), {}
        with TemporaryDirectory() as output:
            save_best_checkpoint(agent, output, metadata, 1, None)
            agent.save.assert_not_called()
            save_best_checkpoint(agent, output, metadata, 2, None, final=True)
            self.assertIsNone(metadata["best_training_success_rate"]["rate"])
            self.assertIsNotNone(metadata["best_training_success_rate"]["fallback"])
            save_best_checkpoint(agent, output, metadata, 3, SuccessWindow(0, 1))
            self.assertEqual(metadata["best_training_success_rate"]["rate"], 0)
            self.assertIsNone(metadata["best_training_success_rate"]["fallback"])

    def test_periodic_checkpoints_keep_every_400k_boundary_and_batch_overshoot(self):
        agent, metadata = Mock(), {"config": {}}
        checkpoint_at = 400_000
        with TemporaryDirectory() as output:
            for transitions in (399_999, 400_000, 400_128, 799_999, 800_127, 1_200_001):
                checkpoint_at = save_periodic_checkpoint(agent, output, metadata, transitions, checkpoint_at)
            self.assertTrue((Path(output) / "ckpts").is_dir())
            self.assertEqual([c.args[0].relative_to(output).as_posix() for c in agent.save.call_args_list],
                             ["ckpts/step-000400000.pt", "ckpts/step-000800127.pt", "ckpts/step-001200001.pt"])
            self.assertEqual([c.kwargs["transitions"] for c in agent.save.call_args_list],
                             [400_000, 800_127, 1_200_001])
            self.assertEqual(checkpoint_at, 1_600_000)
