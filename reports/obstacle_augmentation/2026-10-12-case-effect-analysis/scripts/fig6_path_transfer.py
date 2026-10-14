#!/usr/bin/env python3
"""E02 配套：S0009→S0023 段上"边走边抓"停点消融（f = 0.5 / 2/3 / 3/4）。

与 fig4_e02_transfer.py 同一数据契约，增加一条"停止位置 → 抓取成功率"的曲线：
  f = 0   原采样 S0009 原地（0/5）
  f = 0.5 本次：段中点（0.2218 m）
  f = 2/3 本次：段上 2/3 处（0.2957 m）
  f = 3/4 本次：段上 3/4 处（0.3326 m）
  f = 1   E02：段终点 S0023（3/5）
另叠原采样垂直列上的 S0016（2/5）/ S0023（4/5）作为"同样距离的站位"参照。
只读 outputs/ 与既有 run，不改动任何原始产物。
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

import viz

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
FIG = BASE / "figures"
ROOT = Path("/data0/wenyifan/MoMaTrajGen/LastMileDataFlow")
DIAG = ROOT / ".worktrees/no-edit-lastmile-val/outputs/diagnostics"
BASELINE = ROOT / ".worktrees/no-edit-lastmile-val/outputs/no_edit/molmobot-val103-cup30-20261011-194653"
E02 = DIAG / "molmobot-transfer-S0009-S0023-20261011-E02"

# 同一起点、同一批种子的连续执行段（本次主实验）
PATH_RUNS = {
    0.5: "path-molmobot-S0009-S0023-f50-*",
    2 / 3: "path-molmobot-S0009-S0023-f67-*",
    0.75: "path-molmobot-S0009-S0023-f75-*",
}
# 原采样垂直列上的站位（不同起点，仅作距离参照）
COLUMN = {"S0009": 0.0, "S0016": 1.0, "S0023": 2.0}


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, center - half), min(1.0, center + half)


def find_run(pattern: str) -> Path | None:
    hits = sorted(DIAG.glob(pattern))
    return hits[-1] if hits else None


def load_run(root: Path, fraction: float, seed_note: str) -> list[dict]:
    trials = json.loads((root / "trials.json").read_text())
    exp = json.loads((root / "experiment.json").read_text())
    rows = []
    for t in trials:
        attempt = Path(t["attempt"])
        verdict = t.get("verdict") or {}
        hands = verdict.get("hands") or {}
        lift = max((h["max_lift_m"] for h in hands.values()), default=float("nan"))
        held = max((h["max_held_s"] for h in hands.values()), default=float("nan"))
        nav = t.get("navigation") or {}
        rows.append({
            "series": "path",
            "fraction": fraction,
            "seed_note": seed_note,
            "trial": t["trial"],
            "seed": t["seed"],
            "stop_distance_m": exp["segment"]["stop_distance_m"],
            "distance_to_target_m": exp["segment"]["stop_distance_to_target_m"],
            "navigation_status": nav.get("status", "unknown"),
            "operation_status": t["status"],
            "reason": t["reason"],
            "policy_steps": t.get("executed_steps"),
            "max_lift_m": lift,
            "max_held_s": held,
            "wall_time_s": t.get("elapsed_wall_s"),
            "attempt": str(attempt),
        })
    return rows


def load_e02() -> list[dict]:
    rows = list(csv.DictReader((E02 / "trial_results.csv").open()))
    out = []
    for r in rows:
        out.append({
            "series": "e02", "fraction": 1.0, "seed_note": "E02",
            "trial": int(r["trial"]), "seed": int(r["seed"]),
            "stop_distance_m": 0.44351841600052566,
            "distance_to_target_m": 0.44351841600052566,
            "navigation_status": "success",
            "operation_status": r["operation_status"], "reason": r["reason"],
            "policy_steps": int(r["policy_steps"]),
            "max_lift_m": float(r["max_lift_m"]), "max_held_s": float(r["max_held_s"]),
            "wall_time_s": float(r["wall_time_s"]),
            "attempt": str(E02 / r["attempt"]),
        })
    return out


def station_rows() -> list[dict]:
    stats = json.loads((BASELINE / "scenes/val_103/tasks/cup2/station_statistics.json").read_text())
    anchor = np.array(json.loads(
        (BASELINE / "scenes/val_103/tasks/cup2/task.json").read_text())["anchor_world"][:2])
    rows = []
    for s in stats:
        if s["station_id"] not in COLUMN:
            continue
        xy = np.array(s["xy"])
        rows.append({
            "series": "station", "station_id": s["station_id"],
            "fraction": COLUMN[s["station_id"]],
            "distance_from_start_m": float(np.linalg.norm(xy - np.array([6.869938036800492, 1.8770781345919443]))),
            "distance_to_target_m": float(np.linalg.norm(anchor - xy)),
            "successes": s["successes"], "trials": s["terminal_trials"],
        })
    return rows


def main():
    viz.setup()
    path_rows, missing = [], []
    for fraction, pattern in PATH_RUNS.items():
        root = find_run(pattern)
        if root is None:
            missing.append(pattern)
            continue
        path_rows += load_run(root, fraction, root.name.split("-")[-1])
    path_rows += load_e02()
    path_rows.sort(key=lambda r: (r["fraction"], r["trial"]))
    stations = station_rows()

    DATA.mkdir(parents=True, exist_ok=True)
    fields = ["series", "fraction", "trial", "seed", "stop_distance_m", "distance_to_target_m",
              "navigation_status", "operation_status", "reason", "policy_steps",
              "max_lift_m", "max_held_s", "wall_time_s", "attempt"]
    with (DATA / "path_transfer.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(path_rows)

    series = {}
    for r in path_rows:
        series.setdefault(r["fraction"], []).append(r)

    fig = plt.figure(figsize=(13.4, 9.4))
    gs = fig.add_gridspec(2, 3, height_ratios=[0.88, 1.22], hspace=0.40, wspace=0.30,
                          left=0.075, right=0.985, top=0.855, bottom=0.115)

    # ------------------------------------------------- A: distance -> success
    axA = fig.add_subplot(gs[0, 0])
    order = sorted(series)
    xs, ys, lo, hi, labels = [], [], [], [], []
    for fraction in order:
        rows = series[fraction]
        k = sum(1 for r in rows if r["operation_status"] == "success"
                and r["navigation_status"] == "success")
        n = len(rows)
        l, h = wilson(k, n)
        d = rows[0]["stop_distance_m"]
        xs.append(d); ys.append(100 * k / n); lo.append(100 * l); hi.append(100 * h)
        labels.append((d, k, n, fraction))
    axA.errorbar(xs, ys, yerr=[np.array(ys) - np.array(lo), np.array(hi) - np.array(ys)],
                 color=viz.BACKEND["vla"], lw=1.6, capsize=3, marker="o", ms=6, zorder=4,
                 label="同一 S0009 初态、同批种子（本次）")
    axA.plot(xs, ys, color=viz.BACKEND["vla"], lw=1.6, ls="-", zorder=3)
    for d, k, n, fraction in labels:
        axA.annotate(f"{k}/{n}", (d, 100 * k / n), xytext=(0, 11), textcoords="offset points",
                     ha="center", fontsize=8, color=viz.INK, fontweight="bold")
    sx = [s["distance_from_start_m"] for s in stations]
    sy = [100 * s["successes"] / s["trials"] for s in stations]
    axA.scatter(sx, sy, s=64, facecolors="none", edgecolors=viz.INK_2, linewidths=1.4,
                zorder=4, label="原采样站位（各自原地初态）")
    for s in stations:
        axA.annotate(f'{s["station_id"]} {s["successes"]}/{s["trials"]}',
                     (s["distance_from_start_m"], 100 * s["successes"] / s["trials"]),
                     xytext=(4, -12), textcoords="offset points", fontsize=7.5, color=viz.INK_2)
    axA.set_xlabel("距 S0009 起点的行进距离 (m)")
    axA.set_ylabel("抓取成功率 (%)")
    axA.set_ylim(-6, 108); axA.set_xlim(-0.02, 0.50)
    axA.set_title("A 停点距离 vs 成功率：非单调，峰值在 3/4 处而非终点", loc="left")
    viz.clean(axA)
    axA.legend(loc="lower right", fontsize=7.2)

    # ------------------------------------------------- B: lift trajectories
    axB = fig.add_subplot(gs[0, 1:])
    for rows in series.values():
        for r in rows:
            traj_path = Path(r["attempt"]) / "trajectory.jsonl"
            if r["series"] == "e02":
                traj_path = Path(r["attempt"]) / "trajectory.jsonl"
            if not traj_path.is_file():
                continue
            traj = [json.loads(l) for l in traj_path.read_text().splitlines()]
            z = np.array([x["state_after"]["target"]["pose_world_xyz_wxyz"][2] for x in traj
                          if (x.get("observations") or {}).get("phase") != "navigation"])
            if not len(z):
                continue
            z = (z - z[0]) * 100
            ok = r["operation_status"] == "success"
            color = viz.STATUS["good"] if ok else viz.STATUS["critical"]
            axB.plot(np.arange(len(z)) * 0.1, z, lw=1.9, color=color, alpha=.92 if ok else .7,
                     zorder=3)
    axB.axhline(0, color=viz.AXIS, lw=1)
    axB.axhline(2.0, color=viz.INK_2, lw=1, ls=(0, (4, 3)))
    axB.text(0.985, 0.80, "成功判据：物理抬升 + 手指接触 + 持握（约 2 cm 量级）",
             transform=axB.transAxes, fontsize=7, color=viz.INK_2, ha="right")
    axB.set_xlabel("策略步 × 0.1 s（到达停点之后）")
    axB.set_ylabel("目标杯抬升 (cm)")
    axB.set_title("B 各停点的抬升曲线：成功（绿）都抬到 11–19 cm，失败（红）停在 0–9 cm", loc="left")
    axB.set_ylim(-1.2, 16.5)
    viz.clean(axB)
    h = [plt.Line2D([], [], color=viz.STATUS["good"], lw=2.4),
         plt.Line2D([], [], color=viz.STATUS["critical"], lw=2.4)]
    axB.legend(h, ["抓取成功", "未形成持握（策略预算耗尽）"], loc="upper left")

    # ------------------------------------------------- C: top view of stops
    axC = fig.add_subplot(gs[1, 0])
    start_xy = np.array([6.869938036800492, 1.8770781345919443])
    goal_xy = np.array([6.869938036800492, 2.32059655059247])
    anchor = np.array([7.3134564528010175, 2.098837342592207])
    axC.plot([start_xy[0], goal_xy[0]], [start_xy[1], goal_xy[1]], color=viz.INK_2, lw=1.4,
             ls=(0, (5, 3)), zorder=2, label="S0009→S0023 段（0.4435 m，101 点静态检查）")
    for fraction, color, name in ((0.5, viz.SEQ_BLUE[4], "f=0.5"), (2 / 3, viz.SEQ_BLUE[7], "f=2/3"),
                                  (0.75, viz.SEQ_BLUE[10], "f=3/4")):
        pt = start_xy + fraction * (goal_xy - start_xy)
        axC.scatter(*pt, s=80, c=color, edgecolors=viz.SURFACE, linewidths=1.2, zorder=5)
        axC.annotate(name, pt, xytext=(6, -3), textcoords="offset points", fontsize=8, color=viz.INK)
    axC.scatter(*start_xy, s=90, c=viz.DEEMPH, edgecolors=viz.SURFACE, linewidths=1.2, zorder=5)
    axC.annotate("S0009（同一起点）", start_xy, xytext=(-35, -13), textcoords="offset points",
                 fontsize=8, color=viz.INK)
    axC.scatter(*goal_xy, s=90, c=viz.STATUS["critical"], edgecolors=viz.SURFACE, linewidths=1.2, zorder=5)
    axC.annotate("S0023 终点", goal_xy, xytext=(6, 4), textcoords="offset points", fontsize=8, color=viz.INK)
    axC.scatter(*anchor, marker="*", s=150, c=viz.INK, zorder=6)
    axC.annotate("目标杯", anchor, xytext=(-14, -16), textcoords="offset points", fontsize=8, color=viz.INK)
    axC.set_xlim(6.80, 7.42); axC.set_ylim(1.78, 2.44)
    axC.set_xlabel("世界 X (m)"); axC.set_ylabel("世界 Y (m)")
    axC.set_title("C 三个停点都落在原采样列上、间距 0.2218 m", loc="left")
    axC.set_aspect("equal")
    viz.clean(axC, grid_axis="both")
    axC.legend(loc="lower right", fontsize=6.8)

    # ------------------------------------------------- D: per-trial table
    axD = fig.add_subplot(gs[1, 1:])
    axD.axis("off")
    cols = ["停点", "行进\n(m)", "次数", "种子", "导航", "抓取", "策略\n步数", "最大抬升\n(cm)", "墙钟\n(s)"]
    rows = []
    for r in path_rows:
        stop = ("S0009" if r["series"] == "station" else
                "S0023(E02)" if r["series"] == "e02" else f'f={r["fraction"]:.3g}')
        rows.append([stop, f'{r["stop_distance_m"]:.4f}', f'T{r["trial"]}', str(r["seed"]),
                     r["navigation_status"], "成功" if r["operation_status"] == "success" else "失败",
                     str(r["policy_steps"]), f'{r["max_lift_m"] * 100:.2f}', f'{r["wall_time_s"]:.0f}'])
    tb = axD.table(cellText=rows, colLabels=cols, cellLoc="center",
                   bbox=[0.0, 0.02, 1.0, 0.94])
    tb.auto_set_font_size(False); tb.set_fontsize(6.4)
    for j in range(len(cols)):
        tb[0, j].set_facecolor("#eceae4"); tb[0, j].set_text_props(fontweight="bold")
    for i, r in enumerate(path_rows, start=1):
        if r["operation_status"] == "success":
            for j in range(len(cols)):
                tb[i, j].set_facecolor("#eaf5ec")
    axD.set_title(f'D 逐次结果：共 {len(path_rows)} 次真实试验，导航全部到达；'
                  f'成功 {sum(1 for r in path_rows if r["operation_status"] == "success")} 次',
                  loc="left", y=1.03)

    fig.suptitle("图 6  E02 配套：在 S0009→S0023 段的不同位置停下再抓（f=0.5 / 2/3 / 3/4，各 5 次）",
                 x=0.055, ha="left", fontsize=13, fontweight="bold", y=0.985)
    fig.text(0.055, 0.938,
             "与 E02 完全同一协议：同一冻结场景与任务、同一 S0009 初态、同一批 5 个种子、同一 400 步/300 s 预算；"
             "到达停点后不重置、不传送、不恢复快照。停点只在初始化阶段设置底盘位姿，头/臂/躯干保持 S0009 初态，"
             "因此这不是「仅改底盘」的严格因果对照。",
             fontsize=7.4, color=viz.INK_2, va="top", ha="left")
    fig.text(0.055, 0.028,
             "数据：outputs/diagnostics/path-molmobot-S0009-S0023-f{50,67,75}-*/ 与 molmobot-transfer-S0009-S0023-20261011-E02/；"
             "原采样站位取自 molmobot-val103-cup30-20261011-194653 的 station_statistics.json。\n"
             "停点是与原采样站位同列（X 相同）的插值点，未继承端点合法性，逐点做过完整机器人静态碰撞检查；"
             "误差棒为 Wilson 95% 区间（n=5，区间大面积重叠，不能据此断言两点有真实差异）。"
             + (f" 缺 run：{missing}" if missing else ""),
             fontsize=7.0, color=viz.MUTED, va="top", ha="left")
    viz.save(fig, FIG / "fig6-path-transfer.png")
    print(json.dumps({f"{k:.4g}": [sum(1 for r in v if r["operation_status"] == "success"), len(v)]
                      for k, v in series.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
