"""Frozen scores and held-out stochastic rollouts using the same environment."""

import json
from pathlib import Path

import numpy as np
import torch

from lastmile_dataflow.utils.figures import plt
from .station_q import action_sampled_q, select_acceptance_stations, summarize_trials
from .sac.agent import SAC
from .env.observation import REWARD_COMPONENTS
from .sac.agent import seed_all
from .env.observation import graph_observation
from .monitor import station_evaluation
from .capture import RolloutCapture
from .devices import VISIBLE_DEVICE


def reset_stations(env, station_ids, env_ids=None):
    env.task.requested_station_ids = torch.as_tensor(station_ids, dtype=torch.long, device=env.device)
    observations, _ = env.reset(env_ids=env_ids)
    return graph_observation(observations)


@torch.no_grad()
def station_scores(agent, env, samples, station_ids=None):
    rows = [{**s, "q_mean": None, "q_std": None, "K": samples} for s in env.task.stations]
    valid = [i for i, row in enumerate(rows) if row["valid"] and (station_ids is None or i in station_ids)]
    for start in range(0, len(valid), env.num_envs):
        batch = valid[start : start + env.num_envs]
        padded = batch + [batch[-1]] * (env.num_envs - len(batch))
        obs = reset_stations(env, padded)
        mean, std = action_sampled_q(agent, obs, samples)
        for offset, index in enumerate(batch):
            rows[index].update(q_mean=float(mean[offset]), q_std=float(std[offset]))
    return rows if station_ids is None else [rows[i] for i in station_ids]


@torch.no_grad()
def validation_rollout(agent, env, station_ids, seed, gamma, deterministic=False, capture=None):
    """One episode per vector slot; ended slots reset without entering results twice.

    Stage facts (closest pose, both fingers, lift, hold) locate the earliest
    failing phase; they do not replace the configured RL success protocol.
    """
    torch.manual_seed(seed)
    obs = reset_stations(env, station_ids)
    finished = torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
    task = env.task
    best_position = torch.full((env.num_envs,), float("inf"), device=env.device)
    best_rotation = torch.full_like(best_position, float("inf"))
    reached_pose = torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
    both_fingers = torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
    max_lift = torch.full_like(best_position, -float("inf"))
    max_hold = torch.zeros_like(best_position)
    stage_time = torch.zeros(env.num_envs, 4, device=env.device)
    component_sums = torch.zeros_like(task.reward_components)
    returns = torch.zeros(env.num_envs, device=env.device)
    discounted = torch.zeros_like(returns)
    discount = 1.0
    records = [None] * env.num_envs
    if capture is not None:
        capture.initialize(env, station_ids, seed, deterministic)
    while not finished.all():
        active = ~finished
        action, _ = agent.actor.sample(obs, deterministic=deterministic)
        action[finished] = 0
        observations, reward, terminated, truncated, _ = env.step(action)
        if capture is not None:
            capture.sample(env, active)
        position, rotation = (e.norm(dim=-1) for e in task.goal_error())
        reached_pose |= active & (position < 0.01) & (rotation < np.deg2rad(10))
        lift = env.sim.data.xpos[:, task.target, 2] - task.height_reference
        best_position = torch.where(active, torch.minimum(best_position, position), best_position)
        best_rotation = torch.where(active, torch.minimum(best_rotation, rotation), best_rotation)
        both_fingers |= active & (task.forces > 1e-6).all(-1)
        max_lift = torch.where(active, torch.maximum(max_lift, lift), max_lift)
        max_hold = torch.where(active, torch.maximum(max_hold, task.hold.float()), max_hold)
        both = (task.forces > 1e-6).all(-1)
        grasp = ~both & (position < 0.01) & (rotation < np.deg2rad(10))
        pre = ~both & ~grasp & (position < 0.04)
        approach = ~both & ~grasp & ~pre
        stage_time += torch.stack((approach, pre, grasp, both), -1) * active[:, None] * env.step_dt
        component_sums += task.reward_components * active[:, None]
        returns += torch.where(active, reward, 0.0)
        discounted += discount * torch.where(active, reward, 0.0)
        discount *= gamma
        done = terminated | truncated | env.task.unhealthy
        first_done = done & active
        for index in first_done.nonzero(as_tuple=True)[0].tolist():
            unhealthy = bool(env.task.unhealthy[index])
            records[index] = {
                "station_id": env.task.stations[station_ids[index]]["station_id"],
                "seed": seed,
                "success": bool(env.task.success[index]),
                "collision_free": bool(env.task.collision_free[index]),
                "infrastructure_failure": unhealthy,
                "return": None if unhealthy else float(discounted[index]),
                "episode_return": None if unhealthy else float(returns[index]),
                "collision_time_s": float(env.task.collision_time[index]),
                "max_drift_m": float(env.task.max_drift[index]),
                "max_yaw_drift_rad": float(env.task.max_yaw_drift[index]),
                "max_lift_drift_m": float(env.task.max_lift_drift[index]),
                "policy": "mean" if deterministic else "stochastic",
                "best_position_error_m": float(best_position[index]),
                "best_rotation_error_rad": float(best_rotation[index]),
                "reached_pose": bool(reached_pose[index]),
                "both_fingers": bool(both_fingers[index]),
                "max_lift_m": float(max_lift[index]),
                "max_hold_s": float(max_hold[index]),
                "failed": bool(task.failed[index]),
                "contact_bonus_count": int(task.contact_bonus_paid[index]),
                "stage_seconds": dict(
                    zip(("Approach", "Pre-grasp", "Grasp", "LIFT"), stage_time[index].cpu().tolist())
                ),
                "reward_components": component_sums[index].cpu().tolist(),
                "reward_component_names": list(REWARD_COMPONENTS),
            }
        finished |= first_done
        obs = graph_observation(observations).clone()
        if done.any() and not finished.all():
            ids = done.nonzero(as_tuple=True)[0]
            reset_obs = reset_stations(env, [station_ids[i] for i in ids.tolist()], env_ids=ids)
            obs.replace(ids, reset_obs)
    if capture is not None:
        capture.finish(env, records)
    return records


def debug_station_rollouts(agent, env, settings, seed):
    """Training-station check for debug pools: same stations, stochastic and mean actions."""
    names = settings.get("debug_station_ids") or []
    if not names:
        return None
    lookup = {row["station_id"]: i for i, row in enumerate(env.task.stations)}
    ids = [lookup[name] for name in names]
    slots = [ids[i % len(ids)] for i in range(env.num_envs)]
    return {
        policy: validation_rollout(agent, env, slots, seed + 1000, 1.0, deterministic=policy == "mean")
        for policy in ("stochastic", "mean")
    }


def stage_summary(rows):
    healthy = [r for r in rows if not r["infrastructure_failure"]]
    if not healthy:
        return {"healthy": 0}
    near = [r["reached_pose"] for r in healthy]
    return {
        "healthy": len(healthy),
        "success": sum(r["success"] for r in healthy),
        "collision_free_success": sum(r["success"] and r["collision_free"] for r in healthy),
        "reached_pose": sum(near),
        "both_fingers": sum(r["both_fingers"] for r in healthy),
        "lift_1cm": sum(r["max_lift_m"] >= 0.01 for r in healthy),
        "held_1s": sum(r["max_hold_s"] >= 1.0 - 1e-9 for r in healthy),
        "best_position_error_m_median": float(np.median([r["best_position_error_m"] for r in healthy])),
        "max_hold_s": max(r["max_hold_s"] for r in healthy),
    }


def monitor_validation(agent, env, config, transitions, output, force_video=False):
    """Fixed validation points/seeds and frozen online Q throughout evaluation."""

    output = Path(output)
    analysis, views = output / "analysis", output / "views"
    analysis.mkdir(exist_ok=True)
    views.mkdir(exist_ok=True)
    settings = config["training"]
    seed = config["evaluation"]["seed"]
    candidates = [i for i, row in enumerate(env.task.stations) if row["valid"] and row["split"] == "validation"]
    station_ids = np.random.default_rng(seed).choice(candidates, env.num_envs, replace=False).tolist()
    selection = [{**env.task.stations[i], "group": "validation"} for i in station_ids]
    (analysis / "validation-selection.json").write_text(
        json.dumps(
            {"stations": selection, "seed": seed, "rollouts_per_station": settings["validation_rollouts_per_station"]},
            indent=2,
        )
    )
    video = config["video"]
    previous = getattr(env, "_video_transitions", -video["every"])
    record_video = (
        force_video
        or transitions == 0
        or transitions - previous >= video["every"]
        or transitions >= config["training"]["max_transitions"]
    )
    captures = []
    with torch.random.fork_rng(devices=[torch.device(env.device).index]):
        torch.manual_seed(seed)
        rows = station_scores(agent, env, config["evaluation"]["action_samples"], station_ids)
        values = {r["station_id"]: r for r in rows}
        selection = [{**row, "q_mean": values[row["station_id"]]["q_mean"]} for row in selection]
        trials = []
        for repeat in range(settings["validation_rollouts_per_station"]):
            capture = None
            if record_video:
                capture = RolloutCapture(output, transitions, config, representatives=True)
                captures.append(capture)
            trials.extend(
                validation_rollout(agent, env, station_ids, seed + repeat, config["sac"]["gamma"], capture=capture)
            )
        if record_video:
            training_id = int(env.task.pool[0])
            fixed = [training_id, station_ids[0]]
            for deterministic in (False, True):
                capture = RolloutCapture(output, transitions, config)
                captures.append(capture)
                slots = [fixed[i % 2] for i in range(env.num_envs)]
                # A one-slot evaluation still records both splits.
                for selected in [slots] if env.num_envs > 1 else [[i] for i in fixed]:
                    validation_rollout(
                        agent, env, selected, seed, config["sac"]["gamma"], deterministic=deterministic, capture=capture
                    )
            env._video_transitions = transitions
        debug = debug_station_rollouts(agent, env, settings, seed)
    summary = summarize_trials(selection, trials, seed=seed)
    healthy = [row for row in trials if not row["infrastructure_failure"]]
    event = {
        "event": "evaluation",
        "transitions": transitions,
        "split": "validation",
        "evaluation_success_rate": float(np.mean([r["success"] for r in healthy])) if healthy else None,
        "successes": sum(r["success"] for r in healthy),
        "healthy_trials": len(healthy),
        "total_trials": len(trials),
        "infrastructure_failures": len(trials) - len(healthy),
        "videos": [row for capture in captures for row in capture.videos],
        "success_protocol": config["success"],
        "episode_return_mean": float(np.mean([r["episode_return"] for r in healthy])) if healthy else None,
        "value_success_correlation": summary["q_success_spearman"],
    }
    if debug:
        event["debug_stations"] = {policy: stage_summary(rows_) for policy, rows_ in debug.items()}
    (analysis / f"validation-{transitions:012d}.json").write_text(
        json.dumps(
            {
                "metrics": event,
                "stations": summary["stations"],
                "trials": trials,
                "q_scores": rows,
                "debug_trials": debug,
            },
            indent=2,
        )
    )
    station_evaluation(
        rows,
        summary["stations"],
        views / "station-evaluation.png",
        q_limits=config["evaluation"]["q_color_limits"],
        correlation=summary["q_success_spearman"],
    )
    agent.actor.train()
    agent.q1.train()
    agent.q2.train()
    return event


def score(config, *, checkpoint, gpu, output, seed=None, num_envs=32):
    """``gpu`` is the physical card the caller bound with select_devices."""
    from .env.environment import StationEnv

    seed = config["evaluation"]["seed"] if seed is None else seed
    seed_all(seed)
    agent, saved = SAC.load(checkpoint, f"cuda:{VISIBLE_DEVICE}")
    if config != saved["metadata"]["config"]:
        raise ValueError("Scoring requires the checkpoint effective configuration")
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=num_envs)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        rows = station_scores(agent, env, config["evaluation"]["action_samples"])
        result = {
            "checkpoint": str(Path(checkpoint).absolute()),
            "seed": seed,
            "physical_gpu": gpu,
            "interpretation": "raw E[min online Q]; K is action samples, not rollouts",
            "legal_coverage": sum(r["valid"] for r in rows) / len(rows),
            "stations": rows,
        }
        (output / "station-q.json").write_text(json.dumps(result, indent=2))
        plot_scores(rows, output / "station-q.png", config["evaluation"]["q_color_limits"])
        return result
    finally:
        env.close()


def plot_scores(rows, path, q_limits):
    legal = [r for r in rows if r["valid"]]
    if not legal:
        return
    # World yaw differs per XY; the readable slice is the target-facing offset 0.
    slice_rows = [r for r in rows if r["yaw_offset_rad"] is None or abs(r["yaw_offset_rad"]) < 1e-9]
    best = {}
    for row in legal:
        if row["xy_id"] not in best or row["q_mean"] > best[row["xy_id"]]["q_mean"]:
            best[row["xy_id"]] = row
    (Path(path).parent / "best-yaw.json").write_text(json.dumps(list(best.values()), indent=2))
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    for axis, subset, title in zip(
        axes, (slice_rows, list(best.values())), ("facing target (offset 0)", "max over heading")
    ):
        invalid = [r for r in subset if not r["valid"]]
        valid = [r for r in subset if r["valid"]]
        axis.scatter([r["x"] for r in invalid], [r["y"] for r in invalid], c="gray", marker="x", label="invalid")
        points = axis.scatter(
            [r["x"] for r in valid],
            [r["y"] for r in valid],
            c=[r["q_mean"] for r in valid],
            vmin=q_limits[0],
            vmax=q_limits[1],
            marker="s",
        )
        axis.set(title=title, xlabel="world x (m)", ylabel="world y (m)", aspect="equal")
        fig.colorbar(points, ax=axis, label="raw min-Q (not success probability)")
    fig.savefig(path, dpi=160)
    plt.close(fig)


def evaluate(config, *, checkpoint, scores, gpu, output, seed=None, baseline=False):
    """``gpu`` is the physical card the caller bound with select_devices."""
    from .env.environment import StationEnv

    seed = config["evaluation"]["seed"] if seed is None else seed
    seed_all(seed)
    agent, saved = SAC.load(checkpoint, f"cuda:{VISIBLE_DEVICE}")
    if config != saved["metadata"]["config"]:
        raise ValueError("Evaluation requires the checkpoint effective configuration")
    scored = json.loads(Path(scores).read_text())
    if baseline and saved["transitions"] != 0:
        raise ValueError("The initialization baseline must be an untrained checkpoint")
    if not baseline and str(Path(checkpoint).absolute()) != scored["checkpoint"]:
        raise ValueError("Scores must reference the evaluated checkpoint")
    selection = select_acceptance_stations(scored["stations"], config["evaluation"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    trial_plan = [
        {**row, "seed": seed + i * config["evaluation"]["rollouts_per_station"] + trial, "trial": trial}
        for i, row in enumerate(selection)
        for trial in range(config["evaluation"]["rollouts_per_station"])
    ]
    (output / "selection.json").write_text(json.dumps(trial_plan, indent=2))
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    trials = []
    try:
        lookup = {row["station_id"]: i for i, row in enumerate(env.task.stations)}
        with (output / "trials.jsonl").open("w") as log:
            for row in trial_plan:
                seed_all(row["seed"])
                obs = reset_stations(env, [lookup[row["station_id"]]])
                states, velocities, controls, actions = [], [], [], []
                episode_return = 0.0
                discount = 1.0
                done = False
                while not done:
                    with torch.no_grad():
                        action, _ = agent.actor.sample(obs)
                    states.append(env.sim.data.qpos[0].cpu().numpy().copy())
                    velocities.append(env.sim.data.qvel[0].cpu().numpy().copy())
                    actions.append(action[0].cpu().numpy().copy())
                    observations, reward, terminated, truncated, _ = env.step(action)
                    controls.append(env.sim.data.ctrl[0].cpu().numpy().copy())
                    obs = graph_observation(observations)
                    episode_return += discount * float(reward[0])
                    discount *= config["sac"]["gamma"]
                    done = bool((terminated | truncated)[0])
                states.append(env.sim.data.qpos[0].cpu().numpy().copy())
                velocities.append(env.sim.data.qvel[0].cpu().numpy().copy())
                name = f"{row['station_id'].lower()}-t{row['trial']:02d}.npz"
                np.savez_compressed(
                    output / name,
                    qpos=np.array(states),
                    qvel=np.array(velocities),
                    ctrl=np.array(controls),
                    action=np.array(actions),
                )
                result = {
                    "station_id": row["station_id"],
                    "seed": row["seed"],
                    "trial": row["trial"],
                    "success": bool(env.task.success[0]),
                    "collision_free": bool(env.task.collision_free[0]),
                    "collision_time_s": float(env.task.collision_time[0]),
                    "max_drift_m": float(env.task.max_drift[0]),
                    "max_yaw_drift_rad": float(env.task.max_yaw_drift[0]),
                    "max_lift_drift_m": float(env.task.max_lift_drift[0]),
                    "return": episode_return,
                    "infrastructure_failure": bool(env.task.unhealthy[0]),
                    "reason": (
                        "infrastructure_failure"
                        if env.task.unhealthy[0]
                        else "strict_pick"
                        if env.task.success[0]
                        else "fixed_drift_or_fall"
                        if env.task.failed[0]
                        else "timeout"
                    ),
                    "replay": name,
                }
                trials.append(result)
                log.write(json.dumps(result) + "\n")
                log.flush()
        summary = summarize_trials(selection, trials, seed=seed)
        summary["policy"] = "initialization_baseline" if baseline else "trained_stochastic_actor"
        summary["checkpoint"] = str(Path(checkpoint).absolute())
        summary["physical_gpu"] = gpu
        (output / "summary.json").write_text(json.dumps(summary, indent=2))

        station_evaluation(
            scored["stations"],
            summary["stations"],
            output / "station-evaluation.png",
            q_limits=config["evaluation"]["q_color_limits"],
            correlation=summary["q_success_spearman"],
        )
        return summary
    finally:
        env.close()
