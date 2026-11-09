"""Independent GAT encoders and a tanh Gaussian policy."""
import math

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GATConv

from ...scenes.rl_observation import NODE_DIM, graph_edges


class GraphEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.gat = GATConv(NODE_DIM, 128, heads=1, edge_dim=5, add_self_loops=False)

    def forward(self, observation):
        nodes, edges, attributes, goals = graph_edges(observation)
        embedding = F.relu(self.gat(nodes, edges, attributes))[goals]
        return torch.cat((embedding, observation.arm_task_state), dim=-1)


def mlp(input_dim, output_dim):
    return nn.Sequential(nn.Linear(input_dim, 256), nn.ReLU(),
                         nn.Linear(256, 256), nn.ReLU(), nn.Linear(256, output_dim))


class Actor(nn.Module):
    def __init__(self, state_dim, action_dim=7):
        super().__init__()
        self.encoder = GraphEncoder()
        self.head = mlp(128 + state_dim, action_dim * 2)

    def forward(self, observation):
        mu, log_std = self.head(self.encoder(observation)).chunk(2, -1)
        return mu, log_std.clamp(-5, 2)

    def sample(self, observation, deterministic=False):
        mu, log_std = self(observation)
        normal = torch.distributions.Normal(mu, log_std.exp())
        u = mu if deterministic else normal.rsample()
        action = u.tanh()
        # Stable exact log(1 - tanh(u)^2), including saturated actions.
        correction = 2 * (math.log(2) - u - F.softplus(-2 * u))
        log_prob = (normal.log_prob(u) - correction).sum(-1)
        return action, log_prob


class Critic(nn.Module):
    def __init__(self, state_dim, action_dim=7):
        super().__init__()
        self.encoder = GraphEncoder()
        self.head = mlp(128 + state_dim + action_dim, 1)

    def forward(self, observation, action):
        return self.head(torch.cat((self.encoder(observation), action), -1)).squeeze(-1)
