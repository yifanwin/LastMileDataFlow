#!/usr/bin/env python3
"""Figure 3: outcome decomposition — what actually kills each trial, backend by backend."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

import viz

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
FIG = BASE / "figures"

ROWS = {x["run_id"]: x for x in csv.DictReader((DATA / "runs.csv").open())}

GROUPS = [
    ("cuRobo 基线 37 点", ["no-edit-v080-reach-halfbase-val103-cup30-cuda5-20261011-1707"]),
    ("cuRobo Case1 (161→终选20)", ["no-edit-curobo-case1-v000-20261011-222405",
                                    "no-edit-final20-case1-v000-20261011-225943"]),
    ("cuRobo Case2 (161→终选20)", ["no-edit-curobo-case2-v001-20261011-222602",
                                    "no-edit-final20-case2-v001-20261011-225943"]),
    ("VLA 基线 37 点", ["molmobot-val103-cup30-20261011-194653"]),
    ("VLA Case1 (161→终选20)", ["no-edit-molmobot-case1-v000-20261011-224025",
                                 "no-edit-molmobot-final20-case1-v000-20261011-232238"]),
    ("VLA Case2 (161→终选20)", ["no-edit-molmobot-case2-v001-fix2-20261012-230054",
                                 "no-edit-molmobot-final20-case2-v001-fix3-20261012-232021"]),
]

# stack order = most actionable first; colour tracks the failure mode, not the rank
SEGMENTS = [
    ("成功", "success", viz.STATUS["good"]),
    ("规划无解 planning_no_solution", "planning_no_solution", viz.CAT[0]),
    ("执行期物理失败", "exec_failure", viz.CAT[1]),
    ("策略耗尽步数预算 PolicyFailure", "policy_budget", viz.CAT[2]),
    ("墙钟预算耗尽 timeout（环境）", "timeout", "#7b6fd0"),
    ("incomplete（未跑完）", "incomplete", viz.STATUS["warning"]),
]


def decompose(rows):
    """Map station-level failure_reasons onto the four outcome buckets."""
    n_success = sum(int(r["successes"]) for r in rows)
    n_pns = sum(int(r["trial_planning_no_solution"]) for r in rows)
    n_incomplete = sum(int(r["trial_incomplete"]) for r in rows)
    n_timeout = sum(int(r["trial_timeout"]) for r in rows)
    n_fail = sum(int(r["trial_failure"]) for r in rows)
    # PolicyFailure never ships a trajectory past the budget, so it is separated out
    n_policy = sum(int(r["fr_PolicyFailure"]) for r in rows)
    n_policy = min(n_policy, n_fail)
    n_exec = max(0, n_fail - n_policy)
    return {"success": n_success, "planning_no_solution": n_pns,
            "exec_failure": n_exec, "policy_budget": n_policy,
            "timeout": n_timeout, "incomplete": n_incomplete}


def main():
    viz.setup()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.0),
                                   gridspec_kw={"width_ratios": [2.0, 1.0], "wspace": 0.24})

    labels, tot, gaps = [], [], []
    for gname, rids in GROUPS:
        rows = [ROWS[r] for r in rids]
        d = decompose(rows)
        total = sum(d.values())
        labels.append(gname); tot.append(d); gaps.append(total)
        print(f"{gname:32s} total={total:4d} {d}")

    y = np.arange(len(labels))
    left = np.zeros(len(labels))
    for seg_label, key, color in SEGMENTS:
        vals = np.array([t[key] for t in tot], dtype=float)
        ax1.barh(y, vals, left=left, height=0.62, color=color,
                 edgecolor=viz.SURFACE, linewidth=1.6, label=seg_label, zorder=3)
        for yi, (v, l) in enumerate(zip(vals, left)):
            if v / gaps[yi] > 0.055:                      # direct-label only where it fits
                ax1.text(l + v / 2, yi, f"{v/gaps[yi]*100:.0f}%", ha="center", va="center",
                         fontsize=7.6, color=viz.SURFACE, fontweight="bold")
        left += vals
    ax1.set_yticks(y); ax1.set_yticklabels(labels, fontsize=9)
    ax1.invert_yaxis()
    ax1.set_xlabel("已终结试验次数（各组合的 161 点 + 终选 20 点 run 合并计数）")
    ax1.set_title("A 失败模式构成：cuRobo 卡在规划；VLA 后期被墙钟 timeout 淹没", loc="left")
    viz.clean(ax1, grid_axis="x")
    ax1.legend(loc="lower right", ncol=1)
    for yi, t in zip(y, gaps):
        ax1.text(t + 6, yi, f"n={t}", va="center", fontsize=8, color=viz.INK_2)

    # ---- panel B: how much of the planning cost is the head-FOV constraint
    fov_runs = [ROWS[r] for r in ROWS if ROWS[r]["backend"] == "curobo"]
    names = ["基线 37 点", "Case1 161 点", "Case2 161 点", "Case1 终选20", "Case2 终选20"]
    x = np.arange(len(fov_runs))
    fails = [int(r["fr_PlanningFailure"]) for r in fov_runs]
    pns = [int(r["trial_planning_no_solution"]) for r in fov_runs]
    ax2.bar(x - 0.19, pns, width=0.36, color=viz.CAT[0], zorder=3,
            edgecolor=viz.SURFACE, linewidth=1.6, label="planning_no_solution")
    ax2.bar(x + 0.19, fails, width=0.36, color=viz.CAT[1], zorder=3,
            edgecolor=viz.SURFACE, linewidth=1.6, label="PlanningFailure 归因")
    for xi, (p, f) in enumerate(zip(pns, fails)):
        ax2.text(xi - 0.19, p + 1.4, str(p), ha="center", fontsize=8, color=viz.INK)
        ax2.text(xi + 0.19, f + 1.4, str(f), ha="center", fontsize=8, color=viz.INK)
    ax2.set_xticks(x); ax2.set_xticklabels(names, fontsize=7.5, rotation=20, ha="right")
    ax2.set_ylabel("试验次数")
    ax2.set_title("B cuRobo 的失败几乎全在规划阶段", loc="left")
    ax2.set_ylim(0, max(fails) * 1.22)
    viz.clean(ax2)
    ax2.legend(loc="upper right")

    viz.note(fig, "数据：trials.json 的 status 与 station_statistics.json 的 failure_reasons。"
                  "planning_no_solution 是“冻结预算内无解”（planning_diagnostic.finite_budget_not_impossibility=true），"
                  "不等于物理不可达；PolicyFailure 是策略跑满 400 步仍未达成物理抬升判据。")
    viz.save(fig, FIG / "fig3-failure-decomposition.png")


if __name__ == "__main__":
    main()
