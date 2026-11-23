"""(c) 站位质量图的选抓汇总与决策层。

读取 fit 输出的逐抓法 r_l、u_l，按 Max / Top-3 / Softmax / 均匀 四种选抓分布 α 汇总出
Q_phys = Σ α_l r_l 与 U = Σ α_l u_l，并与同一选抓下的实测站位成功率（标签按 σ 分层等权）比较。
在校准集上选保守系数 β（S_rank = Q_phys − βU）与 U 未知区阈值，测试集只报告。
"""

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from lastmile_dataflow.station_map.rl.quality import load_labels, plot_maps

RULES = ("max", "top3", "softmax", "uniform")
BETAS = (0.0, 0.5, 1.0, 2.0, 4.0)
SPLITS = ("train", "calibration", "test")


def selection_weights(r, rule, tau):
    """Grasp distribution alpha (S, L) from predicted per-grasp scores r (S, L)."""
    if rule == "uniform":
        return np.full_like(r, 1.0 / r.shape[1])
    if rule == "softmax":
        z = np.exp((r - r.max(1, keepdims=True)) / tau)
        return z / z.sum(1, keepdims=True)
    top = 1 if rule == "max" else 3
    alpha = np.zeros_like(r)
    np.put_along_axis(alpha, np.argsort(-r, 1)[:, :top], 1.0 / top, 1)
    return alpha


def empirical_success(labels, station_ids, grasp_ids, bins=5):
    """Strict success per (station, grasp), equal-weight mean over the sigma strata."""
    s_index = {s: i for i, s in enumerate(station_ids)}
    g_index = {g: i for i, g in enumerate(grasp_ids)}
    hits = np.zeros((len(station_ids), len(grasp_ids), bins))
    count = np.zeros_like(hits)
    for row in labels:
        k = min(int(row["sigma_requested"] * bins), bins - 1)
        i, j = s_index[row["station_id"]], g_index[row["grasp_id"]]
        hits[i, j, k] += row["success"]
        count[i, j, k] += 1
    print(f"episodes per (station, grasp, stratum): min {count.min():.0f}, max {count.max():.0f}")
    return (hits / np.maximum(count, 1)).mean(-1)


def rank_corr(a, b):
    return float(spearmanr(a, b).statistic) if len(a) > 2 else float("nan")


def summarize(q, u, actual, feasible):
    err = np.abs(q - actual)
    return {
        "n": int(len(q)), "actual_mean": float(actual.mean()), "q_mean": float(q.mean()),
        "mae": float(err.mean()), "spearman": rank_corr(q, actual),
        "spearman_feasible": rank_corr(q[feasible], actual[feasible]), "n_feasible": int(feasible.sum()),
        "q_std": float(q.std()), "q_iqr": float(np.subtract(*np.percentile(q, [75, 25]))),
        "frac_q_ge_0.9": float((q >= 0.9).mean()), "u_mean": float(u.mean()),
        "spearman_u_abs_err": rank_corr(u, err),
    }


def top_success(score, actual, fraction):
    k = max(1, int(round(fraction * len(score))))
    return float(actual[np.argsort(-score)[:k]].mean())


def decision_layer(q, u, actual, split, *, fraction, u_quantile):
    """Pick beta on calibration by the realized success of the top `fraction` stations; U threshold from
    train+calibration U quantile; report both on test."""
    table = {b: {s: top_success(q[split == s] - b * u[split == s], actual[split == s], fraction)
                 for s in ("calibration", "test")} for b in BETAS}
    beta = max(BETAS, key=lambda b: (table[b]["calibration"], -b))
    threshold = float(np.quantile(u[split != "test"], u_quantile))
    test = split == "test"
    known = u[test] <= threshold
    err = np.abs(q[test] - actual[test])
    s_test = q[test] - beta * u[test]
    return {
        "top_fraction": fraction, "beta_table": {str(b): v for b, v in table.items()}, "beta": beta,
        "u_threshold": threshold, "u_quantile": u_quantile,
        "test_unknown_fraction": float((~known).mean()),
        "test_mae_known": float(err[known].mean()), "test_mae_unknown": float(err[~known].mean()) if (~known).any() else None,
        "test_top_success_all": top_success(s_test, actual[test], fraction),
        "test_top_success_known_only": top_success(np.where(known, s_test, -np.inf), actual[test], fraction),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fit", type=Path, required=True, help="fit 输出目录（含 station-quality.json）")
    parser.add_argument("--labels", type=Path, required=True, help="collect 输出目录")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tau", type=float, default=0.1)
    parser.add_argument("--top-fraction", type=float, default=0.2)
    parser.add_argument("--u-quantile", type=float, default=0.9)
    args = parser.parse_args()

    data = json.loads((args.fit / "station-quality.json").read_text())
    stations, grasp_ids = data["stations"], data["grasp_ids"]
    r = np.array([s["r_by_grasp"] for s in stations])
    u_l = np.array([s["u_by_grasp"] for s in stations])
    split = np.array([s["split"] for s in stations])
    empirical = empirical_success(load_labels(args.labels), [s["station_id"] for s in stations], grasp_ids)

    q_max = r.max(1)
    feasible = q_max >= 0.8      # stations the Max rule already calls feasible: does the rule still rank them?
    report, maps = {"tau": args.tau, "rules": {}}, {}
    for rule in RULES:
        alpha = selection_weights(r, rule, args.tau)
        q, u, actual = (alpha * r).sum(1), (alpha * u_l).sum(1), (alpha * empirical).sum(1)
        maps[rule] = (q, u)
        report["rules"][rule] = {
            "splits": {s: summarize(q[split == s], u[split == s], actual[split == s], feasible[split == s])
                       for s in SPLITS},
            "decision": decision_layer(q, u, actual, split, fraction=args.top_fraction, u_quantile=args.u_quantile),
        }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "selection.json").write_text(json.dumps(report, indent=2))
    rows = [{**{k: s[k] for k in ("station_id", "x", "y", "yaw_offset_rad")},
             **{f"q_{rule}": float(maps[rule][0][i]) for rule in RULES},
             **{f"u_{rule}": float(maps[rule][1][i]) for rule in RULES}} for i, s in enumerate(stations)]
    plot_maps(rows, [], args.output / "q-by-rule.png",
              panels=tuple((f"q_{rule}", f"Q_phys ({rule})", (0, 1)) for rule in RULES))
    plot_maps(rows, [], args.output / "u-by-rule.png",
              panels=tuple((f"u_{rule}", f"U ({rule})", (0, None)) for rule in RULES))
    for rule in RULES:
        print(rule, json.dumps(report["rules"][rule]["splits"]["test"]),
              json.dumps({k: v for k, v in report["rules"][rule]["decision"].items() if k != "beta_table"}))


if __name__ == "__main__":
    main()
