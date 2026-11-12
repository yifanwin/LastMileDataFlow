"""Offscreen matplotlib shared by the map and benchmark figures."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


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
