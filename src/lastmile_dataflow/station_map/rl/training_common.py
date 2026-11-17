"""Shared run metadata, episode records, monitor windows and checkpoint selection."""

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from lastmile_dataflow.utils.io import REPO_ROOT
from .env.observation import ARM_STATE_FIELDS, CONTACT_REWARD_COMPONENTS, FLAT_FIELDS, REWARD_COMPONENTS
from .evaluation import monitor_validation
from .monitor import SuccessWindow


CHECKPOINT_EVERY_TRANSITIONS = 400_000


def runtime_metadata(config, physical_gpu):
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    revisions = {
        "project": os.environ.get("LASTMILE_SOURCE_REVISION")
        or subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True, timeout=10).strip(),
        "mjlab": subprocess.check_output(["git", "-C", config["mjlab_source"], "rev-parse", "HEAD"], text=True, timeout=10).strip(),
    }
    return {
        "revisions": revisions,
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("torch", "torch-geometric", "mjlab", "mujoco", "mujoco-warp", "warp-lang", "numpy")
        },
        "gpu_argument": physical_gpu,
        "cuda_visible_devices": visible,
        "physical_gpu": physical_gpu,
    }


def record_episodes(log, env, done, transitions, returns, components, steps, random_episodes=None):
    for index in done.nonzero(as_tuple=True)[0].tolist():
        log.write(
            json.dumps(
                {
                    "event": "episode",
                    "random_warmup": bool(random_episodes[index]) if random_episodes is not None else False,
                    **({} if env.research_config["success"].get("version") == "rl-pick-contact-v2" else {
                        "grasp_source": env.task.grasps[int(env.task.grasp_ids[index])]["source"],
                        "grasp_row": env.task.grasps[int(env.task.grasp_ids[index])]["row"],
                    }),
                    "transitions": transitions,
                    "station_id": env.task.stations[int(env.task.station_ids[index])]["station_id"],
                    "return": float(returns[index]),
                    "steps": int(steps[index]),
                    "reward_components": components[index].cpu().tolist(),
                    "reward_component_names": (CONTACT_REWARD_COMPONENTS if env.research_config["success"].get("version") == "rl-pick-contact-v2" else REWARD_COMPONENTS),
                    **({} if env.task.contact_protocol else {
                        "contact_bonus_count": int(env.task.contact_bonus_paid[index]),
                    }),
                    "success": bool(env.task.success[index]),
                    "first_contact_time_s": float(env.task.first_contact_time_s[index]) if hasattr(env.task, "first_contact_time_s") and env.task.first_contact_time_s[index] >= 0 else None,
                    "collision_free": bool(env.task.collision_free[index]),
                    "collision_time_s": float(env.task.collision_time[index]),
                    "max_drift_m": float(env.task.max_drift[index]),
                    "max_yaw_drift_rad": float(env.task.max_yaw_drift[index]),
                    "max_lift_drift_m": float(env.task.max_lift_drift[index]),
                    "infrastructure_failure": bool(env.task.unhealthy[index]),
                }
            )
            + "\n"
        )
    returns[done] = 0
    components[done] = 0
    steps[done] = 0


def validation_checkpoint(agent, env, config, transitions, output, log, monitor):
    started = time.perf_counter()
    event, scores = monitor_validation(agent, env, config, transitions, output, force_video=True, return_scores=True)
    event["seconds"] = time.perf_counter() - started
    log.write(json.dumps(event) + "\n")
    log.flush()
    monitor.record_evaluation(event)
    if not event["healthy_trials"]:
        raise RuntimeError("Training-station evaluation produced no healthy trials")
    return scores


def save_best_checkpoint(agent, output, metadata, transitions, window: SuccessWindow | None, *, final=False):
    best = metadata.get("best_training_success_rate")
    improved = window is not None and (best is None or best["rate"] is None or window.rate > best["rate"])
    fallback = final and best is None and window is None
    if improved or fallback:
        metadata["best_training_success_rate"] = {
            "metric": "recent_100_episode_success_rate",
            "rate": window.rate if window is not None else None,
            "count": window.count if window is not None else 0,
            "transitions": transitions,
            "fallback": "no eligible episodes; final model" if fallback else None,
        }
        agent.save(Path(output) / "best.pt", metadata=metadata, transitions=transitions)


def flush_training_monitor(agent, output, metadata, transitions, monitor, *, force=False):
    window = monitor.flush(transitions, force=force)
    save_best_checkpoint(agent, output, metadata, transitions, window)


def save_periodic_checkpoint(agent, output, metadata, transitions, checkpoint_at):
    if transitions >= checkpoint_at:
        directory = Path(output) / "ckpts"
        directory.mkdir(exist_ok=True)
        agent.save(directory / f"step-{transitions:09d}.pt", metadata=metadata, transitions=transitions)
        return (transitions // CHECKPOINT_EVERY_TRANSITIONS + 1) * CHECKPOINT_EVERY_TRANSITIONS
    return checkpoint_at


def training_metadata(config, env, obs, *, gpus, seed, num_envs, max_transitions):
    settings = config["training"]
    devices = [str(gpu) for gpu in gpus]
    mode = config.get("observation", {}).get("mode", "graph")
    contact = config["success"].get("version") == "rl-pick-contact-v2"
    return {
        "config": config,
        "seed": seed,
        **runtime_metadata(config, gpus[0]),
        "sampler_gpus": devices,
        "learner_gpu": devices[0],
        "sampler_num_envs": num_envs or config["environment"]["num_envs"],
        "transition_limit": max_transitions or settings["max_transitions"],
        "observation_mode": mode,
        "node_capacity": obs.nodes.shape[1] if mode == "graph" else None,
        "state_dim": obs.arm_task_state.shape[-1] if mode == "graph" else obs.shape[-1],
        "arm_state_fields": ARM_STATE_FIELDS if mode == "graph" else FLAT_FIELDS,
        "reward_component_names": CONTACT_REWARD_COMPONENTS if contact else REWARD_COMPONENTS,
        "success_protocol": config["success"],
        "stations": env.task.stations,
        "resume": "parameters/optimizers; fresh environment and CPU replay",
    }


def restore_best_checkpoint(saved, resume, output, metadata):
    best = saved["metadata"].get("best_training_success_rate")
    resume_output = Path(resume).parent
    if resume_output.name == "ckpts":
        # A historical snapshot cannot identify an overwritten best.pt.
        resume_output = resume_output.parent
    elif best is not None and best.get("metric") == "recent_100_episode_success_rate":
        metadata["best_training_success_rate"] = best
    previous_best = resume_output / "best.pt"
    if metadata.get("best_training_success_rate") is not None:
        if not previous_best.is_file():
            raise ValueError("Resume requires the previously selected best.pt")
        if previous_best.resolve() != (output / "best.pt").resolve():
            shutil.copyfile(previous_best, output / "best.pt")
    return resume_output
