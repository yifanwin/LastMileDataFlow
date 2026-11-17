"""Algorithm dispatch and asynchronous SAC training with explicit final observations."""

import json
from collections import deque
from pathlib import Path
import resource
import time

import torch

from .sac.agent import SAC, SACConfig
from .sac.replay import Replay
from .env.observation import policy_observation, graph_observation
from .checkpoint import seed_all
from .monitor import DEFAULT_TENSORBOARD_EVERY_SECONDS, TrainingMonitor
from .evaluation import station_scores, plot_scores
import multiprocessing as mp
from .devices import VISIBLE_DEVICE, sampler_entry
from .config import resolve_config
from .ppo.training import train_ppo
from .training_common import (
    CHECKPOINT_EVERY_TRANSITIONS, training_metadata, restore_best_checkpoint,
    flush_training_monitor, save_best_checkpoint, save_periodic_checkpoint, validation_checkpoint,
)
import pickle
from queue import Empty
from queue import Full


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


def train_sac(config, *, gpus, output, seed, resume=None, max_transitions=None, num_envs=None):
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
        metadata = training_metadata(config, env, obs, gpus=gpus, seed=seed,
                                     num_envs=num_envs, max_transitions=max_transitions)
        if resume:
            agent, saved = SAC.load(resume, env.device, restore_rng=True)
            if saved["metadata"]["config"] != config:
                raise ValueError("Resume requires the checkpoint effective configuration")
            transitions = saved["transitions"]
            resume_output = restore_best_checkpoint(saved, resume, output, metadata)
        else:
            agent = SAC(metadata["state_dim"], env.device, SACConfig(**config["sac"]), mode=mode)
            transitions = 0
        same_output = resume is not None and resume_output.resolve() == output.resolve()
        monitor = TrainingMonitor(
            output,
            every_seconds=settings.get("tensorboard_every_seconds", DEFAULT_TENSORBOARD_EVERY_SECONDS),
            purge_step=transitions if same_output else None,
        )
        replay = Replay(settings["replay_capacity"], obs.map(lambda v: v.cpu()) if mode == "graph" else obs.cpu())
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


def train(config, *, gpus, output, seed, resume=None, max_transitions=None, num_envs=None, algorithm=None):
    """Choose the algorithm without changing the existing SAC defaults."""
    kind = algorithm or config.get("algorithm", "sac")
    if kind not in ("sac", "ppo"):
        raise ValueError(f"Unknown RL algorithm: {kind}")
    config = dict(config)
    if kind == "ppo" or "algorithm" in config:
        config["algorithm"] = kind
    if kind == "ppo":
        runner = train_ppo
    else:
        runner = train_sac
    return runner(config, gpus=gpus, output=output, seed=seed, resume=resume,
                  max_transitions=max_transitions, num_envs=num_envs)
