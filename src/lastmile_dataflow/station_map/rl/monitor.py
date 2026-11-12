"""The four requested SAC monitoring figures, backed by recorded metrics."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def training_curves(log_path, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    learners, episodes, evaluations = [], [], []
    with Path(log_path).open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["event"] == "learner" and "actor_loss" in row:
                learners.append(row)
            elif row["event"] == "episode" and not row["infrastructure_failure"]:
                episodes.append(row)
            elif row["event"] == "evaluation":
                evaluations.append(row)

    reward_breakdown(episodes, learners, output)
    figure, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for axis, key, title in zip(axes, ("actor_loss", "critic_loss"), ("Actor Loss", "Critic Loss")):
        axis.plot([r["transitions"] for r in learners], [r[key] for r in learners], marker=".", markersize=3)
        axis.set(title=title, xlabel="Valid transitions", ylabel="Mean loss per log interval")
        axis.grid(alpha=0.2)
    figure.savefig(output / "loss-curves.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6, 4), constrained_layout=True)
    rows = [r for r in learners if "entropy" in r]
    axis.plot([r["transitions"] for r in rows], [r["entropy"] for r in rows], marker=".", markersize=3)
    axis.set(title="Policy Entropy", xlabel="Valid transitions", ylabel="-E[log pi(a|s)] (nats)")
    axis.grid(alpha=0.2)
    figure.savefig(output / "exploration.png", dpi=150)
    plt.close(figure)

    figure, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    rows = [r for r in evaluations if r["evaluation_success_rate"] is not None]
    axes[0].plot([r["transitions"] for r in rows], [100 * r["evaluation_success_rate"] for r in rows], marker="o")
    fixed_station = rows and all(r.get("scope") == "fixed_station" for r in rows)
    title = "Fixed Station Success" if fixed_station else "Independent Validation Success"
    axes[0].set(
        title=title, xlabel="Valid transitions", ylabel="Success (%)", ylim=(-2, 102)
    )
    if episodes:
        grouped = {}
        for row in episodes:
            grouped.setdefault(row["transitions"], []).append(row["return"])
        axes[1].scatter([r["transitions"] for r in episodes], [r["return"] for r in episodes], s=5, alpha=0.12)
        axes[1].plot(
            list(grouped),
            [np.mean(v) for v in grouped.values()],
            marker=".",
            markersize=3,
            label="Mean of completed episodes",
        )
        axes[1].legend()
    axes[1].set(title="Episode Return", xlabel="Valid transitions", ylabel="Undiscounted reward sum")
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.savefig(output / "training-effect.png", dpi=150)
    plt.close(figure)


def reward_breakdown(episodes, learners, output):
    keys = ("position", "rotation", "close", "contact", "lift", "held", "collision")
    titles = (
        "Position Reward",
        "Rotation Reward",
        "Close Reward",
        "Contact Bonus Count",
        "Lift Progress Reward",
        "Held Reward",
        "Collision Penalty",
    )
    boundaries = sorted({r["transitions"] for r in learners})
    if episodes and (not boundaries or episodes[-1]["transitions"] > boundaries[-1]):
        boundaries.append(episodes[-1]["transitions"])
    figure, axes = plt.subplots(4, 2, figsize=(12, 12), constrained_layout=True)
    for axis, key, title in zip(axes.flat, keys, titles):
        points = []
        previous = -1
        for boundary in boundaries:
            rows = [r for r in episodes if previous < r["transitions"] <= boundary and "reward_component_names" in r]
            if rows:
                values = [
                    r["contact_bonus_count"]
                    if key == "contact"
                    else r["reward_components"][r["reward_component_names"].index(key)]
                    for r in rows
                ]
                points.append((boundary, np.mean(values)))
            previous = boundary
        if points:
            axis.plot(*zip(*points), marker=".")
        axis.set(title=title, xlabel="Valid transitions (training episodes)", ylabel="Episode mean")
        axis.grid(alpha=0.2)
    axes.flat[-1].set_visible(False)
    figure.savefig(Path(output) / "reward-breakdown.png", dpi=150)
    plt.close(figure)


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
