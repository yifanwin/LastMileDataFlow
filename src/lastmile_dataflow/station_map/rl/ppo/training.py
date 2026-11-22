"""Synchronous multi-GPU PPO using the shared task, monitoring and recording flow."""

from collections import deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import resource
import time

import torch

from ..checkpoint import load_agent, seed_all
from ..config import PPOConfig, resolve_config
from ..devices import VISIBLE_DEVICE
from ..env.observation import policy_observation
from ..evaluation import station_scores, plot_scores
from ..monitor import DEFAULT_TENSORBOARD_EVERY_SECONDS, TrainingMonitor
from ..training_common import (
    CHECKPOINT_EVERY_TRANSITIONS, flush_training_monitor, record_grasp_subset, restore_best_checkpoint,
    save_best_checkpoint, save_periodic_checkpoint, training_metadata, validation_checkpoint,
)
from .agent import PPO
from .rollout import RolloutBatch
from .sampling import SynchronousSamplers


@dataclass
class SigmaCurriculum:
    """Raise the noise ceiling after the low- and high-noise training windows both succeed.

    Level -1 is sigma = 0 (stage 1). Windows hold the latest episodes with sigma <= 0.25 and
    >= 0.75 of the current ceiling; at level -1 both are every episode.
    """

    levels: list
    start_level: int = -1
    min_transitions: int = 1_000_000
    low_success: float = 0.8
    high_success: float = 0.75
    window: int = 2000
    level: int = field(init=False)
    since: int = field(init=False, default=0)

    def __post_init__(self):
        self.level = self.start_level
        self.low, self.high = deque(maxlen=self.window), deque(maxlen=self.window)

    @property
    def sigma_max(self):
        return 0.0 if self.level < 0 else float(self.levels[self.level])

    def record(self, event):
        if event.get("infrastructure_failure") or "sigma" not in event:
            return
        ceiling = self.sigma_max
        if event["sigma"] <= 0.25 * ceiling + 1e-6:
            self.low.append(event["success"])
        if event["sigma"] >= 0.75 * ceiling - 1e-6:
            self.high.append(event["success"])

    def rates(self):
        rate = lambda values: sum(values) / len(values) if values else 0.0
        return rate(self.low), rate(self.high)

    def advance(self, transitions):
        low, high = self.rates()
        ready = (len(self.low) == self.window and len(self.high) == self.window
                 and transitions - self.since >= self.min_transitions
                 and low >= self.low_success and high >= self.high_success)
        if not ready or self.level + 1 >= len(self.levels):
            return None
        self.level += 1
        self.since = transitions
        self.low.clear()
        self.high.clear()
        return {"event": "curriculum", "transitions": transitions, "level": self.level,
                "sigma_max": self.sigma_max, "previous_low_success": low, "previous_high_success": high}


def train_ppo(config, *, gpus, output, seed, resume=None, max_transitions=None, num_envs=None):
    from ..env.environment import StationEnv

    config = resolve_config(config)
    cfg = PPOConfig(**config["ppo"])
    seed_all(seed)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    settings = config["training"]
    limit = max_transitions if max_transitions is not None else settings["max_transitions"]
    if limit < 1 or (num_envs is not None and num_envs < 1):
        raise ValueError("PPO transition budget and environment count must be positive")
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=config["evaluation"]["seed"],
                     num_envs=settings["validation_stations"] * settings.get("validation_rollouts_per_station", 1))
    workers = monitor = None
    transitions = 0
    try:
        observations, _ = env.reset()
        mode = config.get("observation", {}).get("mode", "graph")
        obs = policy_observation(observations, mode)
        metadata = training_metadata(config, env, obs, gpus=gpus, seed=seed,
                                     num_envs=num_envs, max_transitions=max_transitions)
        metadata.update(algorithm="ppo", resume="parameters/optimizers/normalizer; fresh environment and rollout")
        (output / "config.json").write_text(json.dumps(config, indent=2))
        resume_output = None
        if resume:
            agent, saved = load_agent(resume, env.device, restore_rng=True, algorithm="ppo")
            if saved["metadata"]["config"] != config:
                raise ValueError("Resume requires the checkpoint effective configuration")
            transitions = saved["transitions"]
            resume_output = restore_best_checkpoint(saved, resume, output, metadata)
        else:
            agent = PPO(metadata["state_dim"], env.device, cfg, mode=mode, action_dim=metadata["action_dim"])
        record_grasp_subset(env, output, metadata)
        monitor = TrainingMonitor(output, every_seconds=settings.get("tensorboard_every_seconds", DEFAULT_TENSORBOARD_EVERY_SECONDS),
                                  best_checkpoint=settings.get("best_checkpoint"),
                                  purge_step=transitions if resume_output is not None and resume_output.resolve() == output.resolve() else None)
        curriculum = SigmaCurriculum(**config["curriculum"]) if "curriculum" in config else None
        workers = SynchronousSamplers(config, gpus, seed, num_envs)
        workers.ready()
        started = time.perf_counter()
        collected = 0
        log_at = transitions + settings["log_every"]
        checkpoint_at = (transitions // CHECKPOINT_EVERY_TRANSITIONS + 1) * CHECKPOINT_EVERY_TRANSITIONS
        evaluation_at = transitions + 100_000
        with (output / "metrics.jsonl").open("a") as log:
            while transitions < limit:
                sampled = workers.collect(agent, limit - transitions,
                                          curriculum.sigma_max if curriculum is not None else None)
                batches = []
                versions = []
                for worker, batch, episodes in sampled:
                    count = len(batch.returns)
                    transitions += count
                    collected += count
                    if count:
                        batches.append(batch)
                        versions.append(agent.updates)
                    for line in episodes.splitlines():
                        event = json.loads(line)
                        event.update(transitions=transitions, worker=worker)
                        log.write(json.dumps(event) + "\n")
                        monitor.record_episode(event)
                        if curriculum is not None:
                            curriculum.record(event)
                if not batches:
                    raise RuntimeError("All PPO sampler transitions are unhealthy")
                # Window success selects the policy which actually generated these episodes.
                flush_training_monitor(agent, output, metadata, transitions, monitor, force=transitions >= limit)
                losses = agent.update(RolloutBatch.concatenate(batches))
                event = {
                    "event": "learner", "transitions": transitions, "healthy": collected,
                    "updates": agent.updates, "optimizer_steps": agent.optimizer_steps,
                    "rollout_samples": sum(len(b.returns) for b in batches),
                    "actor_version_lag": 0, "rollout_policy_version": versions[0],
                    "transitions_per_s": collected / (time.perf_counter() - started),
                    "learner_gpu_allocated_bytes": torch.cuda.memory_allocated(env.device),
                    "learner_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    **({"sigma_max": curriculum.sigma_max, "curriculum_low_success": curriculum.rates()[0],
                        "curriculum_high_success": curriculum.rates()[1]} if curriculum is not None else {}),
                    **losses,
                }
                log.write(json.dumps(event) + "\n")
                log.flush()
                monitor.record_update(losses, algorithm="ppo")
                monitor.record_runtime({key: value for key, value in event.items() if key not in losses})
                if transitions >= log_at:
                    agent.save(output / "latest.pt", metadata=metadata, transitions=transitions)
                    log_at = transitions + settings["log_every"]
                checkpoint_at = save_periodic_checkpoint(agent, output, metadata, transitions, checkpoint_at)
                if curriculum is not None and (change := curriculum.advance(transitions)) is not None:
                    # Keep the policy that cleared each level before the ceiling rises.
                    agent.save(output / f"level{change['level'] - 1:+d}.pt", metadata=metadata, transitions=transitions)
                    log.write(json.dumps(change) + "\n")
                if settings.get("periodic_evaluation", False) and transitions >= evaluation_at and mode == "graph":
                    validation_checkpoint(agent, env, config, transitions, output, log, monitor)
                    evaluation_at = transitions + 100_000
            workers.close()
            workers = None
            log.write(json.dumps({"event": "sampling_complete", "transitions": transitions,
                                  "healthy": collected, "updates": agent.updates,
                                  "seconds": time.perf_counter() - started}) + "\n")
            flush_training_monitor(agent, output, metadata, transitions, monitor, force=True)
            save_best_checkpoint(agent, output, metadata, transitions, None, final=True)
            agent.save(output / "latest.pt", metadata=metadata, transitions=transitions)
            result = {"algorithm": "ppo", "transitions": transitions, "updates": agent.updates,
                      "checkpoint": str(output / "latest.pt")}
            if mode == "graph":
                best, _ = load_agent(output / "best.pt", env.device)
                initial = validation_checkpoint(best, env, config, transitions, output, log, monitor)
                rows = station_scores(best, env, config["evaluation"]["action_samples"], initial_scores=initial)
                (output / "station-v.json").write_text(json.dumps({
                    "checkpoint": str((output / "best.pt").absolute()), "seed": config["evaluation"]["seed"],
                    "score_kind": "v", "interpretation": "equal grasp mean of state value V", "stations": rows,
                }, indent=2))
                plot_scores(rows, output / "station-v.png", config["evaluation"].get("v_color_limits"))
        return result
    finally:
        if workers is not None:
            workers.close()
        if monitor is not None:
            monitor.close(transitions)
        env.close()
