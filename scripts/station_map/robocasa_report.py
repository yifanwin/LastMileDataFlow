"""M5 report: rank metrics of every method against every evaluator, paired far-left differences, heatmaps.

Inputs (jsonl): --scores rows {method, scene, station_id, score}; --evaluator NAME=PATH rows {scene, station_id, success_rate};
--station-table gives x, y, yaw offset. Output: metrics.json, metrics.md, heatmap PNGs.
"""
import argparse
import collections
import json
from dataclasses import asdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from lastmile_dataflow.station_map.robocasa_metrics import paired_bootstrap, rank_metrics, steiger
from lastmile_dataflow.utils.io import rows, write_json

OURS = "Ours"


def table(path, value):
    return {(r["scene"], r["station_id"]): float(r[value]) for r in rows(path) if r.get(value) is not None}


def evaluate(scores, evaluators, scenes):
    methods = sorted({m for m, _ in scores})
    out = {"rank": [], "significance": [], "paired_difference": []}
    for ev, S in evaluators.items():
        for scene in scenes + ["pooled"]:
            keys = sorted(k for k in S if (scene == "pooled" or k[0] == scene) and all((m, k) in scores for m in methods))
            if len(keys) < 3:
                continue
            s = np.array([S[k] for k in keys])
            q = {m: np.array([scores[(m, k)] for k in keys]) for m in methods}
            for m in methods:
                out["rank"].append({"evaluator": ev, "scene": scene, "method": m, **asdict(rank_metrics(q[m], s))})
            if OURS in q:
                for m in methods:
                    if m != OURS:
                        out["significance"].append({"evaluator": ev, "scene": scene, "vs": m,
                                                    "bootstrap": paired_bootstrap(q[OURS], q[m], s), "steiger": steiger(q[OURS], q[m], s)})
        if len(scenes) == 2:
            clean, clut = scenes
            ids = sorted(sid for sc, sid in S if sc == clean and (clut, sid) in S
                         and all((m, (sc2, sid)) in scores for m in methods for sc2 in scenes))
            if len(ids) >= 3:
                ds = np.array([S[(clean, i)] - S[(clut, i)] for i in ids])
                for m in methods:
                    dq = np.array([scores[(m, (clean, i))] - scores[(m, (clut, i))] for i in ids])
                    ok = np.ptp(dq) > 0 and np.ptp(ds) > 0
                    r = rank_metrics(dq, ds) if ok else None
                    out["paired_difference"].append({"evaluator": ev, "method": m, "n": len(ids), "mean_dS": float(ds.mean()),
                                                     "mean_dQ": float(dq.mean()), "spearman": r.spearman if r else None,
                                                     "kendall_tau_b": r.kendall_tau_b if r else None})
    return out


def markdown(result):
    lines = ["| 评测器 | 场景 | 方法 | n | ρ | τ_b | Top-1 | Top-5 | Regret | AUROC |", "|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda v: "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.3f}"
    for r in result["rank"]:
        lines.append(f"| {r['evaluator']} | {r['scene']} | {r['method']} | {r['n']} | {f(r['spearman'])} | {f(r['kendall_tau_b'])} | "
                     f"{f(r['top1'])} | {f(r['top5'])} | {f(r['regret'])} | {f(r['auroc'])} |")
    lines += ["", "| 评测器 | 场景 | Ours vs | Δρ | 95% CI | 显著 | Steiger z | p |", "|---|---|---|---|---|---|---|---|"]
    for r in result["significance"]:
        b, st = r["bootstrap"], r["steiger"]
        lines.append(f"| {r['evaluator']} | {r['scene']} | {r['vs']} | {b['diff']:.3f} | [{b['ci95'][0]:.3f}, {b['ci95'][1]:.3f}] | "
                     f"{'是' if b['significant'] else '否'} | {st['z']:.2f} | {st['p_two_sided']:.3g} |")
    lines += ["", "| 评测器 | 方法 | n | mean ΔS | mean ΔQ | ρ(ΔQ,ΔS) | τ_b |", "|---|---|---|---|---|---|---|"]
    for r in result["paired_difference"]:
        lines.append(f"| {r['evaluator']} | {r['method']} | {r['n']} | {r['mean_dS']:.3f} | {r['mean_dQ']:.3f} | {f(r['spearman'])} | {f(r['kendall_tau_b'])} |")
    return "\n".join(lines) + "\n"


def heatmaps(scores, evaluators, stations, scenes, output, yaw_deg=0.0):
    methods = sorted({m for m, _ in scores})
    pts = {r["station_id"]: (r["x"], r["y"]) for r in stations if r["valid"] and r["yaw_offset_deg"] == yaw_deg}
    for scene in scenes:
        panels = [(f"{ev} S", S) for ev, S in evaluators.items()] + [(m, {k: v for (mm, k), v in scores.items() if mm == m}) for m in methods]
        fig, axes = plt.subplots(1, len(panels), figsize=(3.2 * len(panels), 3.4), squeeze=False)
        for ax, (title, values) in zip(axes[0], panels):
            ids = [i for i in pts if (scene, i) in values]
            v = np.array([values[(scene, i)] for i in ids])
            if len(v) and np.ptp(v) > 0 and not title.endswith(" S"):
                v = (v - v.min()) / np.ptp(v)  # per-method rank scale; S stays absolute
            sc = ax.scatter([pts[i][0] for i in ids], [pts[i][1] for i in ids], c=v, cmap="RdYlGn", vmin=0, vmax=1, s=60, marker="s")
            ax.set_title(title, fontsize=9); ax.set_aspect("equal"); ax.tick_params(labelsize=6)
        fig.colorbar(sc, ax=axes[0].tolist(), shrink=0.8)
        fig.suptitle(f"{scene}, yaw offset {yaw_deg:+.0f}°", fontsize=10)
        fig.savefig(Path(output) / f"heatmap-{scene}-yaw{int(yaw_deg):+d}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scores", nargs="+", required=True)
    p.add_argument("--evaluator", nargs="+", required=True, help="NAME=stations.jsonl")
    p.add_argument("--station-table", required=True)
    p.add_argument("--scenes", nargs="+", required=True, help="clean scene first for paired differences")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    scores = {}
    for path in a.scores:
        for r in rows(path):
            scores[(r["method"], (r["scene"], r["station_id"]))] = float(r["score"])
    evaluators = {e.split("=", 1)[0]: table(e.split("=", 1)[1], "success_rate") for e in a.evaluator}
    out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
    result = evaluate(scores, evaluators, a.scenes)
    write_json(out / "metrics.json", result)
    (out / "metrics.md").write_text(markdown(result))
    stations = list(rows(a.station_table))
    for yaw in sorted({r["yaw_offset_deg"] for r in stations}):
        heatmaps(scores, evaluators, stations, a.scenes, out, yaw)
    print(markdown(result))


if __name__ == "__main__":
    main()
