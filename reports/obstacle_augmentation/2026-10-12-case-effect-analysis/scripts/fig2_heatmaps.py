#!/usr/bin/env python3
"""Figure 2: spatial success-rate heatmaps, cuRobo vs VLA across the case matrix."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import viz

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
FIG = BASE / "figures"
ROOT = Path("/data0/wenyifan/MoMaTrajGen/LastMileDataFlow")
NOEDIT = ROOT / ".worktrees/no-edit-lastmile-val/outputs/no_edit"
AUG = ROOT / ".worktrees/scenes-datasets-design/outputs/no_edit"
AUGD = ROOT / ".worktrees/scenes-datasets-design/outputs/diagnostics"

ROWS = {x["run_id"]: x for x in csv.DictReader((DATA / "runs.csv").open())}
STATIONS = list(csv.DictReader((DATA / "stations.csv").open()))
ANCHOR = json.loads((DATA / "anchor.json").read_text())["anchor_world"]

CUROBO = ["no-edit-v080-reach-halfbase-val103-cup30-cuda5-20261011-1707",
          "no-edit-curobo-case1-v000-20261011-222405",
          "no-edit-curobo-case2-v001-20261011-222602",
          "no-edit-final20-case1-v000-20261011-225943",
          "no-edit-final20-case2-v001-20261011-225943"]
VLA = ["molmobot-val103-cup30-20261011-194653",
       "no-edit-molmobot-case1-v000-20261011-224025",
       "no-edit-molmobot-case2-v001-fix2-20261012-230054",
       "no-edit-molmobot-final20-case1-v000-20261011-232238",
       "no-edit-molmobot-final20-case2-v001-fix3-20261012-232021"]
COLS = ["基线 · 37 点", "Case1 · 161 点", "Case2 · 161 点", "Case1 · 终选 20 点", "Case2 · 终选 20 点"]
UNOBS = "#f2f1ee"
BLOCKED = "#b9b7b0"

# which variant supplies the obstacle overlay for a given run
OBSTACLE_OF = {"case1-v000": ("case1", "v000"), "case2-v001": ("case2", "v001"),
               "final20-case1": ("case1", "v000"), "final20-case2": ("case2", "v001")}


COLS_INDEX = {r: i for i, pair in enumerate(zip(CUROBO, VLA)) for r in pair}


def run_dir(run_id):
    return (NOEDIT if ("halfbase" in run_id or "194653" in run_id) else AUG) / run_id


def obstacle_edits(run_id):
    for key, (case, variant) in OBSTACLE_OF.items():
        if key in run_id:
            p = AUGD / f"dense-{case}-five-20261011-r5-views/variants/{variant}/edits.json"
            if p.exists():
                return json.loads(p.read_text())
    return []


def draw(ax, run_id, *, show_ylab):
    t = run_dir(run_id) / "scenes/val_103/tasks/cup2"
    npz = np.load(t / "maps/success_heatmap.npz")
    origin, res = npz["origin"], float(npz["resolution_m"])
    free = npz["free"].astype(bool)
    rate = npz["success_rate"]
    observed = free & (npz["weighted_trials"] > 0) & np.isfinite(rate)

    h, w = free.shape
    ext = [origin[0] - .5 * res, origin[0] + (w - .5) * res,
           origin[1] - .5 * res, origin[1] + (h - .5) * res]

    ax.set_facecolor(UNOBS)                                     # free but never observed
    ax.imshow(np.where(free, 0.0, 1.0), origin="lower", extent=ext, cmap="Greys",
              vmin=0, vmax=1, interpolation="nearest", alpha=0.42, zorder=1)
    masked = np.ma.masked_where(~observed, rate)
    im = ax.imshow(masked, origin="lower", extent=ext, cmap=viz.seq_cmap(),
                   vmin=0, vmax=1, interpolation="nearest", zorder=2)

    heat = json.loads((t / "maps/heatmap.json").read_text())
    for ob in heat.get("support_obstacles", []):
        lo, hi = np.asarray(ob["min"]), np.asarray(ob["max"])
        ax.add_patch(Rectangle(lo[:2], *(hi - lo)[:2], facecolor="#eed7b2",
                               edgecolor="#987d54", lw=1.0, zorder=3, alpha=.95))
    for e in obstacle_edits(run_id):
        lo, hi = np.asarray(e["world_bounds"]["min"]), np.asarray(e["world_bounds"]["max"])
        ax.add_patch(Rectangle(lo[:2], *(hi - lo)[:2], facecolor="none",
                               edgecolor=viz.STATUS["critical"], lw=1.5,
                               ls=(0, (3, 2)), zorder=4))

    for s in (x for x in STATIONS if x["run_id"] == run_id and x["geometry"] == "valid"):
        x, y = float(s["x"]), float(s["y"])
        if s["success_rate"]:
            ax.scatter(x, y, c=[float(s["success_rate"])], cmap=viz.seq_cmap(),
                       vmin=0, vmax=1, s=30, edgecolors=viz.INK, linewidths=.5, zorder=6)
        else:
            ax.scatter(x, y, facecolors="none", edgecolors="#45443f", s=30,
                       linewidths=.9, zorder=6)

    ax.scatter(*ANCHOR[:2], marker="*", s=170, c=viz.INK, zorder=7,
               edgecolors=viz.SURFACE, linewidths=.8)

    r = ROWS[run_id]
    term, succ = int(r["terminal_trials"]), int(r["successes"])
    done = r["run_status"] == "completed"
    head = f"{succ}/{term} = {100*succ/term:.0f}%" + ("" if done else "  未跑完")
    ax.set_title(f"{COLS[COLS_INDEX[run_id]]}\n{head}",
                 fontsize=9, pad=5,
                 color=viz.INK if done else viz.STATUS["critical"],
                 fontweight="bold" if done else "normal")
    ax.set_xlim(6.30, 8.45); ax.set_ylim(1.15, 3.00); ax.set_aspect("equal")
    ax.tick_params(labelsize=7)
    ax.set_xlabel("世界 X (m)", fontsize=7.5)
    ax.set_ylabel("世界 Y (m)" if show_ylab else "", fontsize=7.5)
    return im


def main():
    viz.setup()
    n = len(COLS)
    fig, axes = plt.subplots(2, n, figsize=(3.5 * n, 8.6))
    fig.subplots_adjust(left=.055, right=.915, top=.90, bottom=.125, wspace=.09, hspace=.40)

    for i, (cid, vid) in enumerate(zip(CUROBO, VLA)):
        draw(axes[0, i], cid, show_ylab=(i == 0))
        draw(axes[1, i], vid, show_ylab=(i == 0))
    axes[0, 0].annotate("cuRobo v0.8.0 规划器", xy=(-0.34, .5), xycoords="axes fraction",
                        rotation=90, va="center", ha="center", fontsize=11,
                        color=viz.BACKEND["curobo"], fontweight="bold")
    axes[1, 0].annotate("MolmoBot VLA 策略", xy=(-0.34, .5), xycoords="axes fraction",
                        rotation=90, va="center", ha="center", fontsize=11,
                        color=viz.BACKEND["vla"], fontweight="bold")

    cax = fig.add_axes([0.930, 0.20, 0.014, 0.50])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=plt.Normalize(0, 1), cmap=viz.seq_cmap()), cax=cax)
    cb.set_label("高斯加权实测成功率（0–1）", fontsize=8.5)
    cb.ax.tick_params(labelsize=7.5)

    # legend of the two non-series cells
    sw = fig.add_axes([0.930, 0.735, 0.055, 0.10]); sw.axis("off")
    sw.add_patch(Rectangle((0, .62), .35, .3, facecolor=UNOBS, edgecolor="#c9c8c2"))
    sw.text(.42, .77, "自由但未观测\n(unknown ≠ 0)", fontsize=7, va="center", color=viz.INK_2)
    sw.add_patch(Rectangle((0, .18), .35, .3, facecolor=BLOCKED, edgecolor="#c9c8c2", alpha=.55))
    sw.text(.42, .33, "栅格阻挡\n（桌子/墙膨胀）", fontsize=7, va="center", color=viz.INK_2)

    fig.suptitle("图 2  同一 Cup_30 / val_103 任务的空间成功率热力图：程序化障碍物与站位密度如何改变可达区域",
                 x=0.055, ha="left", fontsize=13, fontweight="bold", y=0.995)
    fig.text(0.055, 0.955,
             "网格单元 0.05 m；色场为按底盘支撑距离传播的 Gaussian 加权实测成功率（σ = 0.11 / 0.22 m，随采样间距变化）。\n"
             "实心圈 = 几何有效且已测的站位（颜色即该站位成功率）；空心圈 = 几何有效但未测；★ = 目标杯；米色矩形 = 目标所在餐桌；红虚线 = 本 case 新增障碍物包围盒。",
             fontsize=7.6, color=viz.INK_2, va="top", ha="left")
    fig.text(0.055, 0.055,
             "数据：各 run 的 tasks/cup2/maps/success_heatmap.npz（直接读原始数组按统一色标重绘，未使用 run 自带 PNG）；"
             "障碍物包围盒取 dense-case1/2-five-20261011-r5-views/variants/v000|v001/edits.json。\n"
             "除左下角 VLA Case2·161 点外，每个面板都已把该 run 的全部几何有效站位各测 5 次；"
             "VLA Case2·161 点是写本快照时仍在采集（49/58 站位），其“未观测”面积不代表失败。"
             "VLA 的实测成功率被大量墙钟 timeout 稀释（见 图 3），直接比色场深浅会低估其能力。",
             fontsize=7.2, color=viz.MUTED, va="top", ha="left")
    viz.save(fig, FIG / "fig2-success-heatmaps.png")


if __name__ == "__main__":
    main()
