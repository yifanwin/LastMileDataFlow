"""Independent GAT encoders and a tanh Gaussian policy."""

import math

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GATConv

from .env.observation import NODE_DIM, graph_edges


class GraphEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.gat = GATConv(NODE_DIM, 128, heads=1, edge_dim=5, add_self_loops=False)

    def forward(self, observation):
        nodes, edges, attributes, goals = graph_edges(observation)
        embedding = F.relu(self.gat(nodes, edges, attributes))[goals]
        return torch.cat((embedding, observation.arm_task_state), dim=-1)


class FlatEncoder(nn.Module):
    def forward(self, observation):
        return observation


def mlp(input_dim, output_dim):
    return nn.Sequential(
        nn.Linear(input_dim, 256), nn.ReLU(), nn.Linear(256, 256), nn.ReLU(), nn.Linear(256, output_dim)
    )


def squashed_log_probability(normal, raw_action):
    correction = 2 * (math.log(2) - raw_action - F.softplus(-2 * raw_action))
    return (normal.log_prob(raw_action) - correction).sum(-1)


class Actor(nn.Module):
    def __init__(self, state_dim, action_dim=7, *, mode="graph"):
        super().__init__()
        self.action_dim = action_dim
        self.encoder = GraphEncoder() if mode == "graph" else FlatEncoder()
        self.head = mlp((128 if mode == "graph" else 0) + state_dim, action_dim * 2)

    def forward(self, observation):
        mu, log_std = self.head(self.encoder(observation)).chunk(2, -1)
        return mu, log_std.clamp(-5, 2)

    def sample(self, observation, deterministic=False, *, noise=None):
        mu, log_std = self(observation)
        normal = torch.distributions.Normal(mu, log_std.exp())
        if deterministic:
            u = mu
        else:
            u = normal.rsample() if noise is None else mu + log_std.exp() * noise
        return u.tanh(), squashed_log_probability(normal, u)

    def log_probability(self, observation, raw_action):
        mu, log_std = self(observation)
        normal = torch.distributions.Normal(mu, log_std.exp())
        return squashed_log_probability(normal, raw_action)



class ObservationNormalizer(nn.Module):
    """Running mean/variance of flat observations, updated only by the PPO Learner."""

    def __init__(self, dim, *, clip=10.0, min_std=1e-2):
        super().__init__()
        self.clip, self.min_std = clip, min_std
        self.register_buffer("mean", torch.zeros(dim))
        self.register_buffer("var", torch.ones(dim))
        self.register_buffer("count", torch.zeros(()))

    def forward(self, observation):
        std = self.var.sqrt().clamp_min(self.min_std)
        return ((observation - self.mean) / std).clamp(-self.clip, self.clip)

    @torch.no_grad()
    def update(self, observation):
        n = observation.shape[0]
        mean, var = observation.mean(0), observation.var(0, correction=0)
        total = self.count + n
        delta = mean - self.mean
        self.var.copy_((self.var * self.count + var * n + delta.square() * self.count * n / total) / total)
        self.mean.add_(delta * n / total)
        self.count.copy_(total)


def orthogonal_init(network, output_gain):
    linears = [layer for layer in network.modules() if isinstance(layer, nn.Linear)]
    for layer in linears:
        nn.init.orthogonal_(layer.weight, math.sqrt(2) if layer is not linears[-1] else output_gain)
        nn.init.zeros_(layer.bias)


class GaussianActor(nn.Module):
    """PPO policy tanh(N(mu(s), std)) with a state-independent learnable log std."""

    def __init__(self, state_dim, action_dim=7, *, mode="graph", init_std=1.0, normalizer=None):
        super().__init__()
        self.action_dim = action_dim
        self.encoder = GraphEncoder() if mode == "graph" else FlatEncoder()
        self.normalizer = normalizer if normalizer is not None else nn.Identity()
        self.head = mlp((128 if mode == "graph" else 0) + state_dim, action_dim)
        self.log_std = nn.Parameter(torch.as_tensor(init_std, dtype=torch.float32).log().expand(action_dim).clone())
        orthogonal_init(self.head, 0.01)

    def distribution(self, observation):
        mu = self.head(self.encoder(self.normalizer(observation)))
        return torch.distributions.Normal(mu, self.log_std.clamp(-5, 1).exp().expand_as(mu))

    def sample(self, observation, deterministic=False, *, noise=None):
        normal = self.distribution(observation)
        if deterministic:
            u = normal.loc
        else:
            u = normal.sample() if noise is None else normal.loc + normal.scale * noise
        return u.tanh(), squashed_log_probability(normal, u)

    def log_probability(self, observation, raw_action):
        return squashed_log_probability(self.distribution(observation), raw_action)


class Value(nn.Module):
    def __init__(self, state_dim, *, mode="graph", normalizer=None):
        super().__init__()
        self.encoder = GraphEncoder() if mode == "graph" else FlatEncoder()
        self.normalizer = normalizer if normalizer is not None else nn.Identity()
        self.head = mlp((128 if mode == "graph" else 0) + state_dim, 1)
        orthogonal_init(self.head, 1.0)

    def forward(self, observation):
        return self.head(self.encoder(self.normalizer(observation))).squeeze(-1)


class Critic(nn.Module):
    def __init__(self, state_dim, action_dim=7, *, mode="graph"):
        super().__init__()
        self.action_dim = action_dim
        self.encoder = GraphEncoder() if mode == "graph" else FlatEncoder()
        self.head = mlp((128 if mode == "graph" else 0) + state_dim + action_dim, 1)

    def forward(self, observation, action):
        return self.head(torch.cat((self.encoder(observation), action), -1)).squeeze(-1)
