#!/usr/bin/env python3
"""从真实 result.json 生成 case 构造的「Agent 调用次数 vs 接受数」散点图。

数据源：outputs/case_construction/*/result.json（真实运行记录，非手工填写）。
输出：reports/figures/case-construction-calls-vs-accepted.{png,csv}

设计遵循 data-viz 规范：
- 形式：强调式散点（emphasis）——接受样本用强调色，其余用去强调灰；
- 标记大小编码同一 (调用数, 接受数) 格内的运行条数，避免重叠点被掩盖；
- 不抖动、不对齐，坐标即真实值。
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "outputs" / "case_construction"
OUT = ROOT / "reports" / "figures"

# ---- 配色（dataviz 参考色板；两色已过 validate_palette.js --pairs all）----
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
ACCENT = "#2a78d6"   # 接受
DEEMPH = "#b3b1a9"   # 未接受（去强调灰）

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Noto Sans CJK SC", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
})


def load_rows() -> list[dict]:
    rows = []
    for path in sorted(SRC.glob("*/result.json")):
        d = json.loads(path.read_text())
        case_type = d.get("case_type")
        calls = d.get("agent_calls")
        if case_type is None and calls is None:
            continue  # 探针/对照类，不是 case 构造运行
        accepted = d.get("construction_accepted_count")
        if accepted is None:
            accepted = d.get("accepted_count")
        rows.append({
            "run_id": d.get("run_id") or path.parent.name,
            "case_type": case_type,
            "status": d.get("status"),
            "agent_calls": 0 if calls is None else int(calls),
            "accepted": 0 if accepted is None else int(accepted),
            "case_verified": int(d.get("case_verified_count") or 0),
        })
    return rows


def write_csv(rows: list[dict]) -> Path:
    path = OUT / "case-construction-calls-vs-accepted.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def plot(rows: list[dict]) -> Path:
    by_cell: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        by_cell.setdefault((r["agent_calls"], r["accepted"]), []).append(r)

    fig, ax = plt.subplots(figsize=(9.2, 5.0), dpi=200)

    max_n = max(len(v) for v in by_cell.values())
    for (calls, accepted), group in by_cell.items():
        n = len(group)
        size = 90 + 300 * (n / max_n) ** 0.6
        color = ACCENT if accepted > 0 else DEEMPH
        ax.scatter(calls, accepted, s=size, c=color, alpha=0.9, zorder=3,
                   edgecolors=SURFACE, linewidths=2.0)
        ax.annotate(str(n), (calls, accepted), ha="center", va="center",
                    fontsize=10, color="#ffffff" if accepted > 0 else INK_PRIMARY,
                    fontweight="bold", zorder=4)

    # 接受样本单独点注
    acc = [r for r in rows if r["accepted"] > 0]
    if acc:
        a = acc[0]
        ax.annotate(
            f"{a['run_id']}\ncase1 · 接受 1 个 · 调用 {a['agent_calls']} 次",
            xy=(a["agent_calls"], a["accepted"]),
            xytext=(a["agent_calls"] - 1.4, a["accepted"] - 0.34),
            fontsize=9.5, color=INK_SECONDARY, ha="left", va="top",
            arrowprops=dict(arrowstyle="-", color=INK_MUTED, lw=1.0),
            zorder=5,
        )

    ax.set_xlabel("Agent 调用次数（单次真实构造运行）", color=INK_SECONDARY, fontsize=11)
    ax.set_ylabel("接受样本数", color=INK_SECONDARY, fontsize=11)
    ax.set_title("case 构造：Agent 调用次数 vs 构造接受数",
                 color=INK_PRIMARY, fontsize=14, fontweight="bold", pad=14)
    ax.text(0.0, 1.015, f"n = {len(rows)} 次真实运行；点内数字为同一格内的运行条数",
            transform=ax.transAxes, fontsize=9.5, color=INK_MUTED)

    ax.set_xlim(-1.6, max(r["agent_calls"] for r in rows) + 1.6)
    ax.set_ylim(-0.28, 1.45)
    ax.set_xticks(sorted({r["agent_calls"] for r in rows}))
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["0（未接受）", "1（接受）"])

    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=10)

    handles = [
        Line2D([], [], marker="o", linestyle="none", markersize=10,
               markerfacecolor=ACCENT, markeredgecolor=SURFACE, markeredgewidth=1.5,
               label="接受至少 1 个构造样本"),
        Line2D([], [], marker="o", linestyle="none", markersize=10,
               markerfacecolor=DEEMPH, markeredgecolor=SURFACE, markeredgewidth=1.5,
               label="0 接受（预算耗尽 / 中断 / 基础设施错误 / 源准备失败）"),
        Line2D([], [], marker="o", linestyle="none", markersize=7,
               markerfacecolor=INK_MUTED, markeredgecolor=SURFACE, markeredgewidth=1.2,
               label="标记面积随同格运行条数增大"),
    ]
    leg = ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.17),
                    frameon=False, fontsize=9.5, ncol=1, handletextpad=0.8)
    for text in leg.get_texts():
        text.set_color(INK_SECONDARY)

    fig.text(0.012, 0.015,
             "数据源：outputs/case_construction/*/result.json（真实运行记录，未手工填写）",
             fontsize=8.5, color=INK_MUTED)

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path = OUT / "case-construction-calls-vs-accepted.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    print(f"读取 {len(rows)} 条运行记录")
    print("PNG :", plot(rows))
    print("CSV :", write_csv(rows))


if __name__ == "__main__":
    main()
