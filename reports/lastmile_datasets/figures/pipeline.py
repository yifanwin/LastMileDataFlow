"""解释图：仅表达拟议编排，不表示真实端到端验收。"""
from pathlib import Path
import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, help="Optional raster for visual review")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    font_file = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if not font_file.is_file():
        raise RuntimeError("Choose an installed CJK font before rendering this figure")
    font = FontProperties(fname=font_file)
    colors = {"existing": "#3498db", "new": "#e67e22", "neutral": "#7f8c8d"}
    ink = "#2c3e50"
    fig, ax = plt.subplots(figsize=(12, 9))
    ax.set(xlim=(0, 12), ylim=(0, 9))
    ax.axis("off")
    fig.subplots_adjust(left=.025, right=.975, top=.99, bottom=.015)

    def text(x, y, value, size=12, color=ink, weight="normal", ha="center"):
        ax.text(x, y, value, fontproperties=font, fontsize=size, color=color,
                weight=weight, ha=ha, va="center", linespacing=1.55)

    def box(x, y, title, detail, kind="existing", width=4.6, height=.94):
        color = colors[kind]
        ax.add_patch(FancyBboxPatch((x-width/2, y-height/2), width, height,
            boxstyle="round,pad=0.035,rounding_size=0.1", linewidth=1.5,
            edgecolor=color, facecolor=color+"14"))
        text(x, y+.18, title, 14, weight="bold")
        text(x, y-.19, detail, 10.5)

    def arrow(start, end, **kwargs):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>",
            mutation_scale=15, linewidth=1.6, color=ink, **kwargs))

    text(6, 8.65, "最简实现：现有执行与记录 + 薄编排", 19, weight="bold")
    center = 4.7
    items = [
        (7.75, "scene-datasets 冻结包", "障碍物、任务、配置、selected 采样点", "existing"),
        (6.45, "cuRobo 逐点实测", "复用 collector / 独立 trial / 真实计数", "existing"),
        (5.15, "统一选点与关联", "S0 低率 + S1 高率 / 保存完整证据", "new"),
        (3.85, "连续 S0 → S1 → 操作", "复用 A* / 到站实测接续 / 禁止重置", "existing"),
        (2.55, "Raw + Canonical 原始数据", "完整状态、RGB/depth、命令、ctrl、接触与时间", "existing"),
        (1.25, "已有 MolmoBot Adapter", "独立导出 state/images；动作另需标定映射", "existing"),
    ]
    for y, title, detail, kind in items:
        box(center, y, title, detail, kind)
    for (upper, *_), (lower, *_) in zip(items, items[1:]):
        arrow((center, upper-.5), (center, lower+.5))

    # Calibration attempts are also recorded; they are not stitched into rollouts.
    ax.plot([2.35, .75, .75, 2.3], [6.45, 6.45, 2.55, 2.55],
            linewidth=1.5, color=ink)
    arrow((1.65, 2.55), (2.35, 2.55))
    text(.8, 4.5, "标定也保存\n原始证据", 10.5, ha="left")
    box(9.5, 5.15, "LastMileBench", "共用率表 / 自由策略评测", "new", width=3.4)
    arrow((7.05, 5.15), (7.77, 5.15))
    text(9.5, 4.22, "不同控制配置不冒充同配置标定\nnative 匹配标定不是采集前置条件", 10.5)
    text(9.5, 2.55, "成功 / 失败 / 异常均保留\n成功且审计通过 → 训练视图", 11)
    text(9.5, 1.25, "position ≠ native motor\n原始层不为模型格式删字段", 11)
    text(6, .31, "蓝：现成模块有代码依据    橙：待补的编排连接    本图不是已完成验收的声明", 10.5)
    fig.savefig(root / "pipeline.svg", format="svg", facecolor="white")
    if args.preview:
        fig.savefig(args.preview, dpi=130, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
