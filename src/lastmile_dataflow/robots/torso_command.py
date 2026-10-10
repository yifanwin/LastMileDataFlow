"""V2 boundary tolerance: sanitize commands, never modify measured physics."""
import numpy as np
from .action import InvalidAction

COMMAND_TOLERANCE_RAD = .003
FEEDBACK_TOLERANCE_RAD = .003


def bounded_torso_command(value, limits):
    value = float(value)
    lo, hi = limits
    if not np.isfinite(value) or value < lo-COMMAND_TOLERANCE_RAD or value > hi+COMMAND_TOLERANCE_RAD:
        raise InvalidAction('torso height exceeds clipping tolerance')
    return float(np.clip(value, lo, hi))


def torso_feedback_margin(value, limits):
    """Positive amount outside the h range; NaN is unsafe, not tolerated."""
    value = float(value)
    if not np.isfinite(value):
        return float('inf')
    return max(0., limits[0]-value, value-limits[1])
