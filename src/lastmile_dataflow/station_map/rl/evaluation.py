"""Frozen scores and training-station rollouts using the same environment."""

import json
import time
from functools import partial
from pathlib import Path

import numpy as np
import torch

from lastmile_dataflow.utils.figures import plt
from .station_q import action_sampled_q, select_acceptance_stations, summarize_trials, score_key
from .config import resolve_config
from .checkpoint import load_agent
from .env.observation import REWARD_COMPONENTS, CONTACT_REWARD_COMPONENTS
from .checkpoint import seed_all
from .env.observation import graph_observation, policy_observation
from .monitor import station_evaluation
from .capture import RolloutCapture, EpisodeReplays
from .soft_value import plot_values
from .devices import VISIBLE_DEVICE
from .env.terms import finger_contact, target_lift_height
from .env.terms import mobile_episode, grasp_episode, collision_episode


def reset_stations(env, station_ids, env_ids=None, *, mode="graph"):
    env.task.requested_station_ids = torch.as_tensor(station_ids, dtype=torch.long, device=env.device)
    observations, _ = env.reset(env_ids=env_ids)
    return graph_observation(observations) if mode == "graph" else policy_observation(observations, mode)


@torch.no_grad()
def station_scores(agent, env, samples, station_ids=None, *, initial_scores=()):
    kind = "v" if getattr(agent, "algorithm", "sac") == "ppo" else "q"
    mean_key, std_key = f"{kind}_mean", f"{kind}_std"
    rows = [{**s, mean_key: None, std_key: None, **({"K": samples} if kind == "q" else {})}
            for s in env.task.stations]
    cached = {row["station_id"]: row for row in initial_scores}
    rows = [cached.get(row["station_id"], row) for row in rows]
    valid = [i for i, row in enumerate(rows) if row["valid"] and row["station_id"] not in cached
             and (station_ids is None or i in station_ids)]
    for index in valid:
        grasp_scores = []
        pool = env.task.grasp_pools[index]
        for start in range(0, len(pool), env.num_envs):
            batch = pool[start:start + env.num_envs]
            padded = batch + [batch[-1]] * (env.num_envs - len(batch))
            env.task.requested_grasp_ids = torch.tensor(padded, device=env.device)
            obs = reset_stations(env, [index] * env.num_envs, mode=getattr(agent, "mode", "graph"))
            if kind == "v":
                mean = agent.value(obs)
                std = torch.zeros_like(mean)
            else:
                mean, std = action_sampled_q(agent, obs, samples)
            for offset, grasp_id in enumerate(batch):
                candidate = env.task.grasps[grasp_id]
                grasp_scores.append({"source": candidate["source"], "row": candidate["row"], mean_key: float(mean[offset]), std_key: float(std[offset])})
        average = float(np.mean([g[mean_key] for g in grasp_scores]))
        second = np.mean([g[std_key] ** 2 + g[mean_key] ** 2 for g in grasp_scores])
        rows[index].update({mean_key: average, std_key: float(np.sqrt(max(0, second - average ** 2))), "grasps": grasp_scores})
    return rows if station_ids is None else [rows[i] for i in station_ids]


def episode_generators(seeds, device):
    return [torch.Generator(device=device).manual_seed(seed) for seed in seeds]


def episode_noise(generators, action_dim, active):
    noise = torch.zeros(len(generators), action_dim, device=active.device)
    for index in active.nonzero(as_tuple=True)[0].tolist():
        noise[index] = torch.randn(action_dim, generator=generators[index], device=active.device)
    return noise


def request_episode_grasps(task, station_ids, generators):
    """Uniform grasp selection tied to each trial, independent of batch layout."""
    grasps = []
    for station, generator in zip(station_ids, generators):
        pool = task.grasp_pools[station]
        offset = int(torch.rand((), generator=generator, device=task.device) * len(pool))
        grasps.append(pool[offset])
    task.requested_grasp_ids = torch.tensor(grasps, device=task.device)


@torch.no_grad()
def validation_rollout(
    agent, env, station_ids, seed, gamma, deterministic=False, capture=None, *,
    on_progress=None, trial_seeds=None, on_trial=None, replay=None,
):
    """One episode per vector slot; ended slots reset without entering results twice.

    Stage facts (closest pose, both fingers, lift, hold) locate the earliest
    failing phase; they do not replace the configured RL success protocol.
    """
    torch.manual_seed(seed)
    generators = episode_generators(trial_seeds, env.device) if trial_seeds is not None else None
    mode = getattr(agent, "mode", "graph")
    if generators is not None and hasattr(env.task, "grasp_pool_table"):
        request_episode_grasps(env.task, station_ids, generators)
    obs = reset_stations(env, station_ids, mode=mode)
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
    steps = 0
    if capture is not None:
        capture.initialize(env, station_ids, seed, deterministic)
    if replay is not None:
        replay.initialize(env)
    while not finished.all():
        active = ~finished
        sampling = {} if generators is None else {"noise": episode_noise(generators, getattr(agent.actor, "action_dim", 7), active)}
        action, _ = agent.actor.sample(obs, deterministic=deterministic, **sampling)
        action[finished] = 0
        observations, reward, terminated, truncated, _ = env.step(action)
        if replay is not None:
            replay.sample(env, action)
        if capture is not None:
            capture.sample(env, active)
        position, rotation = (e.norm(dim=-1) for e in task.goal_error())
        reached_pose |= active & (position < 0.01) & (rotation < np.deg2rad(10))
        lift = target_lift_height(env)
        best_position = torch.where(active, torch.minimum(best_position, position), best_position)
        best_rotation = torch.where(active, torch.minimum(best_rotation, rotation), best_rotation)
        both_fingers |= active & finger_contact(env)
        max_lift = torch.where(active, torch.maximum(max_lift, lift), max_lift)
        max_hold = torch.where(active, torch.maximum(max_hold, task.hold.float()), max_hold)
        both = finger_contact(env)
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
        ended = first_done.nonzero(as_tuple=True)[0].tolist()
        for index in ended:
            unhealthy = bool(env.task.unhealthy[index])
            records[index] = {
                "station_id": env.task.stations[station_ids[index]]["station_id"],
                "seed": seed if trial_seeds is None else trial_seeds[index],
                "steps": steps + 1,
                "success": bool(env.task.success[index]) and not unhealthy,
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
                **mobile_episode(task, index),
                **grasp_episode(task, index),
                **collision_episode(task, index),
                "contact_bonus_count": int(task.contact_bonus_paid[index]),
                "stage_seconds": dict(
                    zip(("Approach", "Pre-grasp", "Grasp", "LIFT"), stage_time[index].cpu().tolist())
                ),
                "reward_components": component_sums[index].cpu().tolist(),
                "reward_component_names": list(CONTACT_REWARD_COMPONENTS if mode == "flat" else REWARD_COMPONENTS),
            }
            if mode == "flat":
                first_contact = float(task.first_contact_time_s[index])
                records[index]["first_simultaneous_contact_s"] = first_contact if first_contact >= 0 else None
            if on_trial is not None:
                on_trial(index, records[index])
        finished |= first_done
        steps += 1
        if on_progress is not None and (steps % 100 == 0 or ended):
            on_progress(finished, steps)
        obs = policy_observation(observations, getattr(agent, "mode", "graph")).clone()
        if done.any() and not finished.all():
            ids = done.nonzero(as_tuple=True)[0]
            reset_obs = reset_stations(env, [station_ids[i] for i in ids.tolist()], env_ids=ids,
                                       mode=getattr(agent, "mode", "graph"))
            if isinstance(obs, torch.Tensor):
                obs[ids] = reset_obs[ids] if len(reset_obs) == env.num_envs else reset_obs
            else:
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


def evaluation_progress(path, transitions, stage, timings, completed=0, total=0):
    path.write_text(json.dumps({"stage": stage, "completed_trials": completed,
                               "total_trials": total, "seconds": timings}, indent=2))
    print(f"Evaluation {transitions}: {stage} {completed}/{total}", flush=True)


def rollout_progress(finished, steps, *, path, transitions, timings, offset, count, total):
    completed = offset + int(finished[:count].sum())
    path.write_text(json.dumps({"stage": "rollouts", "completed_trials": completed,
                               "total_trials": total, "vector_steps": steps, "seconds": timings}, indent=2))
    print(f"Evaluation {transitions}: rollouts {completed}/{total}, vector step {steps}", flush=True)


def monitor_validation(agent, env, config, transitions, output, force_video=False, *, return_scores=False):
    """Fixed validation points/seeds and frozen online Q throughout evaluation."""

    output = Path(output)
    analysis, views = output / "analysis", output / "views"
    analysis.mkdir(exist_ok=True)
    views.mkdir(exist_ok=True)
    kind = "v" if agent.algorithm == "ppo" else "q"
    key = f"{kind}_mean"
    settings = config["training"]
    seed = config["evaluation"]["seed"]
    fixed_station = config["stations"].get("fixed_station")
    scope = "fixed_station" if fixed_station else "train"
    if fixed_station:
        station_ids = [int(env.task.pool[0])]
    else:
        candidates = [i for i, row in enumerate(env.task.stations) if row["valid"]]
        station_ids = np.random.default_rng(seed).choice(candidates, settings["validation_stations"], replace=len(candidates) < settings["validation_stations"]).tolist()
    selection = [{**env.task.stations[i], "group": scope} for i in dict.fromkeys(station_ids)]
    (analysis / "validation-selection.json").write_text(
        json.dumps(
            {
                "stations": selection,
                "seed": seed,
                "scope": scope,
                "rollouts_per_station": settings["validation_rollouts_per_station"],
            },
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
    timings = {}
    progress = analysis / f"evaluation-progress-{transitions:012d}.json"

    with torch.random.fork_rng(devices=[torch.device(env.device).index] if torch.device(env.device).type == "cuda" else []):
        torch.manual_seed(seed)
        evaluation_progress(progress, transitions, f"initial_{kind}", timings)
        started = time.perf_counter()
        rows = station_scores(agent, env, config["evaluation"]["action_samples"], list(dict.fromkeys(station_ids)))
        timings[f"initial_{kind}"] = time.perf_counter() - started
        values = {r["station_id"]: r for r in rows}
        selection = [{**row, key: values[row["station_id"]][key]} for row in selection]
        trials = []
        requested = station_ids * settings["validation_rollouts_per_station"]
        started = time.perf_counter()
        for start in range(0, len(requested), env.num_envs):
            batch = requested[start:start + env.num_envs]
            slots = batch + [batch[-1]] * (env.num_envs - len(batch))
            evaluation_progress(progress, transitions, "rollouts", timings, len(trials), len(requested))
            on_progress = partial(rollout_progress, path=progress, transitions=transitions,
                                  timings=timings, offset=start, count=len(batch), total=len(requested))
            result = validation_rollout(agent, env, slots, seed + start, agent.config.gamma,
                                        on_progress=on_progress)
            trials.extend({**row, "trial": start + i} for i, row in enumerate(result[:len(batch)]))
        timings["rollouts"] = time.perf_counter() - started
        evaluation_progress(progress, transitions, "videos", timings, len(trials), len(requested))
        started = time.perf_counter()
        if record_video:
            training_id = int(env.task.pool[0])
            fixed = list(dict.fromkeys([training_id, station_ids[0]]))
            for deterministic in (False, True):
                slots = [fixed[i % len(fixed)] for i in range(env.num_envs)]
                selected_slots = [slots.index(i) for i in dict.fromkeys(slots)]
                capture = RolloutCapture(output, transitions, config, selected_slots=selected_slots)
                captures.append(capture)
                # A one-slot evaluation still records both splits.
                for selected in [slots] if env.num_envs > 1 else [[i] for i in fixed]:
                    validation_rollout(
                        agent, env, selected, seed, agent.config.gamma, deterministic=deterministic, capture=capture
                    )
            env._video_transitions = transitions
        timings["videos"] = time.perf_counter() - started
        debug = debug_station_rollouts(agent, env, settings, seed)
    summary = summarize_trials(selection, trials, seed=seed)
    healthy = [row for row in trials if not row["infrastructure_failure"]]
    event = {
        "stage_seconds": timings,
        "event": "evaluation",
        "transitions": transitions,
        "split": "train",
        "scope": scope,
        "evaluation_success_rate": float(np.mean([r["success"] for r in healthy])) if healthy else None,
        "successes": sum(r["success"] for r in healthy),
        "healthy_trials": len(healthy),
        "total_trials": len(trials),
        "infrastructure_failures": len(trials) - len(healthy),
        "videos": [row for capture in captures for row in capture.videos],
        "success_protocol": config["success"],
        "episode_return_mean": float(np.mean([r["episode_return"] for r in healthy])) if healthy else None,
        "value_success_correlation": summary[f"{kind}_success_spearman"],
    }
    if debug:
        event["debug_stations"] = {policy: stage_summary(rows_) for policy, rows_ in debug.items()}
    (analysis / f"validation-{transitions:012d}.json").write_text(
        json.dumps(
            {
                "metrics": event,
                "stations": summary["stations"],
                "trials": trials,
                f"{kind}_scores": rows,
                "debug_trials": debug,
            },
            indent=2,
        )
    )
    station_evaluation(
        rows,
        summary["stations"],
        views / "station-evaluation.png",
        q_limits=config["evaluation"].get(f"{kind}_color_limits"),
        correlation=summary[f"{kind}_success_spearman"],
    )
    agent.actor.train()
    if agent.algorithm == "ppo":
        agent.value.train()
    else:
        agent.q1.train()
        agent.q2.train()
    evaluation_progress(progress, transitions, "complete", timings, len(trials), len(requested))
    return (event, rows) if return_scores else event


def score(config, *, checkpoint, gpu, output, seed=None, num_envs=32):
    """``gpu`` is the physical card the caller bound with select_devices."""
    from .env.environment import StationEnv

    config = resolve_config(config)
    seed = config["evaluation"]["seed"] if seed is None else seed
    seed_all(seed)
    agent, saved = load_agent(checkpoint, f"cuda:{VISIBLE_DEVICE}")
    if config != saved["metadata"]["config"]:
        raise ValueError("Scoring requires the checkpoint effective configuration")
    if agent.mode == "flat" and agent.algorithm != "ppo":
        raise ValueError("Flat SAC checkpoints use score.py --value-mode soft; flat PPO is scored by V")
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=num_envs)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        kind = "v" if agent.algorithm == "ppo" else "q"
        rows = station_scores(agent, env, config["evaluation"]["action_samples"])
        result = {
            "checkpoint": str(Path(checkpoint).absolute()),
            "seed": seed,
            "physical_gpu": gpu,
            "score_kind": kind,
            "interpretation": ("equal grasp mean of state value V" if kind == "v" else
                               "mean over grasps of E[min online Q]; K is action samples per grasp"),
            "legal_coverage": sum(r["valid"] for r in rows) / len(rows),
            "stations": rows,
        }
        (output / f"station-{kind}.json").write_text(json.dumps(result, indent=2))
        if agent.mode == "flat":
            # Same per-station map as the SAC soft-value figure; the target is static across resets.
            plot_values(rows, env.task.poses()[4][0, :2].tolist(), output / f"station-{kind}.png",
                        title=f"PPO initial state value V | {saved['transitions']:,} transitions | "
                              f"mean over {len(env.task.grasp_pools[0])} grasps",
                        label="mean V(s0) over grasp candidates (not success probability)")
        else:
            plot_scores(rows, output / f"station-{kind}.png", config["evaluation"].get(f"{kind}_color_limits"))
        return result
    finally:
        env.close()


def plot_scores(rows, path, q_limits):
    key = score_key(rows)
    label = "state value V" if key == "v_mean" else "min-Q"
    limits = q_limits or (None, None)
    legal = [r for r in rows if r["valid"]]
    if not legal:
        return
    # World yaw differs per XY; the readable slice is the target-facing offset 0.
    slice_rows = [r for r in rows if r["yaw_offset_rad"] is None or abs(r["yaw_offset_rad"]) < 1e-9]
    best = {}
    for row in legal:
        if row["xy_id"] not in best or row[key] > best[row["xy_id"]][key]:
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
            c=[r[key] for r in valid],
            vmin=limits[0],
            vmax=limits[1],
            marker="s",
        )
        axis.set(title=title, xlabel="world x (m)", ylabel="world y (m)", aspect="equal")
        fig.colorbar(points, ax=axis, label=f"raw {label} (not success probability)")
    fig.savefig(path, dpi=160)
    plt.close(fig)


def evaluate(config, *, checkpoint, scores, gpu, output, seed=None, baseline=False, num_envs=20):
    """``gpu`` is the physical card the caller bound with select_devices."""
    from .env.environment import StationEnv

    config = resolve_config(config)
    seed = config["evaluation"]["seed"] if seed is None else seed
    seed_all(seed)
    agent, saved = load_agent(checkpoint, f"cuda:{VISIBLE_DEVICE}")
    if config != saved["metadata"]["config"]:
        raise ValueError("Evaluation requires the checkpoint effective configuration")
    if agent.mode == "flat":
        return evaluate_flat(config, agent, checkpoint, gpu, output, seed, num_envs=num_envs)
    if scores is None:
        raise ValueError("Graph evaluation requires station scores")
    scored = json.loads(Path(scores).read_text())
    kind = "v" if agent.algorithm == "ppo" else "q"
    if scored.get("score_kind", "q") != kind:
        raise ValueError("Station scores must match the checkpoint algorithm")
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
    if num_envs < 1:
        raise ValueError("num_envs must be positive")
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=min(num_envs, len(trial_plan)))
    try:
        trials, _ = run_evaluation_trials(agent, env, trial_plan, agent.config.gamma, output)
        summary = summarize_trials(selection, trials, seed=seed)
        summary["policy"] = "initialization_baseline" if baseline else "trained_stochastic_actor"
        summary["checkpoint"] = str(Path(checkpoint).absolute())
        summary["physical_gpu"] = gpu
        summary["num_envs"] = env.num_envs
        summary["rng"] = "per_trial_generator"
        (output / "summary.json").write_text(json.dumps(summary, indent=2))

        station_evaluation(
            scored["stations"],
            summary["stations"],
            output / "station-evaluation.png",
            q_limits=config["evaluation"].get(f"{kind}_color_limits"),
            correlation=summary[f"{kind}_success_spearman"],
        )
        return summary
    finally:
        env.close()


def record_evaluation_trial(index, result, *, plan, log, trials, replay, output):
    if index >= len(plan):
        return
    row = plan[index]
    result["trial"] = row["trial"]
    if "first_simultaneous_contact_s" in result:
        result["reason"] = (
            "infrastructure_failure" if result["infrastructure_failure"] else
            "simultaneous_contact" if result["success"] else
            "out_of_bounds" if result.get("out_of_bounds", False) else
            "collision_failure" if not result["collision_free"] else
            "control_failure" if result["failed"] else "timeout"
        )
    else:
        result["reason"] = (
            "infrastructure_failure" if result["infrastructure_failure"] else
            "strict_pick" if result["success"] else
            "fixed_drift_or_fall" if result["failed"] else "timeout"
        )
    if replay is not None:
        name = f"{row['station_id'].lower()}-t{row['trial']:02d}.npz"
        replay.save(index, result["steps"], output / name)
        result["replay"] = name
    trials[index] = result
    log.write(json.dumps(result) + "\n")
    log.flush()


def run_evaluation_trials(agent, env, plan, gamma, output, *, capture_all=False):
    """Bounded batches share the validation runner and persist each terminal result."""
    lookup = {row["station_id"]: i for i, row in enumerate(env.task.stations)}
    trials, videos = [], []
    timings = {}
    started = time.perf_counter()
    progress = output / "progress.json"
    with (output / "trials.jsonl").open("w") as log:
        for start in range(0, len(plan), env.num_envs):
            batch = plan[start:start + env.num_envs]
            padded = batch + [batch[-1]] * (env.num_envs - len(batch))
            replay = None if capture_all else EpisodeReplays()
            capture = None
            if capture_all:
                capture = RolloutCapture(output, agent.updates, env.research_config,
                                         selected_slots=list(range(len(batch))),
                                         trial_ids=[r["trial"] for r in padded],
                                         trial_seeds=[r["seed"] for r in padded])
            rows = [None] * len(batch)
            on_trial = partial(record_evaluation_trial, plan=batch, log=log, trials=rows,
                               replay=replay, output=output)
            on_progress = partial(rollout_progress, path=progress, transitions=agent.updates,
                                  timings=timings, offset=start, count=len(batch), total=len(plan))
            validation_rollout(agent, env, [lookup[r["station_id"]] for r in padded],
                               padded[0]["seed"], gamma, trial_seeds=[r["seed"] for r in padded],
                               on_trial=on_trial, replay=replay, capture=capture, on_progress=on_progress)
            trials.extend(rows)
            if capture is not None:
                videos.extend(capture.videos)
            print(f"Evaluation: completed {len(trials)}/{len(plan)} trials", flush=True)
    timings["rollouts_and_videos"] = time.perf_counter() - started
    evaluation_progress(progress, agent.updates, "complete", timings, len(trials), len(plan))
    return trials, videos


def flat_trial_plan(task, settings, seed):
    """Freeze station selection separately from per-trial action randomness."""
    names = [task.stations[int(i)]["station_id"] for i in task.pool]
    count = settings.get("station_count")
    if count is None:
        selected, repeats = names[:1], 20
    else:
        if count < 1 or settings["rollouts_per_station"] < 1:
            raise ValueError("Evaluation station_count and rollouts_per_station must be positive")
        selected = np.random.default_rng(seed).choice(names, min(count, len(names)), replace=False).tolist()
        repeats = settings["rollouts_per_station"]
    return [
        dict(station_id=name, seed=seed + index * repeats + trial, trial=index * repeats + trial)
        for index, name in enumerate(selected) for trial in range(repeats)
    ]


def flat_trial_summary(trials):
    healthy = [row for row in trials if not row["infrastructure_failure"]]
    successes = sum(row["success"] for row in healthy)
    collision_free_successes = sum(row["success"] and row["collision_free"] for row in healthy)
    return {
        "total_trials": len(trials),
        "healthy_trials": len(healthy),
        "infrastructure_failures": len(trials) - len(healthy),
        "successes": successes,
        "success_rate": successes / len(healthy) if healthy else None,
        "collision_free_successes": collision_free_successes,
        "collision_free_success_rate": collision_free_successes / len(healthy) if healthy else None,
        "first_contact_times_s": [r["first_simultaneous_contact_s"] for r in healthy if r["success"]],
        "failure_reason_counts": {
            reason: sum(r["reason"] == reason for r in trials)
            for reason in ("infrastructure_failure", "collision_failure", "control_failure", "timeout")
        },
    }


def evaluate_flat(config, agent, checkpoint, gpu, output, seed, *, num_envs):
    """Evaluate a frozen, seeded station plan and capture every trial."""
    from .env.environment import StationEnv

    if num_envs < 1:
        raise ValueError("num_envs must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "views").mkdir(exist_ok=True)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=min(num_envs, 20))
    try:
        plan = flat_trial_plan(env.task, config.get("evaluation", {}), seed)
        names = list(dict.fromkeys(row["station_id"] for row in plan))
        (output / "selection.json").write_text(json.dumps(plan, indent=2))
        trials, videos = run_evaluation_trials(agent, env, plan, agent.config.gamma, output, capture_all=True)
        summary = {
            "checkpoint": str(Path(checkpoint).absolute()),
            "seed": seed,
            "physical_gpu": gpu,
            "num_envs": env.num_envs,
            "rng": "per_trial_generator",
            "policy": "stochastic_flat_contact",
            "success_protocol": config["success"],
            **({"station_id": names[0]} if len(names) == 1 else {}),
            "station_ids": names,
            "station_count": len(names),
            "stations": [
                {"station_id": name, **flat_trial_summary([r for r in trials if r["station_id"] == name])}
                for name in names
            ],
            **flat_trial_summary(trials),
            "videos": videos,
            "trials": trials,
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary
    finally:
        env.close()
