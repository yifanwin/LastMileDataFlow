#!/usr/bin/env python3
"""Figure 1: case effect — success rate and how the added obstacles consume stations."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

import viz

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
FIG = BASE / "figures"
ROOT = Path("/data0/wenyifan/MoMaTrajGen/LastMileDataFlow")
NOEDIT = ROOT / ".worktrees/no-edit-lastmile-val/outputs/no_edit"
AUG = ROOT / ".worktrees/scenes-datasets-design/outputs/no_edit"

ROWS = {x["run_id"]: x for x in csv.DictReader((DATA / "runs.csv").open())}

MATRIX = [
    ("基线\n37 点",
     "no-edit-v080-reach-halfbase-val103-cup30-cuda5-20261011-1707",
     "molmobot-val103-cup30-20261011-194653"),
    ("Case1\n161 点",
     "no-edit-curobo-case1-v000-20261011-222405",
     "no-edit-molmobot-case1-v000-20261011-224025"),
    ("Case2\n161 点",
     "no-edit-curobo-case2-v001-20261011-222602",
     "no-edit-molmobot-case2-v001-fix2-20261012-230054"),
    ("Case1\n终选 20 点",
     "no-edit-final20-case1-v000-20261011-225943",
     "no-edit-molmobot-final20-case1-v000-20261011-232238"),
    ("Case2\n终选 20 点",
     "no-edit-final20-case2-v001-20261011-225943",
     "no-edit-molmobot-final20-case2-v001-fix3-20261012-232021"),
]

STACK_RUNS = [
    ("基线 37 点", "no-edit-v080-reach-halfbase-val103-cup30-cuda5-20261011-1707"),
    ("Case1 161 点", "no-edit-curobo-case1-v000-20261011-222405"),
    ("Case2 161 点", "no-edit-curobo-case2-v001-20261011-222602"),
]


def run_dir(run_id):
    return (NOEDIT if ("halfbase" in run_id or "194653" in run_id) else AUG) / run_id


def station_causes(run_id):
    """Split geometry-filtered stations by whether a newly added obstacle is the cause."""
    st = json.loads((run_dir(run_id) / "scenes/val_103/tasks/cup2/stations.json").read_text())
    valid = new = old = 0
    for s in st:
        if s["geometry"] == "valid":
            valid += 1
            continue
        bodies = {c["body_names"][0] for c in (s.get("collisions") or []) if c.get("body_names")}
        if any("procedural_obstacle" in b for b in bodies):
            new += 1
        else:
            old += 1
    return {"valid": valid, "new_obstacle": new, "pre_existing": old, "total": len(st)}


def run_is_complete(run_id):
    """Both the scene and the task result must be final for a complete denominator."""
    row = ROWS[run_id]
    return row["run_status"] == "completed" and row["task_status"] not in ("not_yet_written", "incomplete")


def unfinished(run_id):
    return not run_is_complete(run_id)


def main():
    viz.setup()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.8, 8.4), sharex=True,
                                   gridspec_kw={"height_ratios": [1.45, 1.0], "hspace": 0.17})
    xs = np.arange(len(MATRIX))
    w = 0.33

    # ------------------------------------------------------ A: success rates
    for backend, off in (("curobo", -w / 2), ("vla", +w / 2)):
        rates, ann = [], []
        for _, cr, vr in MATRIX:
            rid = cr if backend == "curobo" else vr
            row = ROWS[rid]
            term, succ = int(row["terminal_trials"]), int(row["successes"])
            rates.append(100.0 * succ / term if term else 0.0)
            # In-flight/stopped runs get a "≥" prefix: their denominator is only the
            # episodes finished so far, not the planned 5 per station.
            partial = "≥" if not run_is_complete(rid) else ""
            ann.append((f"{partial}{succ}/{term}", unfinished(rid), rid))
        ax1.bar(xs + off, rates, width=w, color=viz.BACKEND[backend],
                edgecolor=viz.SURFACE, linewidth=1.6, zorder=3,
                hatch=["////" if a else None for _, a, _ in ann],
                label="cuRobo v0.8.0 规划器" if backend == "curobo" else "MolmoBot VLA 策略")
        for x, rate, (txt, a, rid) in zip(xs + off, rates, ann):
            ax1.text(x, rate + 1.6, txt, ha="center", fontsize=8.5, color=viz.INK,
                     fontweight="bold")
            if a:
                # Part of the denominator is missing: label how, so a reader does not
                # read "≥21/251" as either finished or as a plain failure.
                row = ROWS[rid]
                # Keep the on-bar marker short (long text collides with the
                # neighbouring bar); the exact station count goes in the footnote.
                label = "已停止" if row.get("run_stopped_early") == "True" else "未跑完"
                ax1.text(x, rate + 6.4, label, ha="center", va="bottom", fontsize=7,
                         color=viz.STATUS["critical"])
    ax1.set_ylabel("实测成功率（站位试验池化，%）")
    ax1.set_title("A 同一 Cup_30 / val_103 任务：障碍物与站位密度对成功率的影响", loc="left", pad=30)
    ax1.set_ylim(0, 82)
    ax1.set_yticks(range(0, 71, 10))
    viz.clean(ax1)
    ax1.legend(loc="upper right", ncol=2)
    ax1.text(0.0, 1.012,
             "斜纹 = run 提前停止或仍在跑，分母不是完整的每点 5 次（VLA Case2 161 点已在 51/58 站位处按用户决定停止）；同组内左柱 cuRobo、右柱 VLA 是两个不同后端的算法，"
             "不要互相当作消融对照。",
             transform=ax1.transAxes, fontsize=7, color=viz.MUTED, va="bottom")

    # ------------------------------------------------ B: where the stations go
    cats = [("几何有效，实际各测 5 次", "valid", viz.CAT[0]),
            ("被新增障碍物判为初始碰撞 / 无地面支撑", "new_obstacle", viz.STATUS["critical"]),
            ("被原有家具或墙体过滤", "pre_existing", viz.DEEMPH)]
    bottom = np.zeros(len(STACK_RUNS))
    for label, key, color in cats:
        vals = np.array([station_causes(r)[key] for _, r in STACK_RUNS], dtype=float)
        ax2.bar(xs[:len(STACK_RUNS)], vals, bottom=bottom, width=0.56, color=color,
                edgecolor=viz.SURFACE, linewidth=1.6, label=label, zorder=3)
        for i, (v, b) in enumerate(zip(vals, bottom)):
            if v >= 12:
                ax2.text(i, b + v / 2, f"{int(v)}", ha="center", va="center", fontsize=8.5,
                         color=viz.SURFACE, fontweight="bold")
        bottom += vals
    ax2.set_xticks(xs)
    ax2.set_xticklabels([m[0] for m in MATRIX], fontsize=8.5)
    ax2.set_ylabel("格点站位数量")
    ax2.set_title("B 新增障碍物直接吃掉 65–76 个格点；基线 13 个有效点里 Case1 只剩 5 个", loc="left")
    ax2.set_ylim(0, 196)
    viz.clean(ax2)
    ax2.legend(loc="upper left", ncol=1)
    ax2.text(len(STACK_RUNS) - 0.25, 150,
             "终选 20 点\n= 从有效点按方位/\n距离覆盖抽出的 20 个\n（Case1 从 24 抽 20，\nCase2 从 58 抽 20）",
             fontsize=7.2, color=viz.MUTED, va="top", ha="left")
    ax2.annotate("", xy=(3.28, 60), xytext=(3.28, 130),
                 arrowprops=dict(arrowstyle="->", color=viz.MUTED, lw=1))

    viz.note(fig, "数据：各 run 的 scenes/val_103/tasks/cup2/{result.json, station_statistics.json, stations.json}；"
                  "基线在 .worktrees/no-edit-lastmile-val，Case 在 .worktrees/scenes-datasets-design。"
                  "“被新增障碍物判为碰撞”= 该站位的初始碰撞记录里出现开头的 procedural_obstacle_* body。")
    viz.save(fig, FIG / "fig1-case-effect-overview.png")
    for name, rid in STACK_RUNS:
        print(name, station_causes(rid))


if __name__ == "__main__":
    main()
