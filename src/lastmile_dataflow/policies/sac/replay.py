"""Uniform replay with owned storage and true pre-reset next observations."""
from ...scenes.rl_observation import REWARD_COMPONENTS
from dataclasses import dataclass, fields

import torch

from ...scenes.rl_observation import GraphObservation


@dataclass
class Transition:
    obs: GraphObservation
    action: torch.Tensor
    reward: torch.Tensor
    next_obs: GraphObservation
    terminated: torch.Tensor
    truncated: torch.Tensor
    reward_components: torch.Tensor | None = None
    station_id: torch.Tensor | None = None


    def map(self, function):
        return Transition(*(value.map(function) if isinstance(value, GraphObservation)
                            else function(value) if value is not None else None
                            for value in (getattr(self, f.name) for f in fields(self))))

    def __getitem__(self, index):
        return self.map(lambda value: value[index])

    def to(self, device):
        return self.map(lambda value: value.detach().to(device).clone())


class Replay:
    def __init__(self, capacity, example, action_dim=7):
        self.capacity = capacity
        self.size = self.cursor = 0
        self.device = example.nodes.device
        def allocate(value):
            return torch.empty((capacity, *value.shape[1:]), dtype=value.dtype, device=self.device)
        self.obs = example.map(allocate)
        self.next_obs = example.map(allocate)
        self.action = torch.empty(capacity, action_dim, device=self.device)
        self.reward = torch.empty(capacity, device=self.device)
        self.terminated = torch.empty(capacity, dtype=torch.bool, device=self.device)
        self.truncated = torch.empty_like(self.terminated)
        self.reward_components = torch.empty(capacity, len(REWARD_COMPONENTS), device=self.device)
        self.station_id = torch.empty(capacity, dtype=torch.long, device=self.device)

    @torch.no_grad()
    def add(self, transition):
        count = len(transition.reward)
        # A vector batch may exceed a deliberately small debug replay.
        start = max(0, count - self.capacity)
        ids = (torch.arange(count - start, device=self.device) + self.cursor) % self.capacity
        for target, source in ((self.obs, transition.obs), (self.next_obs, transition.next_obs)):
            for name in ("nodes", "node_mask", "arm_task_state"):
                getattr(target, name)[ids] = getattr(source, name)[start:]
        for name in ("action", "reward", "terminated", "truncated"):
            getattr(self, name)[ids] = getattr(transition, name)[start:]
        self.reward_components[ids] = (0 if transition.reward_components is None else transition.reward_components[start:])
        self.station_id[ids] = (-1 if transition.station_id is None else transition.station_id[start:])
        self.cursor = (self.cursor + len(ids)) % self.capacity
        self.size = min(self.capacity, self.size + len(ids))

    def sample(self, batch_size):
        ids = torch.randint(self.size, (batch_size,), device=self.device)
        return Transition(self.obs[ids], self.action[ids], self.reward[ids], self.next_obs[ids],
                          self.terminated[ids], self.truncated[ids], self.reward_components[ids], self.station_id[ids])
