"""Algorithm-aware checkpoints and shared random state."""

from importlib import import_module
import random

import numpy as np
import torch

from .env.observation import ARM_STATE_FIELDS, FLAT_FIELDS, NODE_DIM


def input_definition(mode):
    return ("flat", FLAT_FIELDS) if mode == "flat" else ("graph", NODE_DIM, ARM_STATE_FIELDS)


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def rng_state(device):
    return {
        "python": random.getstate(), "numpy": np.random.get_state(), "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state(device) if device.type == "cuda" else None,
    }


def restore_rng_state(rng, device):
    random.setstate(rng["python"])
    np.random.set_state(rng["numpy"])
    torch.set_rng_state(rng["torch"].cpu())
    if rng["cuda"] is not None and device.type == "cuda":
        torch.cuda.set_rng_state(rng["cuda"].cpu(), device)


def load_agent(path, device, *, restore_rng=False, algorithm=None):
    saved = torch.load(path, map_location=device, weights_only=False)
    kind = saved.get("algorithm", "sac")
    if algorithm is not None and algorithm != kind:
        raise ValueError(f"Cannot resume {kind} checkpoint with {algorithm}")
    if kind not in ("sac", "ppo"):
        raise ValueError(f"Unknown checkpoint algorithm: {kind}")
    module = import_module(f".{kind}.agent", package=__package__)
    agent_class = getattr(module, kind.upper())
    return agent_class.load(path, device, restore_rng=restore_rng, saved=saved)
