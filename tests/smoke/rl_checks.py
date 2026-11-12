"""Smoke checks behind tests/smoke/rl.py; import only after the GPU is bound."""

from collections import Counter
import json
from pathlib import Path
import time

import numpy as np
import torch

from lastmile_dataflow.utils.io import read_config
from lastmile_dataflow.station_map.rl.config import resolve_config
from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
from lastmile_dataflow.station_map.rl.env.environment import StationEnv
from lastmile_dataflow.station_map.rl.evaluation import reset_stations
from lastmile_dataflow.station_map.rl.sac.agent import seed_all
from lastmile_dataflow.station_map.rl.env.observation import graph_observation
from lastmile_dataflow.station_map.rl.sac.agent import SAC, SACConfig
from lastmile_dataflow.station_map.rl.sac.replay import Replay, Transition


def upstream_smoke(args):
    from mjlab.envs import ManagerBasedRlEnv
    from mjlab.tasks.manipulation.config.yam.env_cfgs import yam_lift_cube_env_cfg

    cfg = yam_lift_cube_env_cfg(play=True)
    cfg.scene.num_envs = args.num_envs
    env = ManagerBasedRlEnv(cfg, device=f"cuda:{VISIBLE_DEVICE}")
    try:
        env.reset()
        action = torch.zeros(env.num_envs, env.action_manager.total_action_dim, device=env.device)
        for _ in range(args.steps):
            env.step(action)
        assert torch.isfinite(env.sim.data.qpos).all()
        result = {
            "upstream": "yam_lift_cube_env_cfg",
            "gpu": args.physical_gpu,
            "num_envs": env.num_envs,
            "steps": args.steps,
            "finite_qpos": True,
        }
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        env.close()


PREGRASP, APPROACH, CLOSE, LIFT = range(4)
PHASES = ("pregrasp", "approach", "close", "lift")


def scripted_action(env, config, phase, closed_at, step):
    """Per-environment phases, so one vector batch can cover many stations."""
    dp, dr = env.task.goal_error()
    pre_dp, pre_dr = env.task.goal_error(-0.08)
    before = (phase == PREGRASP)[:, None]
    dp, dr = torch.where(before, pre_dp, dp), torch.where(before, pre_dr, dr)
    action = torch.zeros(env.num_envs, 7, device=env.device)
    action[:, :3] = (dp / config["action"]["translation_m"]).clamp(-1, 1)
    action[:, 3:6] = (dr / config["action"]["rotation_rad"]).clamp(-1, 1)
    action[:, 6] = 1
    position, rotation = dp.norm(dim=-1), dr.norm(dim=-1)
    at_pregrasp = (phase == PREGRASP) & (position < 0.01) & (rotation < 0.12)
    at_grasp = (phase == APPROACH) & (position < 0.008) & (rotation < 0.1)
    phase[at_pregrasp] = APPROACH
    phase[at_grasp] = CLOSE
    closed_at[at_grasp] = step
    closing = phase == CLOSE
    action[closing, 6] = -1
    phase[closing & (step - closed_at >= 20)] = LIFT
    lifting = phase == LIFT
    action[lifting] = 0
    # Twenty steps of 1 cm, whatever the action scale.
    action[lifting, 2] = (step - closed_at[lifting] < 40).float() * min(1.0, 0.01 / config["action"]["translation_m"])
    action[lifting, 6] = -1
    return action


def collision_label(prepared, name):
    kind, _, index = name.rpartition("_")
    if not index.isdigit():
        return name
    source = {
        "environment": prepared.scene_spec.worldbody.bodies,
        "environment_world": prepared.scene_spec.worldbody.geoms,
        "nonfinger": prepared.robot_spec.bodies,
    }[kind]
    return f"{kind}:{source[int(index)].name}"


def scripted_batch(env, config, args):
    """Report which physically valid stations the same 7D/DLS path can pick from."""
    candidates = [i for i, s in enumerate(env.task.stations) if s["valid"] and s["split"] == args.split]
    chosen = (
        np.random.default_rng(args.station_seed)
        .choice(candidates, min(env.num_envs, len(candidates)), replace=False)
        .tolist()
    )
    chosen += chosen[:1] * (env.num_envs - len(chosen))
    warmup(env, torch.tensor(chosen, device=env.device), args.warmup_steps)
    phase = torch.zeros(env.num_envs, dtype=torch.long, device=env.device)
    closed_at = torch.zeros_like(phase)
    rows, finished = [None] * env.num_envs, torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
    best_position = torch.full((env.num_envs,), float("inf"), device=env.device)
    start = time.perf_counter()
    components = torch.zeros(env.num_envs, env.task.reward_components.shape[-1], device=env.device)
    for step in range(env.max_episode_length):
        action = scripted_action(env, config, phase, closed_at, step)
        action[finished] = 0
        _, _, terminated, truncated, _ = env.step(action)
        best_position = torch.minimum(best_position, env.task.goal_error()[0].norm(dim=-1))
        components += env.task.reward_components * (~finished)[:, None]
        done = (terminated | truncated) & ~finished
        for i in done.nonzero(as_tuple=True)[0].tolist():
            hits = env.task.collision_hits[i]
            station = env.task.stations[chosen[i]]
            rows[i] = {
                **station,
                "success": bool(env.task.success[i]),
                "failed": bool(env.task.failed[i]),
                "control_failed": bool(env.task.control_failed[i]),
                "unhealthy": bool(env.task.unhealthy[i]),
                "collision_free": bool(env.task.collision_free[i]),
                "phase": PHASES[int(phase[i])],
                "steps": step + 1,
                "best_position_error_m": float(best_position[i]),
                "max_drift_m": float(env.task.max_drift[i]),
                "max_yaw_drift_rad": float(env.task.max_yaw_drift[i]),
                "max_lift_drift_m": float(env.task.max_lift_drift[i]),
                "collision_time_s": float(env.task.collision_time[i]),
                "reward_components": components[i].cpu().tolist(),
                "collisions": {
                    collision_label(env.prepared, env.task.collision_names[j]): float(hits[j])
                    for j in hits.nonzero(as_tuple=True)[0].tolist()
                },
            }
        finished |= done
        if finished.all():
            break
        # mjlab requires ended slots to reset; finished slots idle until the batch ends.
        ended = (terminated | truncated).nonzero(as_tuple=True)[0]
        if len(ended):
            reset_stations(env, [chosen[i] for i in ended.tolist()], env_ids=ended)
    healthy = [r for r in rows if not r["unhealthy"]]
    sensors = Counter(name for r in healthy for name in r["collisions"])
    summary = {
        "gpu": args.physical_gpu,
        "split": args.split,
        "num_envs": env.num_envs,
        "seconds": time.perf_counter() - start,
        "vector_steps": step + 1,
        "healthy": len(healthy),
        "success": sum(r["success"] for r in healthy),
        "collision_free_success": sum(r["success"] and r["collision_free"] for r in healthy),
        "collision_free": sum(r["collision_free"] for r in healthy),
        "final_phase": dict(Counter(r["phase"] for r in healthy)),
        "control_failed": sum(r["control_failed"] for r in healthy),
        "success_yaw_bins": dict(Counter(r["station_id"][-3:] for r in healthy if r["success"])),
        "trial_yaw_bins": dict(Counter(r["station_id"][-3:] for r in healthy)),
        "collision_sensors": sensors.most_common(30),
        "overflow_bits": env.sim.data.overflow.cpu().tolist()[:8],
        "peak_memory_bytes": torch.cuda.max_memory_allocated(env.device),
        "rows": rows,
    }
    Path(args.output).write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))


@torch.no_grad()
def policy_diagnosis(env, config, args):
    """Report how far a frozen stochastic Actor gets through the grasp sequence."""
    agent, saved = SAC.load(args.policy_checkpoint, env.device)
    debug = set(config["training"]["debug_station_ids"])
    candidates = [
        i
        for i, s in enumerate(env.task.stations)
        if s["valid"] and s["split"] == args.split and (not debug or s["station_id"] in debug)
    ]
    chosen = (
        np.random.default_rng(args.station_seed)
        .choice(candidates, min(env.num_envs, len(candidates)), replace=False)
        .tolist()
    )
    chosen = [chosen[i % len(chosen)] for i in range(env.num_envs)]
    obs = reset_stations(env, chosen)
    n, device = env.num_envs, env.device
    # Does the frozen Critic prefer moving toward the grasp pose, and does the Actor?
    dp, dr = env.task.goal_error()
    toward = torch.zeros(n, 7, device=device)
    toward[:, :3] = (dp / config["action"]["translation_m"]).clamp(-1, 1)
    toward[:, 3:6] = (dr / config["action"]["rotation_rad"]).clamp(-1, 1)
    toward[:, 6] = 1
    away = toward.clone()
    away[:, :6] = -toward[:, :6]
    still = torch.zeros_like(toward)
    still[:, 6] = 1
    sampled = [agent.actor.sample(obs)[0] for _ in range(16)]

    def q(action):
        return torch.minimum(agent.q1(obs, action), agent.q2(obs, action)).mean().item()

    mean_action = torch.stack(sampled).mean(0)
    critic_probe = {
        "q_toward": q(toward),
        "q_still": q(still),
        "q_away": q(away),
        "q_actor_samples": sum(q(a) for a in sampled) / len(sampled),
        "actor_mean_cosine_to_goal_translation": float(
            torch.nn.functional.cosine_similarity(mean_action[:, :3], dp, dim=-1).mean()
        ),
        "actor_mean_abs_translation": float(mean_action[:, :3].abs().mean()),
        "actor_sample_std_translation": float(torch.stack(sampled)[:, :, :3].std(0).mean()),
        "initial_distance_m": float(dp.norm(dim=-1).mean()),
    }
    print(json.dumps(critic_probe, indent=2))
    finished = torch.zeros(n, dtype=torch.bool, device=device)
    best_distance, best_angle, min_width = (torch.full((n,), float("inf"), device=device) for _ in range(3))
    near_steps, touch_any, touch_both, closed_near, max_lift = (torch.zeros(n, device=device) for _ in range(5))
    for _ in range(env.max_episode_length):
        action, _ = agent.actor.sample(obs)
        observations, _, terminated, truncated, _ = env.step(action)
        active = ~finished
        dp, dr = env.task.goal_error()
        distance, angle = dp.norm(dim=-1), dr.norm(dim=-1)
        q = env.task.robot.data.joint_pos
        width = q[:, env.task.fingers[0]] - q[:, env.task.fingers[1]]
        touching = env.task.forces > 1e-6
        near = (distance < 0.03) & (angle < 0.3)
        best_distance[active] = torch.minimum(best_distance, distance)[active]
        best_angle[active] = torch.minimum(best_angle, angle)[active]
        min_width[active] = torch.minimum(min_width, width)[active]
        near_steps += (active & near).float()
        touch_any += (active & touching.any(-1)).float()
        touch_both += (active & touching.all(-1)).float()
        closed_near += (active & near & (width < 0.06)).float()
        lift = env.sim.data.xpos[:, env.task.target, 2] - env.task.height_reference
        max_lift[active] = torch.maximum(max_lift, lift)[active]
        finished |= terminated | truncated
        obs = graph_observation(observations).clone()
        ended = (terminated | truncated).nonzero(as_tuple=True)[0]
        if finished.all():
            break
        if len(ended):
            obs.replace(ended, reset_stations(env, [chosen[i] for i in ended.tolist()], env_ids=ended))

    def share(values, threshold=0):
        return float((values > threshold).float().mean())

    summary = {
        "checkpoint": str(args.policy_checkpoint),
        "transitions": saved["transitions"],
        "split": args.split,
        "num_envs": n,
        "position_error_cm_percentiles": (
            torch.quantile(best_distance, torch.tensor([0.0, 0.25, 0.5, 0.75, 1.0], device=device)) * 100
        )
        .cpu()
        .tolist(),
        "rotation_error_rad_percentiles": torch.quantile(
            best_angle, torch.tensor([0.0, 0.25, 0.5, 0.75, 1.0], device=device)
        )
        .cpu()
        .tolist(),
        "share_reached_3cm_0p3rad": share(near_steps),
        "mean_steps_near_when_reached": float(near_steps[near_steps > 0].mean()) if (near_steps > 0).any() else 0.0,
        "share_any_finger_touch": share(touch_any),
        "share_both_fingers_touch": share(touch_both),
        "share_closed_below_6cm_while_near": share(closed_near),
        "min_width_m_percentiles": torch.quantile(min_width, torch.tensor([0.0, 0.5, 1.0], device=device))
        .cpu()
        .tolist(),
        "share_lift_over_1cm": share(max_lift, 0.01),
        "share_lift_over_5cm": share(max_lift, 0.05),
        "max_lift_m": float(max_lift.max()),
        "success": int(env.task.success.sum()),
        "critic_probe": critic_probe,
    }
    Path(args.output).write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


def warmup(env, station_ids, steps):
    action = torch.zeros(env.num_envs, 7, device=env.device)
    for _ in range(steps):
        _, _, terminated, truncated, _ = env.step(action)
        if (terminated | truncated).any():
            reset_stations(env, station_ids)
    return reset_stations(env, station_ids)


def run(args):
    """Run the selected smoke on in-process GPU 0 (the caller has already bound the card)."""
    if args.upstream:
        upstream_smoke(args)
        return
    config = resolve_config(read_config(args.config))
    if args.approach_offset is not None:
        config["grasp"]["approach_offset_m"] = args.approach_offset
    if args.grasp_row is not None:
        config["grasp"]["row"] = args.grasp_row
    seed_all(42)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=42, num_envs=args.num_envs)
    try:
        if args.scripted_batch:
            scripted_batch(env, config, args)
            return
        if args.policy_checkpoint:
            policy_diagnosis(env, config, args)
            return
        if args.station_id:
            index = next(
                i for i, s in enumerate(env.task.stations) if s["station_id"] == args.station_id and s["valid"]
            )
            obs = reset_stations(env, [index] * env.num_envs)
        else:
            observations, _ = env.reset()
            obs = graph_observation(observations)
        station_ids = env.task.station_ids.clone()
        obs = warmup(env, station_ids, args.warmup_steps)
        initial_arm = env.task.robot.data.joint_pos[:, env.task.arm].clone()
        replay = Replay(max(args.steps * env.num_envs, 1), obs) if args.learner_updates else None
        torch.cuda.synchronize(env.device)
        start = time.perf_counter()
        completed = 0
        phase_ids = torch.zeros(env.num_envs, dtype=torch.long, device=env.device)
        closed_at = torch.zeros_like(phase_ids)
        trace = {
            name: []
            for name in (
                "qpos",
                "qvel",
                "action",
                "position_error",
                "rotation_error",
                "joint_command",
                "limit_fraction",
                "forces",
                "support",
                "hold_s",
            )
        }
        for step in range(env.max_episode_length if args.scripted else args.steps):
            action = torch.zeros(env.num_envs, 7, device=env.device)
            action[:, 6] = 1
            if args.random_actions:
                action = torch.rand_like(action) * 2 - 1
            if args.scripted:
                action = scripted_action(env, config, phase_ids, closed_at, step)
            observations, reward, terminated, truncated, _ = env.step(action)
            if args.scripted:
                controller = env.action_manager.get_term("tcp")
                position_error, rotation_error = env.task.goal_error()
                for name, value in (
                    ("qpos", env.sim.data.qpos),
                    ("qvel", env.sim.data.qvel),
                    ("action", action),
                    ("position_error", position_error),
                    ("rotation_error", rotation_error),
                    ("joint_command", controller.q_command),
                    ("limit_fraction", controller.limit_fraction),
                    ("forces", env.task.forces),
                    ("support", env.task.support),
                    ("hold_s", env.task.hold),
                ):
                    trace[name].append(value[0].detach().cpu().numpy().copy())
            next_obs = graph_observation(observations)
            if replay is not None:
                healthy = ~env.task.unhealthy
                replay.add(
                    Transition(
                        obs[healthy],
                        action[healthy],
                        reward[healthy],
                        next_obs[healthy],
                        (terminated | truncated)[healthy],
                        torch.zeros_like(truncated[healthy]),
                        env.task.reward_components[healthy],
                        env.task.station_ids[healthy],
                    )
                )
            obs = next_obs.clone()
            assert torch.isfinite(obs.nodes).all() and torch.isfinite(obs.arm_task_state).all()
            assert torch.isfinite(reward).all()
            assert not env.task.unhealthy.any(), "physics overflow or nonfinite state"
            completed += env.num_envs
            done = terminated | truncated
            if done.any():
                break
        torch.cuda.synchronize(env.device)
        summary = {
            "gpu": args.physical_gpu,
            "num_envs": env.num_envs,
            "transitions": completed,
            "seconds": time.perf_counter() - start,
            "node_capacity": env.task.graph.capacity,
            "state_dim": obs.arm_task_state.shape[-1],
            "valid_stations": sum(s["valid"] for s in env.task.stations),
            "valid_splits": dict(Counter(s["split"] for s in env.task.stations if s["valid"])),
            "selected_stations": [env.task.stations[int(i)] for i in station_ids],
            "success": env.task.success.cpu().tolist(),
            "failed": env.task.failed.cpu().tolist(),
            "max_drift_m": env.task.max_drift.cpu().tolist(),
            "hold_s": env.task.hold.cpu().tolist(),
            "max_yaw_drift_rad": env.task.max_yaw_drift.cpu().tolist(),
            "max_lift_drift_m": env.task.max_lift_drift.cpu().tolist(),
            "arm_displacement_rad": (env.task.robot.data.joint_pos[:, env.task.arm] - initial_arm)
            .norm(dim=-1)
            .cpu()
            .tolist(),
            "finger_forces_n": env.task.forces.cpu().tolist(),
            "support": env.task.support.cpu().tolist(),
            "overflow_bits": env.sim.data.overflow.cpu().tolist(),
            "lift_m": (env.sim.data.xpos[:, env.task.target, 2] - env.task.height_reference).cpu().tolist(),
            "collision_free": env.task.collision_free.cpu().tolist(),
            "peak_memory_bytes": torch.cuda.max_memory_allocated(env.device),
            "scripted": args.scripted,
            "phase": PHASES[int(phase_ids[0])] if args.scripted else "sampling",
        }
        position_error, rotation_error = env.task.goal_error()
        summary["position_error_m"] = position_error.norm(dim=-1).cpu().tolist()
        summary["rotation_error_rad"] = rotation_error.norm(dim=-1).cpu().tolist()
        if args.scripted:
            replay_path = Path(args.output).with_name(Path(args.output).stem + "-control.npz")
            np.savez_compressed(replay_path, **{name: np.asarray(values) for name, values in trace.items()})
            summary["control_trace"] = str(replay_path)
        if replay is not None:
            agent = SAC(obs.arm_task_state.shape[-1], env.device, SACConfig(**config["sac"]))
            learner_started = time.perf_counter()
            for _ in range(args.learner_updates):
                losses = agent.update(replay.sample(min(args.batch_size, replay.size)))
            torch.cuda.synchronize(env.device)
            summary["learner_seconds_per_update"] = (time.perf_counter() - learner_started) / args.learner_updates
            summary["learner_updates"] = agent.updates
            summary["learner_losses"] = {name: float(value) for name, value in losses.items()}
        # Exercise local reset without replacing ongoing observations.
        if not args.scripted:
            before = env.sim.data.qpos.clone()
            caches = {
                name: getattr(env.task, name).clone()
                for name in (
                    "elapsed",
                    "window_start",
                    "last_action",
                    "reference_position",
                    "fixed_reference",
                    "contact_bonus_paid",
                    "previous_both",
                    "lift_peak",
                )
            }
            ids = torch.unique(torch.cat((done.nonzero(as_tuple=True)[0], torch.tensor([0], device=env.device))))
            ongoing = torch.ones(env.num_envs, dtype=torch.bool, device=env.device)
            ongoing[ids] = False
            env.reset(env_ids=ids)
            torch.testing.assert_close(before[ongoing], env.sim.data.qpos[ongoing], rtol=0, atol=0)
            for name, cached in caches.items():
                torch.testing.assert_close(cached[ongoing], getattr(env.task, name)[ongoing], rtol=0, atol=0)
            summary["partial_reset_preserved_other_qpos"] = True
            summary["partial_reset_ongoing_envs_checked"] = int(ongoing.sum())
        if args.monitor:
            from lastmile_dataflow.station_map.rl.evaluation import monitor_validation
            from lastmile_dataflow.station_map.rl.monitor import training_curves

            output_dir = Path(args.output).parent
            event = monitor_validation(agent, env, config, completed, output_dir)
            rows = json.loads((output_dir / "analysis" / f"validation-{completed:012d}.json").read_text())
            with (output_dir / "metrics.jsonl").open("w") as stream:
                stream.write(
                    json.dumps({"event": "learner", "transitions": completed, **summary["learner_losses"]}) + "\n"
                )
                stream.write(json.dumps(event) + "\n")
                for trial in rows["trials"]:
                    stream.write(
                        json.dumps(
                            {
                                "event": "episode",
                                "transitions": completed,
                                "return": trial["episode_return"],
                                "infrastructure_failure": trial["infrastructure_failure"],
                            }
                        )
                        + "\n"
                    )
            training_curves(output_dir / "metrics.jsonl", output_dir / "views")
            summary["monitor"] = event
            summary["monitor_interpretation"] = (
                "untrained software smoke with diagnostic updates, not a training result"
            )
        Path(args.output).write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        env.close()
