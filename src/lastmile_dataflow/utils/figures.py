"""Figure helpers: offscreen matplotlib (Agg), grid extent, saving, PIL label fonts with CJK when available."""

from functools import cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import ImageFont  # noqa: E402


def grid_extent(grid):
    """imshow extent with cell centres on the grid points."""
    height, width = grid.free.shape
    r = grid.resolution
    return [
        grid.origin[0] - r / 2,
        grid.origin[0] + (width - 0.5) * r,
        grid.origin[1] - r / 2,
        grid.origin[1] + (height - 0.5) * r,
    ]


def save_figure(fig, target, **options):
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, **options)
    plt.close(fig)
    return target


@cache
def _cjk_path():
    return next((p for p in CJK_FONT_PATHS if Path(p).exists()), None)


def cjk_available():
    return _cjk_path() is not None


@cache
def font(size):
    path = _cjk_path() or FALLBACK_FONT
    return ImageFont.truetype(path, size) if Path(path).exists() else ImageFont.load_default()


def label(chinese, english):
    """Keep images readable when the host has no CJK font."""
    return chinese if cjk_available() else english


CJK_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
