#!/usr/bin/env python3
"""Figure 5: is Case1/Case2 a degradation of the same stations, or a swap of the station set?"""
from __future__ import annotations

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

RUNS = {
    "cuRobo 基线": ("curobo", NOEDIT / "no-edit-v080-reach-halfbase-val103-cup30-cuda5-20261011-1707"),
    "cuRobo Case1": ("curobo", AUG / "no-edit-curobo-case1-v000-20261011-222405"),
    "cuRobo Case2": ("curobo", AUG / "no-edit-curobo-case2-v001-20261011-222602"),
    "VLA 基线": ("vla", NOEDIT / "molmobot-val103-cup30-20261011-194653"),
    "VLA Case1": ("vla", AUG / "no-edit-molmobot-case1-v000-20261011-224025"),
}
ANCHOR = json.loads((DATA / "anchor.json").read_text())["anchor_world"]
BANDS = [(0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80)]


def key(s):
    return (round(s["xy"][0], 4), round(s["xy"][1], 4))


def load(tag):
    p = RUNS[tag][1] / "scenes/val_103/tasks/cup2/station_statistics.json"
    return {key(s): s for s in json.loads(p.read_text())}


def rate(st):
    if st is None or not st["terminal_trials"]:
        return None
    return st["successes"] / st["terminal_trials"]


def main():
    viz.setup()
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(13.6, 5.8),
                                   gridspec_kw={"width_ratios": [1.0, 1.05], "wspace": 0.24})

    base = load("cuRobo 基线")
    coords = sorted([k for k, s in base.items() if s["geometry"] == "valid"],
                    key=lambda k: float(np.hypot(k[0] - ANCHOR[0], k[1] - ANCHOR[1])))
    caseA, caseB = load("cuRobo Case1"), load("cuRobo Case2")

    # ---------------- A: same coordinate, three cases
    for i, k in enumerate(coords):
        rb = rate(base[k])
        r1 = rate(caseA.get(k))
        r2 = rate(caseB.get(k))
        axA.plot([0, rb * 100], [i, i], color=viz.GRID, lw=1.5, zorder=2)
        if r2 is not None:
            axA.plot([rb * 100, r2 * 100], [i, i], color=viz.GRID, lw=1.5, zorder=2)
        if r1 is not None:
            axA.plot([rb * 100, r1 * 100], [i, i], color=viz.GRID, lw=1.5, zorder=2)
        axA.scatter(rb * 100, i, marker="o", s=64, c=viz.CAT[0],
                    edgecolors=viz.SURFACE, linewidths=1.0, zorder=5)
        if r2 is not None:
            axA.scatter(r2 * 100, i, marker="D", s=50, c=viz.CAT[2],
                        edgecolors=viz.SURFACE, linewidths=1.0, zorder=5)
        if r1 is not None:
            axA.scatter(r1 * 100, i, marker="s", s=54, c=viz.STATUS["critical"],
                        edgecolors=viz.SURFACE, linewidths=1.0, zorder=5)
        else:
            axA.annotate("Case1 被挡死", (rb * 100 + 2, i),
                         xytext=(7, 0), textcoords="offset points", fontsize=6.8,
                         va="center", color=viz.STATUS["critical"])
    axA.set_yticks(range(len(coords)))
    axA.set_yticklabels([f"{base[k]['station_id']} · {np.hypot(coords[i][0]-ANCHOR[0], coords[i][1]-ANCHOR[1]):.2f} m"
                         for i, k in enumerate(coords)], fontsize=7.5)
    axA.invert_yaxis()
    axA.set_xlim(-4, 122); axA.set_xticks(range(0, 101, 20))
    axA.set_xlabel("该站位实测成功率 (%)")
    axA.set_title("A 基线的 13 个有效站位：Case2 与基线重合，Case1 有 8 个被挡死", loc="left")
    viz.clean(axA, grid_axis="x")
    h = [plt.Line2D([], [], marker="o", ls="none", ms=7.5, color=viz.CAT[0], mec=viz.SURFACE),
         plt.Line2D([], [], marker="s", ls="none", ms=7.5, color=viz.STATUS["critical"], mec=viz.SURFACE),
         plt.Line2D([], [], marker="D", ls="none", ms=7, color=viz.CAT[2], mec=viz.SURFACE)]
    axA.legend(h, ["基线", "Case1", "Case2"], loc="upper right", ncol=3,
               bbox_to_anchor=(1.0, 1.0))
    vb, vc2 = np.mean([rate(base[k]) for k in coords]), np.mean([rate(caseB[k]) for k in coords])
    vc1 = np.mean([rate(caseA[k]) for k in coords if rate(caseA.get(k)) is not None])
    axA.text(0.985, 0.62, f"13 坐标平均：基线 {vb*100:.1f}%\n"
                          f"同 13 坐标 Case2 {vc2*100:.1f}%\n"
                          f"Case1 仅剩 5 个坐标 {vc1*100:.1f}%",
             transform=axA.transAxes, ha="right", fontsize=7.6, color=viz.INK_2, va="top")

    # ---------------- B: rate profile vs distance, and the correlation
    tags = ["cuRobo 基线", "cuRobo Case1", "cuRobo Case2"]
    width = 0.26
    xs = np.arange(len(BANDS))
    for j, tag in enumerate(tags):
        mp = load(tag)
        off = (j - 1) * width
        vals, ns = [], []
        for lo, hi in BANDS:
            rs = [rate(s) for k, s in mp.items()
                  if s["geometry"] == "valid" and lo <= np.hypot(k[0] - ANCHOR[0], k[1] - ANCHOR[1]) < hi]
            rs = [r for r in rs if r is not None]
            vals.append(100 * float(np.mean(rs)) if rs else 0.0)
            ns.append(len(rs))
        color = [viz.DEEMPH, viz.CAT[1], viz.CAT[2]][j]
        axB.bar(xs + off, vals, width=width, color=color, zorder=3,
                edgecolor=viz.SURFACE, linewidth=1.6, label=tag)
        for x, v, n in zip(xs + off, vals, ns):
            axB.text(x, v + 2.2, f"{v:.0f}%", ha="center", fontsize=7.2, color=viz.INK)
            axB.text(x, 2.0, f"n={n}", ha="center", fontsize=6.4, color=viz.MUTED)
        d = np.array([np.hypot(k[0] - ANCHOR[0], k[1] - ANCHOR[1]) for k, s in mp.items()
                      if s["geometry"] == "valid" and rate(s) is not None])
        r = np.array([rate(s) for k, s in mp.items()
                      if s["geometry"] == "valid" and rate(s) is not None])
        print(f"{tag:14} corr(dist, rate) = {np.corrcoef(d, r)[0,1]:+.2f}")
    axB.set_xticks(xs)
    axB.set_xticklabels([f"{lo:.1f}–{hi:.1f}" for lo, hi in BANDS])
    axB.set_xlabel("站位到目标杯的距离 (m)")
    axB.set_ylabel("该距离区间内有效站位的平均成功率 (%)")
    axB.set_title("B 每个 run 内部成功率与距离基本无关：整体下滑来自站位被吃掉，而不是站位更远", loc="left")
    axB.set_ylim(0, 100)
    viz.clean(axB)
    axB.legend(loc="upper right", ncol=3, fontsize=7.4)
    axB.text(0.015, 0.945, "station 级 距离–成功率 相关系数：基线 +0.02，Case1 −0.14，Case2 +0.23（|r| 均 ≤ 0.23）",
             transform=axB.transAxes, fontsize=7.2, color=viz.INK_2, va="top")

    viz.note(fig, "数据：scenes/val_103/tasks/cup2/{station_statistics.json, task.json}（case2-v001 的 cuRobo 运行）。"
                  "A 的纵轴按到目标距离排序，横线连的是同一坐标在相邻 run 的结果；"
                  "只统计几何有效且已终结试验 ≥1 的站位。\n"
                  "基线 13 个有效点的距离范围 0.44–0.70 m，Case1/Case2 的有效点范围 0.47–0.78 m；"
                  "两档覆盖重叠，所以整体均值差主要来自“哪些站位还存在”，而不是“站位有多远”。")
    viz.save(fig, FIG / "fig5-station-set-and-distance.png")


if __name__ == "__main__":
    main()
