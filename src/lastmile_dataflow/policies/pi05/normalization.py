"""π0.5 state/action normalization shared by inference and the training view."""

import numpy as np


def normalize(values, stats, mode, *, inverse=False):
    """Normalize the leading dimensions covered by ``stats``; padded tails stay unchanged."""
    if mode == "mean_std":
        offset = np.asarray(stats["mean"], np.float32)
        scale = np.asarray(stats["std"], np.float32) + 1e-6
    else:
        low = np.asarray(stats["q01"], np.float32)
        high = np.asarray(stats["q99"], np.float32)
        scale = (high - low + 1e-6) / 2
        offset = low + scale
    size = min(values.shape[-1], len(offset))
    head = values[..., :size]
    result = values.copy()
    result[..., :size] = head * scale[:size] + offset[:size] if inverse else (head - offset[:size]) / scale[:size]
    return result
