"""Read real event files while training writers remain open."""

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import torch

if importlib.util.find_spec("tensorboard"):
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
    from lastmile_dataflow.station_map.rl.monitor import TrainingMonitor


def losses(value):
    return {
        "actor_loss": torch.tensor(value, requires_grad=True),
        "critic_loss": torch.tensor(2 * value),
        "alpha_loss": torch.tensor(-value),
        "entropy": torch.tensor(value / 2),
        "alpha": torch.tensor(value / 10),
    }


def episode(value, *, warmup=False, unhealthy=False):
    return {
        "infrastructure_failure": unhealthy,
        "random_warmup": warmup,
        "return": value,
        "steps": value * 2,
        "success": value > 1,
        "collision_free": True,
        "contact_bonus_count": 1,
        "reward_component_names": ("position", "rotation", "collision", "limit", "control_failure", "success",
                                   "open", "pose", "slow", "close", "contact", "lift", "held"),
        "reward_components": [value] * 13,
    }


@unittest.skipUnless(importlib.util.find_spec("tensorboard"), "requires RL TensorBoard dependency")
class TrainingMonitorTests(unittest.TestCase):
    def test_live_interval_means_all_updates_and_healthy_episode_groups(self):
        clock = Mock(return_value=0)
        with tempfile.TemporaryDirectory() as directory, patch(
            "lastmile_dataflow.station_map.rl.monitor.time", SimpleNamespace(monotonic=clock)
        ):
            monitor = TrainingMonitor(directory, every_seconds=30)
            try:
                monitor.record_update(losses(2.0))
                monitor.record_update(losses(4.0))
                self.assertFalse(monitor.means["loss/actor"].total.requires_grad)
                monitor.record_episode(episode(2))
                monitor.record_episode(episode(6))
                monitor.record_episode(episode(99, unhealthy=True))
                monitor.record_episode(episode(10, warmup=True))
                monitor.record_runtime({"event": "learner", "transitions": 128, "updates": 2, "replay_size": 128})
                clock.return_value = 29
                monitor.flush(128)
                reader = EventAccumulator(str(Path(directory) / "tensorboard"))
                self.assertEqual(reader.Reload().Tags()["scalars"], [])
                clock.return_value = 30
                monitor.flush(256)
                reader.Reload()
                expected = {
                    "loss/actor": 3, "loss/critic": 6, "loss/alpha": -3,
                    "policy/entropy": 1.5, "policy/alpha": 0.4,
                    "train/return": 4, "train/steps": 8, "train/success": 1,
                    "train/contact_bonus_count": 1, "warmup/return": 10,
                    "runtime/updates": 2, "runtime/replay_size": 128,
                }
                for name in episode(1)["reward_component_names"]:
                    expected[f"train/reward/{name}"] = 4
                    expected[f"warmup/reward/{name}"] = 10
                for tag, value in expected.items():
                    point, = reader.Scalars(tag)
                    self.assertEqual(point.step, 256)
                    self.assertAlmostEqual(point.value, value, places=6)
                # An empty interval emits no fabricated samples or stale losses.
                clock.return_value = 60
                monitor.flush(512)
                reader.Reload()
                self.assertEqual(len(reader.Scalars("loss/actor")), 1)
                monitor.record_update(losses(8.0))
                monitor.record_episode(episode(12))
                clock.return_value = 61
                monitor.flush(640)
                self.assertEqual(len(reader.Reload().Scalars("loss/actor")), 1)
            finally:
                monitor.close(640)
            points = reader.Reload().Scalars("loss/actor")
            self.assertEqual([(p.step, p.value) for p in points], [(256, 3), (640, 8)])
            self.assertEqual(reader.Scalars("train/return")[-1].value, 12)

    def test_evaluation_is_immediate_scoped_and_skips_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = TrainingMonitor(directory, every_seconds=30)
            try:
                monitor.record_evaluation({
                    "scope": "fixed_station", "transitions": 500,
                    "evaluation_success_rate": 0.5, "episode_return_mean": 10,
                    "healthy_trials": 2, "total_trials": 3, "infrastructure_failures": 1,
                    "value_success_correlation": None,
                    "debug_stations": {"mean": {"healthy": 2, "held_1s": 1}},
                })
                reader = EventAccumulator(str(Path(directory) / "tensorboard")).Reload()
                prefix = "evaluation/fixed_station/"
                self.assertEqual(reader.Scalars(prefix + "evaluation_success_rate")[0].value, 0.5)
                self.assertEqual(reader.Scalars(prefix + "healthy_trials")[0].step, 500)
                self.assertEqual(reader.Scalars(prefix + "debug/mean/held_1s")[0].value, 1)
                self.assertNotIn(prefix + "value_success_correlation", reader.Tags()["scalars"])
            finally:
                monitor.close(500)

    def test_resume_purges_future_steps_and_new_output_stays_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            monitor = TrainingMonitor(source, every_seconds=30)
            for step in (100, 200):
                monitor.record_episode(episode(step))
                monitor.flush(step, force=True)
            monitor.close(200)
            resumed = TrainingMonitor(source, every_seconds=30, purge_step=150)
            resumed.record_episode(episode(160))
            resumed.close(160)
            reader = EventAccumulator(str(source / "tensorboard")).Reload()
            self.assertEqual([p.step for p in reader.Scalars("train/return")], [100, 160])
            fresh = Path(directory) / "fresh"
            resumed = TrainingMonitor(fresh, every_seconds=30)
            resumed.record_episode(episode(160))
            resumed.close(160)
            reader = EventAccumulator(str(fresh / "tensorboard")).Reload()
            self.assertEqual([p.step for p in reader.Scalars("train/return")], [160])

    def test_positive_interval_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            for interval in (0, -1, float("nan")):
                with self.subTest(interval=interval), self.assertRaisesRegex(ValueError, "must be positive"):
                    TrainingMonitor(directory, every_seconds=interval)
            self.assertFalse((Path(directory) / "tensorboard").exists())
