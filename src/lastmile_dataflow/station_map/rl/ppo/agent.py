"""Clipped PPO: global advantage normalization, separate Actor/V clipping and KL stop."""

from dataclasses import asdict

import torch
from torch.nn import functional as F

from ..config import PPOConfig
from ..checkpoint import input_definition, rng_state, restore_rng_state
from ..networks import Actor, Value
from .rollout import normalize_advantages


def approximate_kl(log_ratio):
    return (log_ratio.exp() - 1 - log_ratio).mean()


def clipped_policy_loss(log_ratio, advantages, clip_coef):
    ratio = log_ratio.exp()
    return -torch.minimum(ratio * advantages, ratio.clamp(1 - clip_coef, 1 + clip_coef) * advantages).mean()


class PPO:
    algorithm = "ppo"

    def __init__(self, state_dim, device, config=PPOConfig(), *, mode="graph"):
        if mode not in ("graph", "flat"):
            raise ValueError(f"Unknown PPO observation mode: {mode}")
        self.state_dim, self.mode, self.config = state_dim, mode, config
        self.device = torch.device(device)
        self.actor = Actor(state_dim, mode=mode).to(device)
        self.value = Value(state_dim, mode=mode).to(device)
        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=config.actor_lr)
        self.value_optimizer = torch.optim.Adam(self.value.parameters(), lr=config.value_lr)
        self.updates = 0
        self.optimizer_steps = 0
        self.kl_early_stops = 0

    def update(self, batch):
        cfg = self.config
        # Exactly one normalization over every worker's valid samples, before shuffling.
        batch.advantages = normalize_advantages(batch.advantages)
        count = len(batch.returns)
        totals = {key: 0. for key in (
            "actor_loss", "value_loss", "entropy", "minibatch_kl", "clip_fraction",
            "actor_grad_norm", "value_grad_norm",
        )}
        weight = epochs_completed = minibatches = 0
        stopped = False
        max_kl = 0.
        for epoch in range(cfg.epochs):
            indices = torch.randperm(count)
            for start in range(0, count, cfg.minibatch_size):
                mini = batch[indices[start:start + cfg.minibatch_size]].to(self.device)
                log_prob = self.actor.log_probability(mini.obs, mini.raw_action)
                log_ratio = log_prob - mini.old_log_prob
                kl = approximate_kl(log_ratio).detach()
                max_kl = max(max_kl, float(kl))
                if kl > cfg.target_kl:
                    stopped = True
                    break
                actor_loss = clipped_policy_loss(log_ratio, mini.advantages, cfg.clip_coef)
                # Fresh current-policy sample estimates squashed-policy entropy.
                _, entropy_log_prob = self.actor.sample(mini.obs)
                entropy = -entropy_log_prob.mean()
                value_loss = F.mse_loss(self.value(mini.obs), mini.returns)
                self.actor_optimizer.zero_grad(set_to_none=True)
                (actor_loss - cfg.entropy_coef * entropy).backward()
                actor_norm = torch.nn.utils.clip_grad_norm_(self.actor.parameters(), cfg.actor_max_grad_norm)
                self.actor_optimizer.step()
                self.value_optimizer.zero_grad(set_to_none=True)
                (cfg.value_coef * value_loss).backward()
                value_norm = torch.nn.utils.clip_grad_norm_(self.value.parameters(), cfg.value_max_grad_norm)
                self.value_optimizer.step()
                n = len(mini.returns)
                metrics = dict(
                    actor_loss=actor_loss, value_loss=value_loss, entropy=entropy, minibatch_kl=kl,
                    clip_fraction=((log_ratio.exp() - 1).abs() > cfg.clip_coef).float().mean(),
                    actor_grad_norm=actor_norm, value_grad_norm=value_norm,
                )
                for key, value in metrics.items():
                    totals[key] += float(value.detach()) * n
                weight += n
                minibatches += 1
                self.optimizer_steps += 1
            if stopped:
                break
            epochs_completed += 1
        global_kl = 0.
        with torch.no_grad():
            for start in range(0, count, cfg.minibatch_size):
                mini = batch[slice(start, start + cfg.minibatch_size)].to(self.device)
                n = len(mini.returns)
                global_kl += float(approximate_kl(self.actor.log_probability(mini.obs, mini.raw_action) - mini.old_log_prob)) * n
            variance = batch.returns.var(correction=0)
            explained = 1 - (batch.returns - batch.old_value).var(correction=0) / variance if variance > 0 else torch.tensor(0.)
        self.updates += 1
        self.kl_early_stops += int(stopped)
        return {
            **{key: value / max(weight, 1) for key, value in totals.items()},
            "global_kl": global_kl / count, "explained_variance": float(explained),
            "epochs_completed": epochs_completed, "minibatches": minibatches,
            "kl_early_stop": int(stopped), "kl_early_stops": self.kl_early_stops, "max_minibatch_kl": max_kl,
        }

    def save(self, path, *, metadata, transitions):
        torch.save({
            "algorithm": self.algorithm, "state_dim": self.state_dim, "mode": self.mode,
            "input_definition": input_definition(self.mode), "ppo_config": asdict(self.config),
            "networks": {name: getattr(self, name).state_dict() for name in ("actor", "value")},
            "optimizers": {name: getattr(self, name + "_optimizer").state_dict() for name in ("actor", "value")},
            "updates": self.updates, "optimizer_steps": self.optimizer_steps,
            "kl_early_stops": self.kl_early_stops,
            "transitions": transitions, "metadata": metadata, "rng": rng_state(self.device),
            "resume_semantics": "parameter/optimizer warm resume; reset environment and rollout",
        }, path)

    @classmethod
    def load(cls, path, device, *, restore_rng=False, saved=None):
        saved = torch.load(path, map_location=device, weights_only=False) if saved is None else saved
        if saved.get("algorithm") != "ppo":
            raise ValueError("PPO requires a PPO checkpoint")
        mode = saved["mode"]
        if saved["input_definition"] != input_definition(mode):
            raise ValueError("Checkpoint observation definition does not match PPO mode")
        agent = cls(saved["state_dim"], device, PPOConfig(**saved["ppo_config"]), mode=mode)
        for name, state in saved["networks"].items():
            getattr(agent, name).load_state_dict(state)
        for name, state in saved["optimizers"].items():
            getattr(agent, name + "_optimizer").load_state_dict(state)
        agent.kl_early_stops = saved.get("kl_early_stops", 0)
        agent.updates, agent.optimizer_steps = saved["updates"], saved["optimizer_steps"]
        if restore_rng:
            restore_rng_state(saved["rng"], agent.device)
        return agent, saved
