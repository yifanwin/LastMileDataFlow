"""Off-policy vector sampling with explicit final observations and local reset."""

import json
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
from .env.observation import ARM_STATE_FIELDS, REWARD_COMPONENTS
from .env.observation import graph_observation
from .sac.agent import seed_all
from .evaluation import monitor_validation
from .monitor import training_curves
from .evaluation import station_scores, plot_scores
import multiprocessing as mp
from .devices import VISIBLE_DEVICE, sampler_entry
from .config import resolve_config
import pickle
from queue import Empty
from queue import Full


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


def record_episodes(log, env, done, transitions, returns, components, steps):
    for index in done.nonzero(as_tuple=True)[0].tolist():
        log.write(
            json.dumps(
                {
                    "event": "episode",
                    "transitions": transitions,
                    "station_id": env.task.stations[int(env.task.station_ids[index])]["station_id"],
                    "return": float(returns[index]),
                    "steps": int(steps[index]),
                    "reward_components": components[index].cpu().tolist(),
                    "reward_component_names": REWARD_COMPONENTS,
                    "contact_bonus_count": int(env.task.contact_bonus_paid[index]),
                    "success": bool(env.task.success[index]),
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


def validation_checkpoint(agent, env, config, transitions, output, log, metadata):

    started = time.perf_counter()
    event = monitor_validation(agent, env, config, transitions, output, force_video=True)
    event["seconds"] = time.perf_counter() - started
    log.write(json.dumps(event) + "\n")
    log.flush()
    if not event["healthy_trials"]:
        raise RuntimeError("Independent validation produced no healthy trials")
    metadata["best_validation_score"] = [event["evaluation_success_rate"], event["episode_return_mean"]]
    agent.save(Path(output) / "best.pt", metadata=metadata, transitions=transitions)
    agent.save(Path(output) / "latest.pt", metadata=metadata, transitions=transitions)
    training_curves(Path(output) / "metrics.jsonl", Path(output) / "views")


def target_updates(healthy, warmup, interval):
    return max(0, healthy - warmup) // interval


class Samplers:
    """Main-process lifecycle and bounded transport; no scheduler."""

    def __init__(self, config, devices, seed, num_envs):

        ctx = mp.get_context("spawn")
        self.queue = ctx.Queue(maxsize=2 * len(devices))
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
        config, gpu=VISIBLE_DEVICE, seed=config["evaluation"]["seed"], num_envs=settings["validation_stations"]
    )
    workers = None
    try:
        observations, _ = env.reset()
        obs = graph_observation(observations)
        metadata = {
            "config": config,
            "seed": seed,
            **runtime_metadata(config, gpus[0]),
            "sampler_gpus": devices,
            "learner_gpu": devices[0],
            "sampler_num_envs": num_envs or config["environment"]["num_envs"],
            "transition_limit": max_transitions or settings["max_transitions"],
            "node_capacity": obs.nodes.shape[1],
            "state_dim": obs.arm_task_state.shape[-1],
            "arm_state_fields": ARM_STATE_FIELDS,
            "reward_component_names": REWARD_COMPONENTS,
            "success_protocol": config["success"],
            "stations": env.task.stations,
            "resume": "parameters/optimizers; fresh environment and CPU replay",
        }
        if resume:
            agent, saved = SAC.load(resume, env.device, restore_rng=True)
            if saved["metadata"]["config"] != config:
                raise ValueError("Resume requires the checkpoint effective configuration")
            transitions = saved["transitions"]
        else:
            agent = SAC(obs.arm_task_state.shape[-1], env.device, SACConfig(**config["sac"]))
            transitions = 0
        replay = Replay(settings["replay_capacity"], obs.map(lambda v: v.cpu()))
        collected, updates = 0, 0
        limit = max_transitions or settings["max_transitions"]
        log_at = transitions + settings["log_every"]
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
                if before < settings["warmup_min"] <= collected:
                    workers.publish(agent, False)
                losses = {}
                while updates < target_updates(collected, settings["warmup_min"], settings["transitions_per_update"]):
                    losses = agent.update(replay.sample(settings["batch_size"]).to(env.device))
                    updates += 1
                    if updates % 16 == 0:
                        workers.publish(agent, False)
                if transitions >= log_at:
                    log.write(
                        json.dumps(
                            {
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
                                **{k: float(v) for k, v in losses.items()},
                            }
                        )
                        + "\n"
                    )
                    log.flush()
                    agent.save(output / "latest.pt", metadata=metadata, transitions=transitions)
                    log_at += settings["log_every"]
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
            workers.close()
            workers = None
            agent.save(output / "latest.pt", metadata=metadata, transitions=transitions)
            validation_checkpoint(agent, env, config, transitions, output, log, metadata)
            rows = station_scores(agent, env, config["evaluation"]["action_samples"])
            (output / "station-q.json").write_text(
                json.dumps({"checkpoint": str(output / "best.pt"), "stations": rows}, indent=2)
            )
            plot_scores(rows, output / "station-q.png", config["evaluation"]["q_color_limits"])
        return {"transitions": transitions, "updates": agent.updates, "checkpoint": str(output / "latest.pt")}
    finally:
        if workers is not None:
            workers.close()
        env.close()
