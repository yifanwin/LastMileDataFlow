#!/usr/bin/env python3
"""Figure 4: E02 — S0009 → S0023 navigation-then-grasp transfer (MolmoBot VLA)."""
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
E02 = ROOT / ".worktrees/no-edit-lastmile-val/outputs/diagnostics/molmobot-transfer-S0009-S0023-20261011-E02"

TRIALS = list(csv.DictReader((DATA / "e02_trials.csv").open()))
RAW = {r["trial"]: r for r in csv.DictReader((E02 / "trial_results.csv").open())}
RAW_BY_I = {r["attempt"].split("-t")[1][0]: r for r in csv.DictReader((E02 / "trial_results.csv").open())}
TRIALS.sort(key=lambda t: int(t["attempt"].split("-t")[1][0]))
EXP = json.loads((E02 / "experiment.json").read_text())
ANCHOR = (EXP["goal"]["xy"][0], EXP["goal"]["xy"][1])
START = (EXP["start"]["xy"][0], EXP["start"]["xy"][1])


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, center - half), min(1.0, center + half)


def attempt_dir(name):
    return E02 / "attempts/pick/S0009" / name


def main():
    viz.setup()
    fig = plt.figure(figsize=(13.4, 8.4))
    gs = fig.add_gridspec(2, 3, height_ratios=[1, 1.1], hspace=0.42, wspace=0.30,
                          left=0.075, right=0.985, top=0.87, bottom=0.10)

    # ---------------------------------------------------- A: three conditions
    axA = fig.add_subplot(gs[0, 0])
    conds = [("S0009 原地操作\n（基线 5 次）", 0, 5, viz.DEEMPH),
             ("S0009 导航→S0023\n后操作（E02）", 3, 5, viz.BACKEND["vla"]),
             ("S0023 原地操作\n（基线 5 次）", 4, 5, viz.DEEMPH)]
    for i, (name, k, n, color) in enumerate(conds):
        lo, hi = wilson(k, n)
        axA.bar(i, 100 * k / n, width=0.56, color=color, zorder=3,
                edgecolor=viz.SURFACE, linewidth=1.8)
        axA.plot([i, i], [100 * lo, 100 * hi], color=viz.INK, lw=1.4, zorder=4)
        for e in (lo, hi):
            axA.plot([i - 0.09, i + 0.09], [100 * e, 100 * e], color=viz.INK, lw=1.4, zorder=4)
        axA.text(i, 100 * k / n + 4.5, f"{k}/{n}", ha="center", fontsize=9,
                 color=viz.INK, fontweight="bold")
    axA.set_xticks(range(3)); axA.set_xticklabels([c[0] for c in conds], fontsize=7.6)
    axA.set_ylabel("抓取成功率 (%)")
    axA.set_ylim(0, 116); axA.set_yticks(range(0, 101, 20))
    axA.set_title("A 到达 S0023 后成功率回到该站水平", loc="left")
    viz.clean(axA)
    axA.text(0.02, 0.98, "误差棒 = Wilson 95% 区间（n=5，仅示意不确定性）",
             transform=axA.transAxes, fontsize=7, color=viz.MUTED, va="top")

    # --------------------------------------------------- B: lift trajectories
    axB = fig.add_subplot(gs[0, 1:])
    for t in TRIALS:
        d = attempt_dir(t["attempt"].split("/")[-1])
        traj = [json.loads(l) for l in (d / "trajectory.jsonl").read_text().splitlines()]
        z = np.array([r["state_after"]["target"]["pose_world_xyz_wxyz"][2]
                      for r in traj if r["observations"].get("phase") != "navigation"])
        z = (z - z[0]) * 100
        ok = t["op_status"] == "success"
        axB.plot(np.arange(len(z)) * 0.1, z, lw=2.0,
                 color=viz.STATUS["good"] if ok else viz.STATUS["critical"],
                 alpha=.9 if ok else .75, zorder=3)
        axB.annotate(f"T{t['attempt'].split('-t')[1][0]}", (len(z) * 0.1, z[-1]),
                     xytext=(6, 0), textcoords="offset points", fontsize=7.5,
                     color=viz.STATUS["good"] if ok else viz.STATUS["critical"])
    axB.axhline(0, color=viz.AXIS, lw=1)
    axB.axhline(2.0, color=viz.INK_2, lw=1, ls=(0, (4, 3)))
    axB.text(30.5, 2.4, "成功判据：物理抬升 + 手指接触 + 持握（约 2 cm 量级）",
             fontsize=7, color=viz.INK_2, ha="right")
    axB.set_xlabel("策略步 × 0.1 s（到达 S0023 之后）")
    axB.set_ylabel("目标杯抬升 (cm)")
    axB.set_title("B 三次成功在 11–14 s 内抬升到 11–14 cm；两次失败停在 0–9 cm", loc="left")
    axB.set_ylim(-1.2, 16.5)
    viz.clean(axB)
    h = [plt.Line2D([], [], color=viz.STATUS["good"], lw=2.4),
         plt.Line2D([], [], color=viz.STATUS["critical"], lw=2.4)]
    axB.legend(h, ["抓取成功（3 次）", "策略耗尽 400 步预算（2 次）"], loc="upper left")

    # ------------------------------------------------- C: base path (top view)
    axC = fig.add_subplot(gs[1, 0])
    d0 = attempt_dir(TRIALS[0]["attempt"].split("/")[-1])
    traj = [json.loads(l) for l in (d0 / "trajectory.jsonl").read_text().splitlines()]
    base = np.array([r["state_after"]["robot"]["base"][:2] for r in traj])
    nav_n = sum(1 for r in traj if r["observations"].get("phase") == "navigation")
    axC.plot(base[:nav_n, 0], base[:nav_n, 1], color=viz.BACKEND["vla"], lw=2.4,
             label="导航段（50 个控制周期，5.0 s）", zorder=3)
    axC.plot(base[nav_n:, 0], base[nav_n:, 1], color=viz.INK_2, lw=1.6,
             label="操作段底盘修正", zorder=3)
    for pt, nm, c in ((START, "S0009 起点", viz.DEEMPH), (ANCHOR, "S0023 终点", viz.STATUS["critical"])):
        axC.scatter(*pt, s=90, c=c, edgecolors=viz.SURFACE, linewidths=1.2, zorder=5)
        axC.annotate(nm, pt, xytext=(-52, -4) if "起点" in nm else (7, 5),
                     textcoords="offset points", fontsize=8, color=viz.INK)
    axC.scatter(*[7.3135, 2.0988], marker="*", s=150, c=viz.INK, zorder=6)
    axC.annotate("目标杯", (7.3135, 2.0988), xytext=(-46, -14), textcoords="offset points",
                 fontsize=8, color=viz.INK)
    axC.set_xlim(6.72, 7.44); axC.set_ylim(1.80, 2.42)
    axC.set_xlabel("世界 X (m)"); axC.set_ylabel("世界 Y (m)")
    axC.set_title("C 直线连接器路径（0.444 m，101 点静态碰撞检查）", loc="left")
    axC.set_aspect("equal")
    viz.clean(axC, grid_axis="both")
    axC.legend(loc="upper right", fontsize=7.0)

    # ---------------------------------------------------- D: per-trial table
    axD = fig.add_subplot(gs[1, 1:])
    axD.axis("off")
    cols = ["次数", "种子", "导航", "抓取", "策略步数", "最大抬升\n(cm)", "最长持握\n(s)",
            "禁接触\n样本", "墙钟\n(s)"]
    rows = []
    for t in TRIALS:
        i = t["attempt"].split("-t")[1][0]
        raw = RAW_BY_I[t["attempt"].split("-t")[1][0]]
        rows.append([f"T{i}", raw["seed"], "到达", "成功" if t["op_status"] == "success"
                     else "失败", t["policy_steps"],
                     f"{float(t['max_lift_m'])*100:.2f}", f"{float(raw['max_held_s']):.3f}",
                     raw["all_forbidden_contact_samples"], f"{float(raw['wall_time_s']):.1f}"])
    tb = axD.table(cellText=rows, colLabels=cols, loc="upper center", cellLoc="center")
    tb.auto_set_font_size(False); tb.set_fontsize(8); tb.scale(1, 1.62)
    for j in range(len(cols)):
        tb[0, j].set_facecolor("#eceae4"); tb[0, j].set_text_props(fontweight="bold")
    for r in (1, 2, 3):
        for j in range(len(cols)):
            tb[r, j].set_facecolor("#eaf5ec")
    for j in range(len(cols)):
        tb[4, j].set_facecolor("#fdeceb"); tb[5, j].set_facecolor("#fdeceb")
    axD.set_title("D 逐次结果：导航 5/5 到达（位置误差 0.15 mm），失败全部来自策略预算耗尽",
                  loc="left", y=1.04)

    fig.suptitle("图 4  E02：MolmoBot VLA 从 S0009 真实导航 0.444 m 到 S0023 后抓取（n=5）",
                 x=0.055, ha="left", fontsize=13, fontweight="bold", y=0.985)
    fig.text(0.055, 0.935,
             "同一仿真实例内先导航后操作，不重置、不传送、不恢复快照；策略 100 ms / 控制 20 ms / 物理 4 ms，单次上限 400 步、300 s。"
             "成功判据为物理抬升 + 手指接触 + 持握，不以程序执行结束代替成功。",
             fontsize=7.6, color=viz.INK_2, va="top", ha="left")
    fig.text(0.055, 0.028,
             "数据：outputs/diagnostics/molmobot-transfer-S0009-S0023-20261011-E02/（trial_results.csv、experiment.json、逐次 trajectory.jsonl / navigation.json / outcome.json）。"
             "基线原地成功率取自 .worktrees/no-edit-lastmile-val 的 cup30 baseline run（S0009 0/5、S0023 4/5）。\n"
             "注意：保持 S0009 头部等初态、到达后不重设为 S0023 原地初态，因此这不是仅改变底盘位置的严格因果对照；n=5 不足以断言导航后优于或劣于原地。",
             fontsize=7.2, color=viz.MUTED, va="top", ha="left")
    viz.save(fig, FIG / "fig4-e02-transfer.png")


if __name__ == "__main__":
    main()
