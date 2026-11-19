"""Off-policy vector sampling with explicit final observations and local reset."""

import json
from collections import deque
import shutil
import importlib.metadata
import os
from pathlib import Path
import subprocess
import resource
import time

import torch

from lastmile_dataflow.utils.io import REPO_ROOT
from .sac.agent import SAC, SACConfig
from .sac.replay import Replay
from .env.terms import mobile_episode, grasp_episode, collision_episode
from .env.observation import ARM_STATE_FIELDS, REWARD_COMPONENTS, CONTACT_REWARD_COMPONENTS, flat_fields, policy_observation, graph_observation
from .sac.agent import seed_all
from .evaluation import monitor_validation
from .monitor import DEFAULT_TENSORBOARD_EVERY_SECONDS, SuccessWindow, TrainingMonitor
from .evaluation import station_scores, plot_scores
import multiprocessing as mp
from .devices import VISIBLE_DEVICE, sampler_entry
from .config import resolve_config
import pickle
from queue import Empty
from queue import Full


CHECKPOINT_EVERY_TRANSITIONS = 400_000


def runtime_metadata(config, physical_gpu):
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    revisions = {
        "project": os.environ.get("LASTMILE_SOURCE_REVISION")
        or subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "mjlab": subprocess.check_output(["git", "-C", config["mjlab_source"], "rev-parse", "HEAD"], text=True).strip(),
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
                    **grasp_episode(env.task, index),
                    **collision_episode(env.task, index),
                    "transitions": transitions,
                    "station_id": env.task.stations[int(env.task.station_ids[index])]["station_id"],
                    **({
                        "base_xyyaw_world": env.task.fixed_state()[index, :3].cpu().tolist(),
                        "base_xyyaw_initial": env.task.fixed_reference[index, :3].cpu().tolist(),
                    } if env.research_config.get("scene", {}).get("fixed_base", False) else {}),
                    **mobile_episode(env.task, index),
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


def target_updates(healthy, warmup, interval):
    return max(0, healthy - warmup) // interval


class Samplers:
    """Main-process lifecycle and bounded transport; no scheduler."""

    def __init__(self, config, devices, seed, num_envs):

        ctx = mp.get_context("spawn")
        self.queue = ctx.Queue(maxsize=2 * len(devices))
        self.pending = deque()
        self.stop = ctx.Event()
        self.channels, self.processes, self.actors = [], [], []
        for worker, device in enumerate(devices):
            parent, child = ctx.Pipe()
            actor_queue = ctx.Queue(maxsize=1)
            process = ctx.Process(
                target=sampler_entry,
                args=(device, config, seed + worker, num_envs, worker, child, actor_queue, self.queue, self.stop),
            )
            process.start()
            child.close()
            self.actors.append(actor_queue)
            self.channels.append(parent)
            self.processes.append(process)

    def receive(self):
        if self.pending:
            return self.pending.popleft()
        while True:
            try:
                payload, blocked = self.queue.get(timeout=0.2)
                return (*pickle.loads(payload), blocked)
            except Empty:
                for process in self.processes:
                    if process.exitcode is not None:
                        raise RuntimeError(f"Sampler exited: {process.exitcode}")

    def publish(self, agent, random_actions):

        payload = pickle.dumps(
            (agent.updates, random_actions, {k: v.detach().cpu().clone() for k, v in agent.actor.state_dict().items()})
        )

        for queue in self.actors:
            while True:
                try:
                    queue.put(payload, timeout=0.05)
                    break
                except Full:
                    try:
                        queue.get(timeout=0.05)
                    except Empty:
                        pass

    def pause(self):

        pending = []
        for channel in self.channels:
            channel.send(("pause", None))
        waiting = set(range(len(self.channels)))
        while waiting:
            for index in list(waiting):
                if self.channels[index].poll():
                    assert self.channels[index].recv() == "paused"
                    waiting.remove(index)
                elif self.processes[index].exitcode is not None:
                    raise RuntimeError("Sampler exited while pausing")
            try:
                pending.append(self.queue.get(timeout=0.05))
            except Empty:
                pass
        # Paused workers have completed all puts; drain the remaining finite queue.
        while True:
            try:
                pending.append(self.queue.get(timeout=0.05))
            except Empty:
                break

        return [(*pickle.loads(payload), blocked) for payload, blocked in pending]

    def resume(self):
        for channel in self.channels:
            channel.send(("resume", None))

    def close(self):
        self.stop.set()
        for process in self.processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join()
        for channel in self.channels:
            channel.close()
        self.queue.close()
        for queue in self.actors:
            queue.close()


def train(config, *, gpus, output, seed, resume=None, max_transitions=None, num_envs=None):
    """``gpus`` are physical cards: the first also hosts the Learner, each one runs a Sampler."""
    from .env.environment import StationEnv

    config = resolve_config(config)
    seed_all(seed)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    settings = config["training"]
    devices = [str(gpu) for gpu in gpus]
    env = StationEnv(
        config, gpu=VISIBLE_DEVICE, seed=config["evaluation"]["seed"], num_envs=settings["validation_stations"] * settings.get("validation_rollouts_per_station", 1)
    )
    workers = None
    monitor = None
    try:
        observations, _ = env.reset()
        mode = config.get("observation", {}).get("mode", "graph")
        obs = graph_observation(observations) if mode == "graph" else policy_observation(observations, mode)
        contact = config["success"].get("version") == "rl-pick-contact-v2"
        metadata = {
            "config": config,
            "seed": seed,
            **runtime_metadata(config, gpus[0]),
            "sampler_gpus": devices,
            "learner_gpu": devices[0],
            "sampler_num_envs": num_envs or config["environment"]["num_envs"],
            "transition_limit": max_transitions or settings["max_transitions"],
            "observation_mode": mode,
            "node_capacity": obs.nodes.shape[1] if mode == "graph" else None,
            "action_dim": env.action_manager.total_action_dim,
            "state_dim": obs.arm_task_state.shape[-1] if mode == "graph" else obs.shape[-1],
            "arm_state_fields": ARM_STATE_FIELDS if mode == "graph" else flat_fields(obs.shape[-1], real_object="cube" not in config["scene"]),
            "reward_component_names": CONTACT_REWARD_COMPONENTS if contact else REWARD_COMPONENTS,
            "success_protocol": config["success"],
            "stations": env.task.stations,
            "resume": "parameters/optimizers; fresh environment and CPU replay",
        }
        if resume:
            agent, saved = SAC.load(resume, env.device, restore_rng=True)
            if saved["metadata"]["config"] != config:
                raise ValueError("Resume requires the checkpoint effective configuration")
            transitions = saved["transitions"]
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
        else:
            agent = SAC(metadata["state_dim"], env.device, SACConfig(**config["sac"]), mode=mode, action_dim=metadata["action_dim"])
            transitions = 0
        same_output = resume is not None and resume_output.resolve() == output.resolve()
        monitor = TrainingMonitor(
            output,
            every_seconds=settings.get("tensorboard_every_seconds", DEFAULT_TENSORBOARD_EVERY_SECONDS),
            purge_step=transitions if same_output else None,
        )
        if hasattr(env.task, "grasps"):
            subset = [env.task.grasps[i] for i in env.task.grasp_pools[0]]
            metadata["grasp_subset"] = [
                {"source": c["source"], "row": c["row"], "pose_local": c["pose_local"].tolist()}
                for c in subset
            ]
            (output / "grasp-subset.json").write_text(json.dumps(metadata["grasp_subset"], indent=2))
        replay = Replay(settings["replay_capacity"], obs.map(lambda v: v.cpu()) if mode == "graph" else obs.cpu(),
                        action_dim=metadata["action_dim"])
        collected, updates = 0, 0
        limit = max_transitions or settings["max_transitions"]
        log_at = transitions + settings["log_every"]
        checkpoint_at = (transitions // CHECKPOINT_EVERY_TRANSITIONS + 1) * CHECKPOINT_EVERY_TRANSITIONS
        evaluation_at = transitions + 100000
        started = time.perf_counter()
        discarded = 0
        with (output / "metrics.jsonl").open("a") as log:
            workers = Samplers(config, devices, seed, num_envs)
            workers.publish(agent, True)
            while transitions < limit:
                worker, version, random_actions, batch, episodes, blocked = workers.receive()
                count = len(batch.reward)
                keep = min(count, limit - transitions)
                if keep < count:
                    batch = batch[torch.randperm(count)[:keep]]
                    discarded += count - keep
                replay.add(batch)
                before = collected
                collected += keep
                transitions += keep
                for line in episodes.splitlines():
                    event = json.loads(line)
                    event.update(transitions=transitions, worker=worker)
                    log.write(json.dumps(event) + "\n")
                    monitor.record_episode(event)
                if before < settings["warmup_min"] <= collected:
                    workers.publish(agent, False)
                learner = {
                    "event": "learner",
                    "transitions": transitions,
                    "healthy": collected,
                    "updates": agent.updates,
                    "lifecycle_updates": updates,
                    "replay_size": replay.size,
                    "worker": worker,
                    "random_actions": random_actions,
                    "actor_version_lag": agent.updates - version,
                    "queue_blocked_seconds": blocked,
                    "generated_unused": discarded,
                    "learner_gpu_allocated_bytes": torch.cuda.memory_allocated(env.device),
                    "learner_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "transitions_per_s": collected / (time.perf_counter() - started),
                }
                monitor.record_runtime(learner)
                flush_training_monitor(agent, output, metadata, transitions, monitor)
                losses = {}
                while updates < target_updates(collected, settings["warmup_min"], settings["transitions_per_update"]):
                    losses = agent.update(replay.sample(settings["batch_size"]).to(env.device))
                    updates += 1
                    learner.update(
                        updates=agent.updates, lifecycle_updates=updates, actor_version_lag=agent.updates - version,
                        transitions_per_s=collected / (time.perf_counter() - started),
                        learner_gpu_allocated_bytes=torch.cuda.memory_allocated(env.device),
                    )
                    monitor.record_update(losses)
                    monitor.record_runtime(learner)
                    flush_training_monitor(agent, output, metadata, transitions, monitor)
                    if updates % 16 == 0:
                        workers.publish(agent, False)
                if transitions >= log_at:
                    log.write(json.dumps({**learner, **{k: float(v) for k, v in losses.items()}}) + "\n")
                    log.flush()
                    agent.save(output / "latest.pt", metadata=metadata, transitions=transitions)
                    log_at += settings["log_every"]
                checkpoint_at = save_periodic_checkpoint(agent, output, metadata, transitions, checkpoint_at)
                if settings.get("periodic_evaluation", False) and transitions >= evaluation_at and transitions < limit:
                    # Pause and drain before evaluation, keeping queued experience for normal ingestion.
                    pending = workers.pause()
                    flush_training_monitor(agent, output, metadata, transitions, monitor, force=True)
                    validation_checkpoint(agent, env, config, transitions, output, log, monitor)
                    workers.resume()
                    for message in pending:
                        workers.pending.append(message)
                    evaluation_at += 100000
            # Drain in-flight data only at shutdown, then release all Samplers before validation.
            discarded += sum(len(message[3].reward) for message in workers.pause())
            log.write(
                json.dumps(
                    {
                        "event": "sampling_complete",
                        "transitions": transitions,
                        "healthy": collected,
                        "lifecycle_updates": updates,
                        "generated_unused": discarded,
                        "seconds": time.perf_counter() - started,
                    }
                )
                + "\n"
            )
            monitor.record_runtime({"generated_unused": discarded})
            flush_training_monitor(agent, output, metadata, transitions, monitor, force=True)
            workers.close()
            workers = None
            save_best_checkpoint(agent, output, metadata, transitions, None, final=True)
            agent.save(output / "latest.pt", metadata=metadata, transitions=transitions)
            agent, _ = SAC.load(output / "best.pt", env.device)
            if not contact:
                initial_scores = validation_checkpoint(agent, env, config, transitions, output, log, monitor)
                seed_all(config["evaluation"]["seed"])
                rows = station_scores(agent, env, config["evaluation"]["action_samples"], initial_scores=initial_scores)
                (output / "station-q.json").write_text(
                    json.dumps({"checkpoint": str((output / "best.pt").absolute()), "seed": config["evaluation"]["seed"], "interpretation": "equal grasp mean of action-sampled min online Q", "stations": rows}, indent=2)
                )
                plot_scores(rows, output / "station-q.png", config["evaluation"]["q_color_limits"])
        return {"transitions": transitions, "updates": agent.updates, "checkpoint": str(output / "latest.pt")}
    finally:
        if workers is not None:
            workers.close()
        if monitor is not None:
            monitor.close(transitions)
        env.close()
