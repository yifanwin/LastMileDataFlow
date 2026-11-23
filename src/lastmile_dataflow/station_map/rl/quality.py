"""Stage 3: frozen-Actor success labels, the five-member quality ensemble and the Q_phys / U maps.

Labels are strict terminal outcomes of one episode per (nominal station, grasp, sigma) request.
Inputs are nominal: the settled target and entity graph expressed in the nominal base frame, the
executed candidate grasp and sigma; the actual docking, noise draws and aligned variant never enter.
"""

from dataclasses import dataclass, fields
import json
import math
import multiprocessing as mp
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from .networks import QualityMember

SIGMA_POINTS = (0.0, 0.25, 0.5, 0.75, 1.0)
SIGMA_WEIGHTS = (0.125, 0.25, 0.25, 0.25, 0.125)  # trapezoid over a uniform sigma spectrum


@dataclass
class QualityInput:
    target: torch.Tensor     # (N, 13) position, rotation 6D, size, bottom height in the nominal base frame
    grasp: torch.Tensor      # (N, 9) object-frame candidate translation and rotation 6D
    sigma: torch.Tensor      # (N, 1)
    nodes: torch.Tensor      # (N, 20, 19)
    mask: torch.Tensor       # (N, 20)
    distance: torch.Tensor   # (N, 2, 20)

    def map(self, function):
        return QualityInput(*(function(getattr(self, f.name)) for f in fields(self)))

    def __getitem__(self, index):
        return self.map(lambda value: value[index])

    def __len__(self):
        return len(self.sigma)

    @classmethod
    def cat(cls, items):
        return cls(*(torch.cat([getattr(item, f.name) for item in items]) for f in fields(cls)))


def nominal_inputs(task, station_ids, grasp_ids, sigma):
    """Quality input from the nominal station pose; matches the control-side field definitions."""
    pose = task.station_poses[station_ids]
    c, s = pose[:, 2].cos(), pose[:, 2].sin()
    zero, one = torch.zeros_like(c), torch.ones_like(c)
    base_r = torch.stack((c, -s, zero, s, c, zero, zero, zero, one), -1).reshape(-1, 3, 3)
    base_p = torch.cat((pose[:, :2], torch.full_like(c, task.mocap_z)[:, None]), -1)
    count = len(station_ids)
    target_p = task.target_snapshot_p.expand(count, -1)
    target_r = task.target_snapshot_r.expand(count, -1, -1)
    local_r = base_r.transpose(-1, -2) @ target_r
    bottom = target_p[:, 2] + (target_r[:, 2, :] @ task.target_points.T).amin(-1) - base_p[:, 2]
    target = torch.cat((
        torch.einsum("bji,bj->bi", base_r, target_p - base_p), local_r[:, :, :2].transpose(-1, -2).flatten(1),
        task.target_size.expand(count, -1), bottom[:, None],
    ), -1)
    rotation = task.grasp_rotations[grasp_ids]
    grasp = torch.cat((task.grasp_positions[grasp_ids], rotation[:, :, :2].transpose(-1, -2).flatten(1)), -1)
    goal = target_p + torch.einsum("bij,j->bi", target_r, task.goal_offset)
    nodes, mask, distance = task.node_tensors.compute(station_ids, base_p, base_r, goal)
    return QualityInput(target, grasp, sigma.reshape(-1, 1).float(), nodes, mask, distance)


def label_requests(task, *, repeats, seed, sigma_bins=5):
    """Every legal station x pool grasp x stratified sigma bin, ``repeats`` times; sigma uniform in its bin."""
    rng = np.random.default_rng(seed)
    grasps = task.grasp_pool_table[0].tolist()
    requests = []
    for station in task.pool.tolist():
        for grasp in grasps:
            for bin_index in range(sigma_bins):
                for _ in range(repeats):
                    sigma = (bin_index + rng.random()) / sigma_bins
                    requests.append((station, grasp, float(sigma)))
    order = rng.permutation(len(requests))
    return [requests[i] for i in order]


@torch.no_grad()
def collect_labels(agent, env, requests, *, deterministic=True, log=None):
    """Run one strict episode per request with the frozen Actor; returns one record per request."""
    from .env.terms import strict_episode

    task, n = env.task, env.num_envs
    pending = list(range(len(requests)))[::-1]
    assigned = torch.full((n,), -1, dtype=torch.long)
    results = [None] * len(requests)

    def assign(ids):
        chosen = [pending.pop() if pending else -1 for _ in ids]
        filler = requests[0]
        rows = [requests[i] if i >= 0 else filler for i in chosen]
        assigned[ids] = torch.tensor(chosen)
        task.requested_station_ids = torch.tensor([r[0] for r in rows], device=env.device)
        task.requested_grasp_ids = torch.tensor([r[1] for r in rows], device=env.device)
        task.requested_sigma = torch.tensor([r[2] for r in rows], device=env.device)

    assign(list(range(n)))
    observations, _ = env.reset()
    obs = observations["policy"]["flat"].clone()
    while (assigned >= 0).any():
        action, _ = agent.actor.sample(obs, deterministic=deterministic)
        observations, _, terminated, truncated, _ = env.step(action)
        obs = observations["policy"]["flat"].clone()
        done = (terminated | truncated | task.unhealthy).cpu()
        ids = (done & (assigned >= 0)).nonzero(as_tuple=True)[0].tolist()
        for index in ids:
            request = int(assigned[index])
            station, grasp, sigma = requests[request]
            results[request] = {
                "request": request, "station_index": station, "station_id": task.stations[station]["station_id"],
                "grasp_id": grasp, "sigma_requested": sigma, "success": bool(task.success[index]),
                "infrastructure_failure": bool(task.unhealthy[index]), "steps": int(env.episode_length_buf[index]),
                **strict_episode(task, index),
            }
            if log is not None:
                log.write(json.dumps(results[request]) + "\n")
        reset_ids = done.nonzero(as_tuple=True)[0]
        if len(reset_ids):
            assign(reset_ids.tolist())
            reset, _ = env.reset(env_ids=reset_ids.to(env.device))
            fresh = reset["policy"]["flat"]
            obs[reset_ids] = fresh[reset_ids] if len(fresh) == n else fresh
    return results


def collect_worker(config, checkpoint, requests, num_envs, seed, path, deterministic=True):
    from .checkpoint import load_agent, seed_all
    from .devices import VISIBLE_DEVICE
    from .env.environment import StationEnv

    seed_all(seed)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=num_envs)
    try:
        agent, _ = load_agent(checkpoint, env.device)
        agent.actor.eval()
        with open(path, "w") as log:
            collect_labels(agent, env, requests, deterministic=deterministic, log=log)
    finally:
        env.close()


def collect(config, *, checkpoint, gpus, output, repeats, seed, num_envs, deterministic=True):
    """Shard the label requests over GPUs; each worker writes its own JSONL of strict outcomes."""
    from .devices import VISIBLE_DEVICE, quality_collect_entry
    from .env.environment import StationEnv

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    requests = label_requests(env.task, repeats=repeats, seed=seed)
    env.close()
    (output / "requests.json").write_text(json.dumps({"checkpoint": str(checkpoint), "seed": seed,
                                                      "policy": "mean" if deterministic else "stochastic",
                                                      "repeats": repeats, "requests": requests}))
    ctx = mp.get_context("spawn")
    shards = [requests[i::len(gpus)] for i in range(len(gpus))]
    processes = [ctx.Process(target=quality_collect_entry, args=(gpu, config, checkpoint, shard, num_envs, seed + i,
                                                           output / f"labels-{i}.jsonl", deterministic))
                 for i, (gpu, shard) in enumerate(zip(gpus, shards))]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode for process in processes):
        raise RuntimeError("A label collection worker failed")
    return {"episodes": len(requests), "output": str(output)}


def spatial_split(stations, *, block_m, seed, fractions=(0.7, 0.15, 0.15)):
    """Assign whole XY blocks (all yaws, grasps, sigma and repeats) to train / calibration / test."""
    xy = np.array([[s["x"], s["y"]] for s in stations])
    blocks = [tuple(b) for b in np.floor((xy - xy.min(0)) / block_m).astype(int)]
    unique = sorted(set(blocks))
    order = np.random.default_rng(seed).permutation(len(unique))
    edges = np.cumsum(np.array(fractions) * len(unique)).round().astype(int)
    names = {}
    for rank, block in enumerate(order):
        names[unique[block]] = "train" if rank < edges[0] else "calibration" if rank < edges[1] else "test"
    return [names[b] for b in blocks]


def fit_member(inputs, labels, *, seed, epochs, batch, lr, device):
    """Bootstrap-resampled episodes, BCE on the success logit."""
    generator = torch.Generator().manual_seed(seed)
    torch.manual_seed(seed)
    member = QualityMember().to(device)
    optimizer = torch.optim.Adam(member.parameters(), lr=lr)
    sample = torch.randint(len(labels), (len(labels),), generator=generator)
    data, target = inputs[sample].map(lambda x: x.to(device)), labels[sample].to(device)
    for _ in range(epochs):
        order = torch.randperm(len(target), generator=generator).to(device)
        for start in range(0, len(target), batch):
            index = order[start:start + batch]
            mini = data[index]
            loss = F.binary_cross_entropy_with_logits(
                member(mini.target, mini.grasp, mini.sigma, mini.nodes, mini.mask, mini.distance), target[index])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
    return member.eval()


@torch.no_grad()
def predict(members, inputs, device, batch=8192):
    """Member probabilities (M, N)."""
    rows = []
    for member in members:
        parts = []
        for start in range(0, len(inputs), batch):
            mini = inputs[start:start + batch].map(lambda x: x.to(device))
            parts.append(torch.sigmoid(member(mini.target, mini.grasp, mini.sigma, mini.nodes, mini.mask,
                                              mini.distance)).cpu())
        rows.append(torch.cat(parts))
    return torch.stack(rows)


def calibration_metrics(probability, labels, bins=10):
    p, y = probability.double(), labels.double()
    clipped = p.clamp(1e-6, 1 - 1e-6)
    table = []
    for low in np.linspace(0, 1, bins, endpoint=False):
        inside = (p >= low) & (p < low + 1 / bins) if low + 1 / bins < 1 else p >= low
        if inside.any():
            table.append({"bin": [float(low), float(low + 1 / bins)], "n": int(inside.sum()),
                          "k": int(y[inside].sum()), "mean_p": float(p[inside].mean())})
    return {"n": len(y), "brier": float(((p - y) ** 2).mean()),
            "nll": float(-(y * clipped.log() + (1 - y) * (1 - clipped).log()).mean()),
            "base_rate": float(y.mean()), "reliability": table}


def station_maps(task, members, device):
    """Per legal station: r_l, u_l over sigma points, then Max (Q_phys, U) and uniform-grasp summaries."""
    stations = task.pool
    grasps = task.grasp_pool_table[0]
    sigma = torch.tensor(SIGMA_POINTS, device=task.device)
    s_i, g_i, k_i = torch.meshgrid(stations, grasps, torch.arange(len(sigma), device=task.device), indexing="ij")
    inputs = nominal_inputs(task, s_i.flatten(), g_i.flatten(), sigma[k_i.flatten()])
    probability = predict(members, inputs.map(lambda x: x.cpu()), device).reshape(
        len(members), len(stations), len(grasps), len(sigma))
    mu, spread = probability.mean(0), probability.std(0, correction=0)
    weights = torch.tensor(SIGMA_WEIGHTS)
    r, u = (mu * weights).sum(-1), (spread * weights).sum(-1)               # (S, L)
    best = r.argmax(-1)
    rows = []
    for row, station in enumerate(stations.tolist()):
        record = task.stations[station]
        rows.append({
            **{key: record[key] for key in ("station_id", "xy_id", "x", "y", "yaw", "yaw_offset_rad", "valid")},
            "q_phys": float(r[row, best[row]]), "u": float(u[row, best[row]]),
            "best_grasp_id": int(grasps[best[row]]),
            "q_phys_uniform_grasp": float(r[row].mean()), "u_uniform_grasp": float(u[row].mean()),
            "r_by_grasp": r[row].tolist(), "u_by_grasp": u[row].tolist(),
            "mu_by_grasp_sigma": mu[row].tolist(),
        })
    return rows, [int(g) for g in grasps.tolist()]


MAP_PANELS = (("q_phys", "Q_phys (Max grasp)", (0, 1)), ("u", "U (ensemble std)", (0, None)))


def plot_maps(rows, invalid, path, panels=MAP_PANELS):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    offsets = sorted({round(r["yaw_offset_rad"] or 0.0, 4) for r in rows})
    fig, axes = plt.subplots(len(offsets), len(panels), figsize=(5.5 * len(panels), 4.6 * len(offsets)),
                             squeeze=False, constrained_layout=True)
    for line, offset in enumerate(offsets):
        subset = [r for r in rows if abs((r["yaw_offset_rad"] or 0.0) - offset) < 1e-3]
        for axis, (key, title, limits) in zip(axes[line], panels):
            axis.scatter([r["x"] for r in invalid], [r["y"] for r in invalid], c="lightgray", marker="x", s=12)
            points = axis.scatter([r["x"] for r in subset], [r["y"] for r in subset], c=[r[key] for r in subset],
                                  vmin=limits[0], vmax=limits[1], marker="s", cmap="viridis")
            axis.set(title=f"{title}, yaw offset {math.degrees(offset):.0f} deg", aspect="equal",
                     xlabel="world x (m)", ylabel="world y (m)")
            fig.colorbar(points, ax=axis)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def load_labels(directory):
    rows = []
    for path in sorted(Path(directory).glob("labels-*.jsonl")):
        rows += [json.loads(line) for line in path.open()]
    return [row for row in rows if not row["infrastructure_failure"]]


def fit(labels_dir, *, output, members, epochs, block_m, seed, batch=1024, lr=1e-3):
    """Spatial-block split, bootstrap members on train, calibration/test metrics and the two maps."""
    from scipy.stats import spearmanr

    from .config import resolve_config
    from .devices import VISIBLE_DEVICE
    from .env.environment import StationEnv

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    meta = json.loads((Path(labels_dir) / "requests.json").read_text())
    saved = torch.load(meta["checkpoint"], map_location="cpu", weights_only=False)
    config = resolve_config(saved["metadata"]["config"])
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    try:
        task, device = env.task, env.device
        rows = load_labels(labels_dir)
        split = spatial_split(task.stations, block_m=block_m, seed=seed)
        station = torch.tensor([r["station_index"] for r in rows], device=task.device)
        inputs = nominal_inputs(task, station, torch.tensor([r["grasp_id"] for r in rows], device=task.device),
                                torch.tensor([r["sigma"] for r in rows], device=task.device)).map(lambda x: x.cpu())
        labels = torch.tensor([float(r["success"]) for r in rows])
        part = np.array([split[r["station_index"]] for r in rows])
        train = torch.as_tensor(np.flatnonzero(part == "train"))
        ensemble = [fit_member(inputs[train], labels[train], seed=seed + m, epochs=epochs, batch=batch, lr=lr,
                               device=device) for m in range(members)]
        probability = predict(ensemble, inputs, device)
        mean, spread = probability.mean(0), probability.std(0, correction=0)
        metrics = {}
        for name in ("train", "calibration", "test"):
            index = torch.as_tensor(np.flatnonzero(part == name))
            if len(index):
                metrics[name] = {**calibration_metrics(mean[index], labels[index]),
                                 "mean_member_std": float(spread[index].mean())}
                # Station ranking: predicted mean over its episodes vs empirical strict success.
                per = {}
                for i in index.tolist():
                    per.setdefault(rows[i]["station_index"], []).append((float(mean[i]), rows[i]["success"]))
                predicted = [np.mean([p for p, _ in v]) for v in per.values()]
                empirical = [np.mean([s for _, s in v]) for v in per.values()]
                metrics[name]["stations"] = len(per)
                metrics[name]["station_spearman"] = (float(spearmanr(predicted, empirical).statistic)
                                                     if len(per) > 2 and np.std(empirical) > 0 else None)
        maps, grasps = station_maps(task, ensemble, device)
        for row in maps:
            row["split"] = split[[s["station_id"] for s in task.stations].index(row["station_id"])]
        invalid = [s for s in task.stations if not s["valid"]]
        torch.save({"members": [m.state_dict() for m in ensemble], "checkpoint": meta["checkpoint"],
                    "sigma_points": SIGMA_POINTS, "sigma_weights": SIGMA_WEIGHTS, "grasp_ids": grasps}, output / "quality.pt")
        (output / "metrics.json").write_text(json.dumps({"episodes": len(rows), "block_m": block_m, "seed": seed,
                                                         "members": members, "epochs": epochs, **metrics}, indent=2))
        (output / "station-quality.json").write_text(json.dumps({
            "checkpoint": meta["checkpoint"], "selection": "max over grasps of r_l = sum_k w_k mu_lk",
            "sigma_points": SIGMA_POINTS, "sigma_weights": SIGMA_WEIGHTS, "grasp_ids": grasps, "stations": maps,
        }, indent=2))
        plot_maps(maps, invalid, output / "station-quality.png")
        return {"episodes": len(rows), **{k: {"brier": v["brier"], "nll": v["nll"], "base_rate": v["base_rate"],
                                               "station_spearman": v["station_spearman"]} for k, v in metrics.items()}}
    finally:
        env.close()
