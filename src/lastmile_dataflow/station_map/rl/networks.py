"""Independent GAT encoders and a tanh Gaussian policy."""

import math

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GATConv

from .env.observation import NODE_DIM, STATION_DIM, STATION_VECTOR_DIM, graph_edges
from .env.scene_graph import MAX_NODES, NODE_DIM as ENTITY_DIM


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



class SceneEncoder(nn.Module):
    """Goal and base queries attend over entity nodes (plus a learned empty slot) with a learned
    distance penalty, and max-pool a per-node proximity response; four 64D reads become 128D.

    ``base_dim`` > 0 projects a base-query summary (control side); 0 uses a learned vector (quality).
    """

    def __init__(self, goal_dim=13, base_dim=8, width=64, heads=4, output=128):
        super().__init__()
        self.heads, self.width = heads, width
        self.node = nn.Sequential(nn.Linear(ENTITY_DIM, width), nn.ReLU(), nn.Linear(width, width))
        self.key, self.value = nn.Linear(width, width), nn.Linear(width, width)
        self.goal_query = nn.Linear(goal_dim, width)
        self.base_query = nn.Linear(base_dim, width) if base_dim else None
        self.base_vector = None if base_dim else nn.Parameter(torch.zeros(width))
        self.empty_key, self.empty_value = nn.Parameter(torch.zeros(width)), nn.Parameter(torch.zeros(width))
        self.distance_scale = nn.Parameter(torch.zeros(2, heads))  # softplus(0) ~ 0.69 per metre
        self.risk = nn.Sequential(nn.Linear(width + 1, width), nn.ReLU(), nn.Linear(width, width))
        self.output = nn.Sequential(nn.Linear(4 * width, output), nn.ReLU())

    def forward(self, goal, base, nodes, mask, distance):
        batch = len(goal)
        valid = mask > 0.5
        embedded = self.node(nodes)                                                   # (B, N, W)
        query_base = self.base_query(base) if self.base_query is not None else self.base_vector.expand(batch, -1)
        query = torch.stack((self.goal_query(goal), query_base), 1)                   # (B, 2, W)
        keys = torch.cat((self.empty_key.expand(batch, 1, -1), self.key(embedded)), 1)
        values = torch.cat((self.empty_value.expand(batch, 1, -1), self.value(embedded)), 1)
        split = lambda x: x.reshape(batch, x.shape[1], self.heads, -1)
        logits = torch.einsum("bqhd,bnhd->bqhn", split(query), split(keys)) / math.sqrt(self.width // self.heads)
        penalty = F.softplus(self.distance_scale)[None, :, :, None] * distance[:, :, None]   # (B, 2, H, N)
        logits = logits - torch.cat((torch.zeros_like(penalty[..., :1]), penalty), -1)
        slots = torch.cat((torch.ones_like(valid[:, :1]), valid), 1)
        weights = logits.masked_fill(~slots[:, None, None], float("-inf")).softmax(-1)
        attended = torch.einsum("bqhn,bnhd->bqhd", weights, split(values)).reshape(batch, 2, -1)
        response = self.risk(torch.cat((values[:, None, 1:].expand(-1, 2, -1, -1), distance[..., None]), -1))
        pooled = response.masked_fill(~valid[:, None, :, None], float("-inf")).amax(2)
        pooled = torch.where(valid.any(-1)[:, None, None], pooled, torch.zeros_like(pooled))
        return self.output(torch.cat((attended[:, 0], pooled[:, 0], attended[:, 1], pooled[:, 1]), -1))


def split_station(observation):
    """Vector (B, 77), nodes (B, 20, 19), mask (B, 20) and distances (B, 2, 20) of the station layout."""
    v = STATION_VECTOR_DIM
    n = MAX_NODES * ENTITY_DIM
    return (observation[:, :v], observation[:, v:v + n].reshape(-1, MAX_NODES, ENTITY_DIM),
            observation[:, v + n:v + n + MAX_NODES], observation[:, v + n + MAX_NODES:].reshape(-1, 2, MAX_NODES))


class StationBackbone(nn.Module):
    """Control-side encoder: h_env (128) followed by the normalized 77D vector (205D)."""

    output_dim = 128 + STATION_VECTOR_DIM

    def __init__(self, normalizer):
        super().__init__()
        self.normalizer = normalizer if normalizer is not None else nn.Identity()
        self.scene = SceneEncoder(goal_dim=13, base_dim=8)

    def forward(self, observation):
        vector, nodes, mask, distance = split_station(observation)
        vector = self.normalizer(vector)
        # Base query: normalized arm and lift positions (first proprioception fields).
        return torch.cat((self.scene(vector[:, :13], vector[:, 35:43], nodes, mask, distance), vector), -1)


class QualityMember(nn.Module):
    """One ensemble member: nominal scene, target, grasp and sigma -> success logit."""

    def __init__(self):
        super().__init__()
        self.scene = SceneEncoder(goal_dim=13, base_dim=0)
        self.head = nn.Sequential(nn.Linear(128 + 23, 128), nn.ReLU(), nn.Linear(128, 64), nn.ReLU(), nn.Linear(64, 1))

    def forward(self, target, grasp, sigma, nodes, mask, distance):
        h = self.scene(target, None, nodes, mask, distance)
        return self.head(torch.cat((h, target, grasp, sigma), -1)).squeeze(-1)


def control_encoder(state_dim, mode, normalizer):
    """Encoder and its output width for one control branch; the station layout owns its normalizer."""
    if mode == "graph":
        return GraphEncoder(), 128 + state_dim
    if state_dim == STATION_DIM:
        return StationBackbone(normalizer), StationBackbone.output_dim
    return FlatEncoder(), state_dim


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
        station = state_dim == STATION_DIM
        self.encoder, width = control_encoder(state_dim, mode, normalizer)
        self.normalizer = normalizer if normalizer is not None and not station else nn.Identity()
        self.head = mlp(width, action_dim)
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
        station = state_dim == STATION_DIM
        self.encoder, width = control_encoder(state_dim, mode, normalizer)
        self.normalizer = normalizer if normalizer is not None and not station else nn.Identity()
        self.head = mlp(width, 1)
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
