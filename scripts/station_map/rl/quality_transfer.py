"""(c) 站位质量图跨场景检验：在编辑场景上复用冻结 Actor 与已拟合的五头质量网络。

stations：编辑场景的站位合法性与实体节点，对照原场景；
collect：冻结 Actor 在编辑场景采严格标签（同阶段三协议），保存所用配置；
evaluate：不重新拟合，用原场景的 quality.pt 预测编辑场景，报告校准、站位 Spearman 和 Q_phys / U 图。
"""

import argparse
import json
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices


def edited_config(checkpoint, edit_scene):
    import torch

    config = torch.load(checkpoint, map_location="cpu", weights_only=False)["metadata"]["config"]
    config["scene"]["edit_scene"] = str(Path(edit_scene).resolve())
    return config


def station_summary(env):
    task = env.task
    rows = task.stations
    reasons = {}
    for row in rows:
        reasons[row["invalid_reason"] or "valid"] = reasons.get(row["invalid_reason"] or "valid", 0) + 1
    import numpy as np

    from lastmile_dataflow.station_map.rl.env.scene_graph import NODE_KINDS

    index = task.node_selection.index
    used = sorted({int(i) for i in index[index >= 0]})
    entities = {task.entities.names[i]: NODE_KINDS[task.entities.kinds[i]] for i in used}
    valid = [i for i, r in enumerate(rows) if r["valid"]]
    return {"stations": len(rows), "valid": len(valid), "reasons": reasons,
            "nodes_per_valid_station": [int(n) for n in np.percentile((index[valid] >= 0).sum(1), (0, 50, 100))],
            "selected_entities": entities}


def evaluate(config, *, labels_dir, quality_path, seed):
    """Frozen source-scene ensemble on the edited scene: calibration, station ranking, maps."""
    import numpy as np
    import torch
    from scipy.stats import spearmanr

    from lastmile_dataflow.station_map.rl import quality
    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv
    from lastmile_dataflow.station_map.rl.networks import QualityMember

    saved = torch.load(quality_path, map_location="cpu", weights_only=False)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    try:
        task, device = env.task, env.device
        members = []
        for state in saved["members"]:
            member = QualityMember().to(device)
            member.load_state_dict(state)
            members.append(member.eval())
        rows = quality.load_labels(labels_dir)
        inputs = quality.nominal_inputs(
            task, torch.tensor([r["station_index"] for r in rows], device=task.device),
            torch.tensor([r["grasp_id"] for r in rows], device=task.device),
            torch.tensor([r["sigma"] for r in rows], device=task.device)).map(lambda x: x.cpu())
        labels = torch.tensor([float(r["success"]) for r in rows])
        probability = quality.predict(members, inputs, device)
        mean, spread = probability.mean(0), probability.std(0, correction=0)
        metrics = {**quality.calibration_metrics(mean, labels), "mean_member_std": float(spread.mean())}
        per = {}
        for i, row in enumerate(rows):
            per.setdefault(row["station_index"], []).append((float(mean[i]), row["success"], float(spread[i])))
        predicted = np.array([np.mean([p for p, _, _ in v]) for v in per.values()])
        empirical = np.array([np.mean([s for _, s, _ in v]) for v in per.values()])
        stds = np.array([np.mean([u for _, _, u in v]) for v in per.values()])
        rng = np.random.default_rng(seed)
        boot = [spearmanr(predicted[i], empirical[i]).statistic
                for i in (rng.integers(len(per), size=len(per)) for _ in range(1000))]
        metrics["stations"] = len(per)
        metrics["station_spearman"] = float(spearmanr(predicted, empirical).statistic)
        metrics["station_spearman_ci95"] = [float(np.nanpercentile(boot, 2.5)), float(np.nanpercentile(boot, 97.5))]
        metrics["station_mae"] = float(np.abs(predicted - empirical).mean())
        metrics["station_bias"] = float((predicted - empirical).mean())
        metrics["error_vs_u_spearman"] = float(spearmanr(np.abs(predicted - empirical), stds).statistic)
        maps, grasps = quality.station_maps(task, members, device)
        empirical_by_station = {task.stations[s]["station_id"]: float(e) for s, e in zip(per, empirical)}
        for row in maps:
            row["empirical"] = empirical_by_station.get(row["station_id"])
        invalid = [s for s in task.stations if not s["valid"]]
        output = Path(labels_dir)
        (output / "station-quality.json").write_text(json.dumps({"quality": str(quality_path), "grasp_ids": grasps,
                                                                 "stations": maps}, indent=2))
        quality.plot_maps(maps, invalid, output / "station-quality.png", panels=(
            ("q_phys", "Q_phys (Max grasp)", (0, 1)), ("u", "U (ensemble std)", (0, None)),
            ("empirical", "Measured strict success", (0, 1))))
        return {"episodes": len(rows), **metrics}
    finally:
        env.close()


def episode_inputs(config, labels_dir, seed):
    """Nominal inputs and strict labels of one label directory, built in that directory's scene."""
    import torch

    from lastmile_dataflow.station_map.rl import quality
    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv

    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    try:
        task = env.task
        rows = quality.load_labels(labels_dir)
        inputs = quality.nominal_inputs(
            task, torch.tensor([r["station_index"] for r in rows], device=task.device),
            torch.tensor([r["grasp_id"] for r in rows], device=task.device),
            torch.tensor([r["sigma"] for r in rows], device=task.device)).map(lambda x: x.cpu())
        return rows, inputs, torch.tensor([float(r["success"]) for r in rows]), list(task.stations)
    finally:
        env.close()


def refit(source_config, target_config, *, source_labels, target_labels, quality_path, output, seed,
          members=5, epochs=40, block_m=0.3, batch=1024, lr=1e-3):
    """Source labels plus the target scene's training blocks; compare with zero-shot on target held-out blocks."""
    import numpy as np
    import torch
    from scipy.stats import spearmanr

    from lastmile_dataflow.station_map.rl import quality
    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv
    from lastmile_dataflow.station_map.rl.networks import QualityMember

    _, source_inputs, source_y, _ = episode_inputs(source_config, source_labels, seed)
    rows, target_inputs, target_y, stations = episode_inputs(target_config, target_labels, seed)
    split = quality.spatial_split(stations, block_m=block_m, seed=seed)
    part = np.array([split[r["station_index"]] for r in rows])
    train = torch.as_tensor(np.flatnonzero(part == "train"))
    inputs = quality.QualityInput.cat([source_inputs, target_inputs[train]])
    labels = torch.cat([source_y, target_y[train]])
    device = torch.device("cuda")
    ensemble = [quality.fit_member(inputs, labels, seed=seed + m, epochs=epochs, batch=batch, lr=lr, device=device)
                for m in range(members)]
    zero_shot = []
    for state in torch.load(quality_path, map_location="cpu", weights_only=False)["members"]:
        member = QualityMember().to(device)
        member.load_state_dict(state)
        zero_shot.append(member.eval())

    def report(models, index):
        probability = quality.predict(models, target_inputs[index], device)
        mean, spread = probability.mean(0), probability.std(0, correction=0)
        result = {**quality.calibration_metrics(mean, target_y[index]), "mean_member_std": float(spread.mean())}
        per = {}
        for i, j in enumerate(index.tolist()):
            per.setdefault(rows[j]["station_index"], []).append((float(mean[i]), rows[j]["success"]))
        predicted = np.array([np.mean([p for p, _ in v]) for v in per.values()])
        empirical = np.array([np.mean([y for _, y in v]) for v in per.values()])
        result.update(stations=len(per), station_spearman=float(spearmanr(predicted, empirical).statistic),
                      station_bias=float((predicted - empirical).mean()))
        result.pop("reliability")
        return result

    metrics = {"block_m": block_m, "seed": seed, "target_train_episodes": int(len(train)),
               "source_episodes": int(len(source_y))}
    for name in ("calibration", "test"):
        index = torch.as_tensor(np.flatnonzero(part == name))
        metrics[name] = {"refit": report(ensemble, index), "zero_shot": report(zero_shot, index)}
    env = StationEnv(target_config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    try:
        maps, grasps = quality.station_maps(env.task, ensemble, device)
        for row in maps:
            row["split"] = split[[s["station_id"] for s in stations].index(row["station_id"])]
        invalid = [s for s in env.task.stations if not s["valid"]]
    finally:
        env.close()
    torch.save({"members": [m.state_dict() for m in ensemble], "sigma_points": quality.SIGMA_POINTS,
                "sigma_weights": quality.SIGMA_WEIGHTS, "grasp_ids": grasps}, output / "quality.pt")
    (output / "station-quality.json").write_text(json.dumps({"quality": "refit", "grasp_ids": grasps,
                                                             "stations": maps}, indent=2))
    quality.plot_maps(maps, invalid, output / "station-quality.png")
    return metrics


GROUPS = ("top", "middle", "low", "uniform")


def select_validation(maps, *, beta, per_group, seed, score="max", u_quantile=None):
    """Pre-registered stations: top / middle third / bottom third by S_rank = Q - beta U, and uniform.

    score="uniform" ranks by the uniform-grasp summary; stations whose U exceeds the u_quantile are marked
    unknown and excluded from the ranked groups. The uniform group is drawn from all legal stations."""
    import numpy as np

    q_key, u_key = ("q_phys", "u") if score == "max" else ("q_phys_uniform_grasp", "u_uniform_grasp")
    threshold = float(np.quantile([r[u_key] for r in maps], u_quantile)) if u_quantile is not None else None
    eligible = [r for r in maps if threshold is None or r[u_key] <= threshold]
    rng = np.random.default_rng(seed)
    ranked = sorted(eligible, key=lambda r: r[q_key] - beta * r[u_key], reverse=True)
    third = len(ranked) // 3
    pools = {"top": ranked[:per_group], "middle": ranked[third:2 * third], "low": ranked[2 * third:],
             "uniform": maps}
    chosen, used = [], set()
    for group in GROUPS:
        pool = [r for r in pools[group] if r["station_id"] not in used]
        picks = pool[:per_group] if group == "top" else [pool[i] for i in rng.permutation(len(pool))[:per_group]]
        for row in picks:
            used.add(row["station_id"])
            chosen.append({"group": group, "station_id": row["station_id"], "s_rank": row[q_key] - beta * row[u_key],
                           "q_phys": row["q_phys"], "u": row["u"], "best_grasp_id": row["best_grasp_id"],
                           "p_max": row["q_phys"], "p_uniform": row["q_phys_uniform_grasp"],
                           "unknown": threshold is not None and row[u_key] > threshold})
    return chosen, {"score": score, "u_quantile": u_quantile, "u_threshold": threshold,
                    "unknown": len(maps) - len(eligible), "legal": len(maps)}


def validate(config, *, checkpoint, maps_path, output, gpus, beta, per_group, episodes, seed, num_envs,
             score="max", u_quantile=None):
    """Q-ranked vs uniform station selection, and Max vs uniform grasp choice on the uniform stations."""
    import multiprocessing as mp

    import numpy as np

    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE, quality_collect_entry
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv

    maps = json.loads(Path(maps_path).read_text())
    selection, rule = select_validation(maps["stations"], beta=beta, per_group=per_group, seed=seed, score=score,
                                        u_quantile=u_quantile)
    (output / "selection.json").write_text(json.dumps({"beta": beta, "maps": str(maps_path), "seed": seed, **rule,
                                                       "selection": selection}, indent=2))
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=1)
    index = {s["station_id"]: i for i, s in enumerate(env.task.stations)}
    env.close()
    rng = np.random.default_rng(seed + 1)
    grasps = maps["grasp_ids"]
    sigmas = rng.random(episodes)          # shared across stations and grasp rules: paired noise levels
    requests, tags = [], []
    for row in selection:
        for k in range(episodes):
            requests.append((index[row["station_id"]], row["best_grasp_id"], float(sigmas[k])))
            tags.append({"group": row["group"], "grasp_rule": "max"})
            if row["group"] == "uniform":
                requests.append((index[row["station_id"]], int(grasps[rng.integers(len(grasps))]), float(sigmas[k])))
                tags.append({"group": "uniform", "grasp_rule": "uniform"})
    shards = [list(range(i, len(requests), len(gpus))) for i in range(len(gpus))]
    (output / "requests.json").write_text(json.dumps({"checkpoint": str(checkpoint), "requests": requests,
                                                      "tags": tags, "shards": shards}))
    ctx = mp.get_context("spawn")
    processes = [ctx.Process(target=quality_collect_entry,
                             args=(gpu, config, checkpoint, [requests[j] for j in shard], num_envs, seed + i,
                                   output / f"labels-{i}.jsonl")) for i, (gpu, shard) in enumerate(zip(gpus, shards))]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode for process in processes):
        raise RuntimeError("A validation worker failed")
    return summarize_validation(output, selection, episodes, seed)


def summarize_validation(output, selection, episodes, seed):
    """Per group success with station-bootstrap intervals; differences vs the uniform group."""
    import numpy as np

    meta = json.loads((output / "requests.json").read_text())
    by = {}
    for shard, ids in enumerate(meta["shards"]):
        for line in (output / f"labels-{shard}.jsonl").open():
            row = json.loads(line)
            if row["infrastructure_failure"]:
                continue
            tag = meta["tags"][ids[row["request"]]]
            by.setdefault((tag["group"], tag["grasp_rule"]), {}).setdefault(row["station_id"], []).append(
                row["success"])
    rng = np.random.default_rng(seed)
    stats = {}
    for (group, rule), stations in by.items():
        rates = np.array([np.mean(v) for v in stations.values()])
        boot = [rates[rng.integers(len(rates), size=len(rates))].mean() for _ in range(2000)]
        predicted = [r["p_max" if rule == "max" else "p_uniform"] for r in selection
                     if r["group"] == group and r["station_id"] in stations]
        stats[f"{group}/{rule}"] = {"stations": len(rates), "episodes": int(sum(len(v) for v in stations.values())),
                                    "success": float(rates.mean()), "ci95": [float(np.percentile(boot, 2.5)),
                                                                             float(np.percentile(boot, 97.5))],
                                    "predicted": float(np.mean(predicted))}
    base = by.get(("uniform", "max"), {})
    for group in ("top", "middle", "low"):
        cur = by.get((group, "max"), {})
        if cur and base:
            a, b = np.array([np.mean(v) for v in cur.values()]), np.array([np.mean(v) for v in base.values()])
            boot = [a[rng.integers(len(a), size=len(a))].mean() - b[rng.integers(len(b), size=len(b))].mean()
                    for _ in range(2000)]
            stats[f"{group}-uniform"] = {"diff": float(a.mean() - b.mean()),
                                         "ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]}
    if ("uniform", "uniform") in by and base:
        pairs = [(np.mean(base[s]), np.mean(by[("uniform", "uniform")][s])) for s in base
                 if s in by[("uniform", "uniform")]]
        d = np.array([a - b for a, b in pairs])
        boot = [d[rng.integers(len(d), size=len(d))].mean() for _ in range(2000)]
        stats["max_grasp-uniform_grasp"] = {"diff": float(d.mean()), "ci95": [float(np.percentile(boot, 2.5)),
                                                                              float(np.percentile(boot, 97.5))]}
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stations", "collect", "evaluate", "validate", "refit"):
        command = sub.add_parser(name)
        command.add_argument("--checkpoint", type=Path, required=True)
        command.add_argument("--edit-scene", type=Path, help="场景构建 run 目录；不给则用原场景")
        command.add_argument("--gpu", type=int, nargs="+", required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--seed", type=int, default=20261124)
    sub.choices["collect"].add_argument("--repeats", type=int, default=2)
    sub.choices["collect"].add_argument("--num-envs", type=int, default=512)
    sub.choices["evaluate"].add_argument("--quality", type=Path, required=True, help="原场景 fit 输出的 quality.pt")
    sub.choices["refit"].add_argument("--quality", type=Path, required=True, help="原场景 quality.pt（零样本对照）")
    sub.choices["refit"].add_argument("--source-labels", type=Path, required=True)
    sub.choices["refit"].add_argument("--target-labels", type=Path, required=True)
    validation = sub.choices["validate"]
    validation.add_argument("--maps", type=Path, required=True, help="evaluate 输出的 station-quality.json")
    validation.add_argument("--beta", type=float, default=1.0)
    validation.add_argument("--per-group", type=int, default=8)
    validation.add_argument("--episodes", type=int, default=20)
    validation.add_argument("--num-envs", type=int, default=64)
    validation.add_argument("--score", choices=("max", "uniform"), default="max", help="排序用 Max 或均匀选抓汇总的 Q 与 U")
    validation.add_argument("--u-quantile", type=float, help="U 高于该分位的站位标为未知区，不参选")
    args = parser.parse_args()
    select_devices(args.gpu if args.command in ("collect", "validate") else args.gpu[:1])

    from lastmile_dataflow.station_map.rl import quality

    if args.edit_scene:
        config = edited_config(args.checkpoint, args.edit_scene)
    else:
        import torch
        config = torch.load(args.checkpoint, map_location="cpu", weights_only=False)["metadata"]["config"]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "config.json").write_text(json.dumps(config, indent=2))
    if args.command == "stations":
        from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
        from lastmile_dataflow.station_map.rl.env.environment import StationEnv

        env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=args.seed, num_envs=1)
        try:
            result = station_summary(env)
        finally:
            env.close()
    elif args.command == "collect":
        result = quality.collect(config, checkpoint=args.checkpoint, gpus=args.gpu, output=args.output,
                                 repeats=args.repeats, seed=args.seed, num_envs=args.num_envs)
    elif args.command == "refit":
        import torch
        source = torch.load(args.checkpoint, map_location="cpu", weights_only=False)["metadata"]["config"]
        result = refit(source, config, source_labels=args.source_labels, target_labels=args.target_labels,
                       quality_path=args.quality, output=args.output, seed=args.seed)
    elif args.command == "validate":
        result = validate(config, checkpoint=args.checkpoint, maps_path=args.maps, output=args.output, gpus=args.gpu,
                          beta=args.beta, per_group=args.per_group, episodes=args.episodes, seed=args.seed,
                          num_envs=args.num_envs, score=args.score, u_quantile=args.u_quantile)
    else:
        result = evaluate(config, labels_dir=args.output, quality_path=args.quality, seed=args.seed)
    (args.output / f"{args.command}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2)[:4000])


if __name__ == "__main__":
    main()
