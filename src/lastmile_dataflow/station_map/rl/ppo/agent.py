"""Clipped PPO: global advantage normalization, KL-adaptive Actor learning rate and entropy bonus."""

from dataclasses import asdict

import torch
from torch.distributions import Normal, kl_divergence
from torch.nn import functional as F

from ..config import PPOConfig, action_fields
from ..checkpoint import input_definition, rng_state, restore_rng_state
from ..networks import GaussianActor, ObservationNormalizer, Value, squashed_log_probability
from .rollout import normalize_advantages


def ppo_networks(state_dim, action_dim, mode, config):
    """Actor and V share one observation normalizer (flat mode only)."""
    normalizer = ObservationNormalizer(state_dim) if mode == "flat" and config.normalize_observations else None
    actor = GaussianActor(state_dim, action_dim, mode=mode, init_std=config.init_std, normalizer=normalizer)
    return actor, Value(state_dim, mode=mode, normalizer=normalizer)


def gaussian_kl(old_mu, old_std, normal):
    # tanh is a bijection, so the squashed-policy KL equals the pre-tanh Gaussian KL.
    return kl_divergence(Normal(old_mu, old_std), normal).sum(-1).mean()


def adapt_learning_rate(lr, kl, config):
    if kl > 2 * config.desired_kl:
        return max(config.lr_min, lr / 1.5)
    if kl < config.desired_kl / 2:
        return min(config.lr_max, lr * 1.5)
    return lr


def clipped_policy_loss(log_ratio, advantages, clip_coef):
    ratio = log_ratio.exp()
    return -torch.minimum(ratio * advantages, ratio.clamp(1 - clip_coef, 1 + clip_coef) * advantages).mean()


class PPO:
    algorithm = "ppo"

    def __init__(self, state_dim, device, config=PPOConfig(), *, mode="graph", action_dim=7):
        if mode not in ("graph", "flat"):
            raise ValueError(f"Unknown PPO observation mode: {mode}")
        self.state_dim, self.action_dim, self.mode, self.config = state_dim, action_dim, mode, config
        self.device = torch.device(device)
        self.actor, self.value = (network.to(device) for network in ppo_networks(state_dim, action_dim, mode, config))
        self.normalizer = self.actor.normalizer if isinstance(self.actor.normalizer, ObservationNormalizer) else None
        self.actor_lr = config.actor_lr
        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=config.actor_lr)
        self.value_optimizer = torch.optim.Adam(self.value.parameters(), lr=config.value_lr)
        self.updates = 0
        self.optimizer_steps = 0

    def _step(self, mini):
        cfg = self.config
        normal = self.actor.distribution(mini.obs)
        kl = gaussian_kl(mini.old_mu, mini.old_std, normal).detach()
        self.actor_lr = adapt_learning_rate(self.actor_lr, float(kl), cfg)
        for group in self.actor_optimizer.param_groups:
            group["lr"] = self.actor_lr
        log_ratio = squashed_log_probability(normal, mini.raw_action) - mini.old_log_prob
        actor_loss = clipped_policy_loss(log_ratio, mini.advantages, cfg.clip_coef)
        entropy = normal.entropy().sum(-1).mean()
        self.actor_optimizer.zero_grad(set_to_none=True)
        (actor_loss - cfg.entropy_coef * entropy).backward()
        actor_norm = torch.nn.utils.clip_grad_norm_(self.actor.parameters(), cfg.actor_max_grad_norm)
        self.actor_optimizer.step()
        value_loss = F.mse_loss(self.value(mini.obs), mini.returns)
        self.value_optimizer.zero_grad(set_to_none=True)
        (cfg.value_coef * value_loss).backward()
        value_norm = torch.nn.utils.clip_grad_norm_(self.value.parameters(), cfg.value_max_grad_norm)
        self.value_optimizer.step()
        self.optimizer_steps += 1
        return dict(
            actor_loss=actor_loss, value_loss=value_loss, entropy=entropy, minibatch_kl=kl,
            clip_fraction=((log_ratio.exp() - 1).abs() > cfg.clip_coef).float().mean(),
            actor_grad_norm=actor_norm, value_grad_norm=value_norm,
        )

    def update(self, batch):
        cfg = self.config
        # Exactly one normalization over every worker's valid samples, before shuffling.
        batch.advantages = normalize_advantages(batch.advantages)
        count = len(batch.returns)
        totals, weight, minibatches = {}, 0, 0
        for _ in range(cfg.epochs):
            indices = torch.randperm(count)
            for start in range(0, count, cfg.minibatch_size):
                mini = batch[indices[start:start + cfg.minibatch_size]].to(self.device)
                n = len(mini.returns)
                for key, value in self._step(mini).items():
                    totals[key] = totals.get(key, 0.) + float(value.detach()) * n
                weight += n
                minibatches += 1
        global_kl = 0.
        with torch.no_grad():
            for start in range(0, count, cfg.minibatch_size):
                mini = batch[slice(start, start + cfg.minibatch_size)].to(self.device)
                global_kl += float(gaussian_kl(mini.old_mu, mini.old_std, self.actor.distribution(mini.obs))) * len(mini.returns)
            variance = batch.returns.var(correction=0)
            explained = 1 - (batch.returns - batch.old_value).var(correction=0) / variance if variance > 0 else torch.tensor(0.)
        # Statistics change only after this rollout's epochs, so old log-probs stay consistent.
        if self.normalizer is not None:
            self.normalizer.update(batch.obs.to(self.device))
        self.updates += 1
        return {
            **{key: value / max(weight, 1) for key, value in totals.items()},
            "global_kl": global_kl / count, "explained_variance": float(explained),
            "minibatches": minibatches, "actor_lr": self.actor_lr,
            "policy_std": float(self.actor.log_std.clamp(-5, 1).exp().mean()),
        }

    def save(self, path, *, metadata, transitions):
        torch.save({
            "algorithm": self.algorithm, "state_dim": self.state_dim, "mode": self.mode,
            "action_dim": self.action_dim, "action_definition": action_fields(self.action_dim),
            "input_definition": input_definition(self.mode, self.state_dim), "ppo_config": asdict(self.config),
            "networks": {name: getattr(self, name).state_dict() for name in ("actor", "value")},
            "optimizers": {name: getattr(self, name + "_optimizer").state_dict() for name in ("actor", "value")},
            "actor_lr": self.actor_lr, "updates": self.updates, "optimizer_steps": self.optimizer_steps,
            "transitions": transitions, "metadata": metadata, "rng": rng_state(self.device),
            "resume_semantics": "parameter/optimizer/normalizer warm resume; reset environment and rollout",
        }, path)

    @classmethod
    def load(cls, path, device, *, restore_rng=False, saved=None):
        saved = torch.load(path, map_location=device, weights_only=False) if saved is None else saved
        if saved.get("algorithm") != "ppo":
            raise ValueError("PPO requires a PPO checkpoint")
        mode, dim, action_dim = saved["mode"], saved["state_dim"], saved["action_dim"]
        if saved["input_definition"] != input_definition(mode, dim):
            raise ValueError("Checkpoint observation definition does not match PPO mode")
        if saved["action_definition"] != action_fields(action_dim):
            raise ValueError("Checkpoint action definition does not match PPO mode")
        agent = cls(dim, device, PPOConfig(**saved["ppo_config"]), mode=mode, action_dim=action_dim)
        for name, state in saved["networks"].items():
            getattr(agent, name).load_state_dict(state)
        for name, state in saved["optimizers"].items():
            getattr(agent, name + "_optimizer").load_state_dict(state)
        agent.actor_lr = saved["actor_lr"]
        agent.updates, agent.optimizer_steps = saved["updates"], saved["optimizer_steps"]
        if restore_rng:
            restore_rng_state(saved["rng"], agent.device)
        return agent, saved
