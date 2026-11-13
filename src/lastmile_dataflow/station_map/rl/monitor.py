"""Live TensorBoard training metrics and spatial evaluation figures."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from torch import Tensor
from torch.utils.tensorboard import SummaryWriter


DEFAULT_TENSORBOARD_EVERY_SECONDS = 30


@dataclass
class _Mean:
    total: float | Tensor = 0.0
    count: int = 0

    def add(self, value):
        self.total = self.total + (value.detach() if isinstance(value, Tensor) else value)
        self.count += 1


class TrainingMonitor:
    """Learner-owned interval statistics; tensors stay on device until flush."""

    def __init__(self, output, *, every_seconds, purge_step=None):
        if not every_seconds > 0:
            raise ValueError("training.tensorboard_every_seconds must be positive")
        self.writer = SummaryWriter(str(Path(output) / "tensorboard"), purge_step=purge_step)
        self.every_seconds = every_seconds
        self.last_flush = time.monotonic()
        self.means: dict[str, _Mean] = defaultdict(_Mean)
        self.latest: dict[str, float | Tensor] = {}

    def _mean(self, tag, value):
        self.means[tag].add(value)

    def record_update(self, losses):
        for key, tag in (
            ("actor_loss", "loss/actor"),
            ("critic_loss", "loss/critic"),
            ("alpha_loss", "loss/alpha"),
            ("entropy", "policy/entropy"),
        ):
            self._mean(tag, losses[key])
        self.latest["policy/alpha"] = losses["alpha"].detach()

    def record_episode(self, event):
        if event["infrastructure_failure"]:
            return
        group = "warmup" if event.get("random_warmup", False) else "train"
        for key in ("return", "steps", "success", "collision_free", "contact_bonus_count"):
            if key in event:
                self._mean(f"{group}/{key}", event[key])
        for name, value in zip(event.get("reward_component_names", ()), event.get("reward_components", ()), strict=True):
            self._mean(f"{group}/reward/{name}", value)

    def record_runtime(self, event):
        self.latest.update({
            f"runtime/{key}": value for key, value in event.items()
            if key not in ("event", "transitions")
        })

    def flush(self, transitions, *, force=False):
        now = time.monotonic()
        if not force and now - self.last_flush < self.every_seconds:
            return
        for tag, mean in self.means.items():
            self.writer.add_scalar(tag, float(mean.total / mean.count), transitions)
        for tag, value in self.latest.items():
            self.writer.add_scalar(tag, float(value), transitions)
        self.writer.flush()
        self.means.clear()
        self.latest.clear()
        self.last_flush = now

    def record_evaluation(self, event):
        prefix = f"evaluation/{event['scope']}"
        for key in (
            "evaluation_success_rate", "episode_return_mean", "healthy_trials", "total_trials",
            "successes", "infrastructure_failures", "value_success_correlation", "seconds",
        ):
            value = event.get(key)
            if value is not None:
                self.writer.add_scalar(f"{prefix}/{key}", value, event["transitions"])
        for policy, metrics in event.get("debug_stations", {}).items():
            for key, value in metrics.items():
                if value is not None:
                    self.writer.add_scalar(f"{prefix}/debug/{policy}/{key}", value, event["transitions"])
        self.writer.flush()

    def close(self, transitions):
        self.flush(transitions, force=True)
        self.writer.close()


def station_evaluation(scores, measured, path, *, q_limits, correlation=None):
    """Max-yaw raw Q map plus actual station-level rollout success scatter."""
    best, representatives = {}, {}
    for row in scores:
        representatives.setdefault(row["xy_id"], row)
        if row["valid"] and (row["xy_id"] not in best or row["q_mean"] > best[row["xy_id"]]["q_mean"]):
            best[row["xy_id"]] = row
    invalid = [row for key, row in representatives.items() if key not in best]
    valid = list(best.values())
    figure, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    axes[0].scatter([r["x"] for r in invalid], [r["y"] for r in invalid], color="gray", marker="x", label="Invalid XY")
    points = axes[0].scatter(
        [r["x"] for r in valid],
        [r["y"] for r in valid],
        c=[r["q_mean"] for r in valid],
        vmin=q_limits[0],
        vmax=q_limits[1],
        marker="s",
    )
    axes[0].set(title="Critic Value Map (max over yaw)", xlabel="World x (m)", ylabel="World y (m)", aspect="equal")
    axes[0].legend(loc="upper left", fontsize=8)
    figure.colorbar(points, ax=axes[0], label="Raw min-Q, not success probability")
    rows = [r for r in measured if r["success_rate"] is not None]
    axes[1].scatter([r["q_mean"] for r in rows], [r["success_rate"] for r in rows], s=35)
    rho = "N/A (constant or insufficient outcomes)" if correlation is None else f"{correlation:.3f}"
    axes[1].set(
        title=f"Value-Success Scatter\nSpearman = {rho}",
        xlabel="Mean online min-Q at station yaw",
        ylabel="Measured rollout success rate",
        ylim=(-0.05, 1.05),
    )
    axes[1].grid(alpha=0.2)
    figure.savefig(path, dpi=150)
    plt.close(figure)
