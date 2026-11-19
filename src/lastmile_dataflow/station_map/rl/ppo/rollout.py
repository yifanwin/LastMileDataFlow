"""Finite-horizon GAE and owned, healthy on-policy training batches."""

from dataclasses import dataclass, fields

import torch

from ..env.observation import GraphObservation


def map_observation(obs, function):
    return obs.map(function) if isinstance(obs, GraphObservation) else function(obs)


def cat_observations(observations):
    first = observations[0]
    if isinstance(first, GraphObservation):
        return GraphObservation(*(torch.cat([getattr(o, f.name) for o in observations]) for f in fields(first)))
    return torch.cat(observations)


def normalize_advantages(advantages):
    return (advantages - advantages.mean()) / (advantages.std(correction=0) + 1e-8)


def gae(rewards, values, next_values, terminated, done, valid, gamma, gae_lambda):
    """Time-major tensors; only true terminals drop the bootstrap (time limits keep it),
    while every episode end and invalid step breaks the recursion."""
    advantages = torch.zeros_like(rewards)
    carry = torch.zeros_like(rewards[0])
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + gamma * torch.where(terminated[t], 0., next_values[t]) - values[t]
        carry = torch.where(valid[t], delta + gamma * gae_lambda * (~done[t]) * carry, 0.)
        advantages[t] = carry
    return advantages, advantages + values


@dataclass
class RolloutBatch:
    obs: torch.Tensor | GraphObservation
    raw_action: torch.Tensor
    old_log_prob: torch.Tensor
    old_value: torch.Tensor
    advantages: torch.Tensor
    returns: torch.Tensor

    def __getitem__(self, index):
        return RolloutBatch(*(getattr(self, f.name)[index] for f in fields(self)))

    def to(self, device):
        return RolloutBatch(map_observation(self.obs, lambda x: x.to(device)), *(
            getattr(self, f.name).to(device) for f in fields(self)[1:]
        ))

    @classmethod
    def concatenate(cls, batches):
        return cls(cat_observations([b.obs for b in batches]), *(
            torch.cat([getattr(b, f.name) for b in batches]) for f in fields(cls)[1:]
        ))
