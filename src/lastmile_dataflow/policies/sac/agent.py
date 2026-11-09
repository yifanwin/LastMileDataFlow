"""SAC double-Q updates and parameter/optimizer warm resume."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import random

import numpy as np
import torch
from torch.nn import functional as F

from .networks import Actor, Critic


@dataclass(frozen=True)
class SACConfig:
    gamma: float = .995
    tau: float = .005
    actor_lr: float = 3e-4
    critic_lr: float = 3e-4
    alpha_lr: float = 1e-4
    initial_alpha: float = .1
    target_entropy: float = -7.


def bellman_target(reward, terminated, next_q, next_log_prob, alpha, gamma):
    return reward + gamma * (~terminated).float() * (next_q - alpha * next_log_prob)


class SAC:
    def __init__(self, state_dim, device, config=SACConfig()):
        self.config = config
        self.state_dim = state_dim
        self.device = torch.device(device)
        self.actor = Actor(state_dim).to(device)
        self.q1, self.q2 = (Critic(state_dim).to(device) for _ in range(2))
        self.target1, self.target2 = deepcopy(self.q1), deepcopy(self.q2)
        self.target1.requires_grad_(False)
        self.target2.requires_grad_(False)
        self.log_alpha = torch.tensor(np.log(config.initial_alpha), dtype=torch.float32,
                                      device=device, requires_grad=True)
        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=config.actor_lr)
        self.critic_optimizer = torch.optim.Adam(
            [*self.q1.parameters(), *self.q2.parameters()], lr=config.critic_lr)
        self.alpha_optimizer = torch.optim.Adam([self.log_alpha], lr=config.alpha_lr)
        self.updates = 0

    @property
    def alpha(self):
        return self.log_alpha.exp().detach()

    def update(self, batch):
        cfg = self.config
        with torch.no_grad():
            next_action, next_log_prob = self.actor.sample(batch.next_obs)
            next_q = torch.minimum(self.target1(batch.next_obs, next_action),
                                   self.target2(batch.next_obs, next_action))
            target = bellman_target(batch.reward, batch.terminated, next_q,
                                    next_log_prob, self.alpha, cfg.gamma)
        critic_loss = (F.mse_loss(self.q1(batch.obs, batch.action), target) +
                       F.mse_loss(self.q2(batch.obs, batch.action), target))
        self.critic_optimizer.zero_grad(set_to_none=True)
        critic_loss.backward()
        self.critic_optimizer.step()

        self.q1.requires_grad_(False)
        self.q2.requires_grad_(False)
        action, log_prob = self.actor.sample(batch.obs)
        q = torch.minimum(self.q1(batch.obs, action), self.q2(batch.obs, action))
        actor_loss = (self.alpha * log_prob - q).mean()
        self.actor_optimizer.zero_grad(set_to_none=True)
        actor_loss.backward()
        self.actor_optimizer.step()
        self.q1.requires_grad_(True)
        self.q2.requires_grad_(True)

        alpha_loss = -(self.log_alpha * (log_prob.detach() + cfg.target_entropy)).mean()
        self.alpha_optimizer.zero_grad(set_to_none=True)
        alpha_loss.backward()
        self.alpha_optimizer.step()
        with torch.no_grad():
            for online, target_net in ((self.q1, self.target1), (self.q2, self.target2)):
                for parameter, target_parameter in zip(online.parameters(), target_net.parameters()):
                    target_parameter.lerp_(parameter, cfg.tau)
        self.updates += 1
        return {"critic_loss": critic_loss.detach(), "actor_loss": actor_loss.detach(),
                "alpha_loss": alpha_loss.detach(), "alpha": self.alpha}

    def save(self, path, *, metadata, transitions):
        torch.save({
            "state_dim": self.state_dim, "sac_config": asdict(self.config),
            "networks": {name: getattr(self, name).state_dict() for name in
                         ("actor", "q1", "q2", "target1", "target2")},
            "optimizers": {name: getattr(self, name + "_optimizer").state_dict()
                           for name in ("actor", "critic", "alpha")},
            "log_alpha": self.log_alpha.detach(), "updates": self.updates,
            "transitions": transitions, "metadata": metadata,
            "rng": {"python": random.getstate(), "numpy": np.random.get_state(),
                    "torch": torch.get_rng_state(),
                    "cuda": torch.cuda.get_rng_state(self.device) if self.device.type == 'cuda' else None},
            "resume_semantics": "parameter/optimizer warm resume; reset environment and replay",
        }, path)

    @classmethod
    def load(cls, path, device, *, restore_rng=False):
        saved = torch.load(path, map_location=device, weights_only=False)
        agent = cls(saved["state_dim"], device, SACConfig(**saved["sac_config"]))
        for name, state in saved["networks"].items():
            getattr(agent, name).load_state_dict(state)
        for name, state in saved["optimizers"].items():
            getattr(agent, name + "_optimizer").load_state_dict(state)
        with torch.no_grad():
            agent.log_alpha.copy_(saved["log_alpha"])
        agent.updates = saved["updates"]
        if restore_rng:
            rng = saved["rng"]
            random.setstate(rng["python"])
            np.random.set_state(rng["numpy"])
            torch.set_rng_state(rng["torch"].cpu())
            if rng["cuda"] is not None and agent.device.type == 'cuda':
                torch.cuda.set_rng_state(rng["cuda"].cpu(), agent.device)
        return agent, saved
