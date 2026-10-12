"""Shared plotting setup: validated palette, CJK font, matplotlib defaults."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# --- design-system palette (validated with dataviz/scripts/validate_palette.js)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CAT = ["#2a78d6", "#eb6834", "#1baf7a"]            # slots 1-3, all-pairs safe
STATUS = {"good": "#0ca30c", "warning": "#fab219",
          "serious": "#ec835a", "critical": "#d03b3b"}
DEEMPH = "#c9c8c2"

# sequential blue ramp (light -> dark)
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
            "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

# backend -> hue follows the entity, not rank
BACKEND = {"curobo": CAT[0], "vla": CAT[1]}

CJK = "Noto Sans CJK SC"


def setup():
    available = {f.name for f in font_manager.fontManager.ttflist}
    family = CJK if CJK in available else "DejaVu Sans"
    plt.rcParams.update({
        "font.family": family,
        "font.size": 9,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.labelsize": 9,
        "axes.labelcolor": INK_2,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8,
        "axes.facecolor": SURFACE,
        "figure.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "figure.dpi": 150,
    })
    return family


def clean(ax, *, grid_axis="y", left=True, bottom=True):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(left)
    ax.spines["bottom"].set_visible(bottom)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID, lw=0.7, zorder=0)
        ax.set_axisbelow(True)
    return ax


def seq_cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("seqblue", SEQ_BLUE)


def note(fig, text):
    fig.text(0.005, 0.005, text, fontsize=6.5, color=MUTED, va="bottom", ha="left")


def save(fig, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("wrote", path)
