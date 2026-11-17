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

    def sample_raw(self, observation):
        mu, log_std = self(observation)
        normal = torch.distributions.Normal(mu, log_std.exp())
        raw = normal.sample()
        return raw, squashed_log_probability(normal, raw)


class Value(nn.Module):
    def __init__(self, state_dim, *, mode="graph"):
        super().__init__()
        self.encoder = GraphEncoder() if mode == "graph" else FlatEncoder()
        self.head = mlp((128 if mode == "graph" else 0) + state_dim, 1)

    def forward(self, observation):
        return self.head(self.encoder(observation)).squeeze(-1)


class Critic(nn.Module):
    def __init__(self, state_dim, action_dim=7, *, mode="graph"):
        super().__init__()
        self.encoder = GraphEncoder() if mode == "graph" else FlatEncoder()
        self.head = mlp((128 if mode == "graph" else 0) + state_dim + action_dim, 1)

    def forward(self, observation, action):
        return self.head(torch.cat((self.encoder(observation), action), -1)).squeeze(-1)
