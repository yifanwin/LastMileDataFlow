"""Offline Monte Carlo SAC soft values for fixed-Home cube stations."""

from dataclasses import dataclass, fields
from datetime import datetime
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import numpy as np
import torch

from lastmile_dataflow.utils.figures import plt
from .devices import VISIBLE_DEVICE
from .env.offline import initial_observations
from .sac.agent import SACInference


@dataclass
class SoftValueSamples:
    action: torch.Tensor
    log_prob: torch.Tensor
    q1: torch.Tensor
    q2: torch.Tensor

    def values(self, alpha):
        return torch.minimum(self.q1, self.q2) - alpha * self.log_prob


@torch.inference_mode()
def sample_soft_values(agent, observations, *, samples, batch_size, seed):
    """Use a fixed noise bank so sample identities do not depend on batch size."""
    if samples < 2 or batch_size < 1:
        raise ValueError("Need at least two action samples and a positive batch size")
    for network in (agent.actor, agent.q1, agent.q2):
        network.eval()
    count = len(observations)
    expanded = observations.repeat_interleave(samples, dim=0)
    generator = torch.Generator(device=observations.device).manual_seed(seed)
    noise = torch.randn(count * samples, agent.actor.action_dim, device=observations.device, generator=generator)
    chunks = []
    for start in range(0, len(expanded), batch_size):
        obs = expanded[start:start + batch_size]
        action, log_prob = agent.actor.sample(obs, noise=noise[start:start + batch_size])
        chunks.append(SoftValueSamples(action.cpu(), log_prob.cpu(),
                                      agent.q1(obs, action).cpu(), agent.q2(obs, action).cpu()))
    return SoftValueSamples(**{
        f.name: torch.cat([getattr(chunk, f.name) for chunk in chunks]).reshape(count, samples, -1)
        if f.name == "action" else torch.cat([getattr(chunk, f.name) for chunk in chunks]).reshape(count, samples)
        for f in fields(SoftValueSamples)
    })


def summarize_values(stations, sampled, alpha):
    values = sampled.values(alpha).double()
    min_q = torch.minimum(sampled.q1, sampled.q2).double()
    entropy = -alpha * sampled.log_prob.double()
    rows = []
    for row in stations:
        rows.append({**row, "v_mean": None, "v_std": None, "v_sem": None,
                     "q_min_mean": None, "entropy_term_mean": None, "K": values.shape[1]})
    for i, index in enumerate(j for j, row in enumerate(rows) if row["valid"]):
        std = float(values[i].std(correction=1))
        rows[index].update(v_mean=float(values[i].mean()), v_std=std,
                           v_sem=std / values.shape[1] ** 0.5,
                           q_min_mean=float(min_q[i].mean()), entropy_term_mean=float(entropy[i].mean()))
    return rows


def plot_values(rows, target, path, *, transitions):
    valid = [r for r in rows if r["valid"]]
    invalid = [r for r in rows if not r["valid"]]
    fig, axis = plt.subplots(figsize=(8, 7), constrained_layout=True)
    axis.scatter([r["x"] for r in invalid], [r["y"] for r in invalid],
                 c="lightgray", marker="x", label="invalid initial station")
    points = axis.scatter([r["x"] for r in valid], [r["y"] for r in valid],
                          c=[r["v_mean"] for r in valid], marker="s", s=100, cmap="viridis")
    axis.scatter(float(target[0]), float(target[1]), c="orangered", marker="*", s=180, label="cube")
    axis.set(title=f"SAC initial soft value | {transitions:,} transitions | K={valid[0]['K']}\n"
                   "Each base faces the cube; invalid stations are masked",
             xlabel="world x (m)", ylabel="world y (m)", aspect="equal")
    axis.legend(loc="upper left", fontsize=8)
    fig.colorbar(points, ax=axis, label="E[min(Q1, Q2) - alpha log pi] (not success probability)")
    fig.savefig(path, dpi=180)
    plt.close(fig)


@torch.inference_mode()
def score_soft(config, *, checkpoint, gpu, output, samples, batch_size, seed, plot=None):
    """Read configured initial state and frozen networks; never load a simulation."""
    started = time.perf_counter()
    device = f"cuda:{VISIBLE_DEVICE}"
    agent, saved = SACInference.load(checkpoint, device)
    effective = saved["metadata"]["config"]
    if config["scene"] != effective["scene"]:
        raise ValueError("Task scene and Home configuration must match the checkpoint")
    if agent.mode != "flat" or agent.state_dim != 69 or not effective["scene"].get("fixed_base") or not effective["scene"].get("cube"):
        raise ValueError("Offline soft scoring requires a fixed-base 69D flat cube checkpoint")
    if samples < 2 or batch_size < 1:
        raise ValueError("Need at least two action samples and a positive batch size")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    torch.cuda.synchronize()
    loaded = time.perf_counter()
    stations = saved["metadata"]["stations"]
    valid = [row for row in stations if row["valid"]]
    observations = initial_observations(config, valid, device)
    if not torch.isfinite(observations).all():
        raise ValueError("Initial observations contain non-finite values")
    torch.cuda.synchronize()
    constructed = time.perf_counter()
    sampled = sample_soft_values(agent, observations, samples=samples, batch_size=batch_size, seed=seed)
    torch.cuda.synchronize()
    inferred = time.perf_counter()
    alpha = float(agent.alpha)
    rows = summarize_values(stations, sampled, alpha)
    arrays = {f.name: getattr(sampled, f.name).numpy() for f in fields(sampled)}
    if not all(np.isfinite(a).all() for a in arrays.values()):
        raise ValueError("Network samples contain non-finite values")
    np.savez_compressed(output / "samples.npz", observation=observations.cpu().numpy(),
                        station_ids=np.array([r["station_id"] for r in valid]), alpha=np.array(alpha), **arrays)
    result = dict(
        timestamp=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        checkpoint=str(Path(checkpoint).resolve()),
        observation_source="fixed initial state in task configuration",
        initial_observation_config=config["observation"],
        transitions=saved["transitions"], updates=saved["updates"], alpha=alpha,
        seed=seed, physical_gpu=gpu, action_samples=samples, batch_size=batch_size,
        interpretation="E_actor[min online Q1,Q2 - alpha*log_pi]; policy-conditioned soft value",
        std_interpretation="sample standard deviation across actions (ddof=1); not model uncertainty",
        timings=dict(load_seconds=loaded-started, observation_seconds=constructed-loaded,
                     inference_seconds=inferred-constructed),
        config=effective, stations=rows,
    )
    plot_path = Path(plot) if plot is not None else output / "station-value.png"
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    plot_values(rows, config["observation"]["cube_position_world"], plot_path, transitions=saved["transitions"])
    result["plot"] = str(plot_path.resolve())
    result["timings"]["total_seconds"] = time.perf_counter() - started
    (output / "station-value.json").write_text(json.dumps(result, indent=2))
    return result
