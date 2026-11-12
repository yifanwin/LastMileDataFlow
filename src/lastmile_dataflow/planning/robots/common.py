"""Shared trajectory ordering, timing and cuRobo state helpers."""

import numpy as np


def ordered_points(points, source_names, destination_names):
    if set(source_names) != set(destination_names) or len(source_names) != len(destination_names):
        raise ValueError("planner released forbidden or missing joints")
    return np.asarray(points)[..., [source_names.index(n) for n in destination_names]]


def retime_points(points, native_dt, control_hz, time_dilation):
    """Uniformly sample native positions onto the existing real control clock."""
    values = np.asarray(points, dtype=float)
    if not np.isfinite(native_dt) or native_dt <= 0 or not 0 < time_dilation <= 1:
        raise ValueError("invalid native trajectory timing")
    if len(values) < 2:
        return values
    duration = (len(values) - 1) * native_dt / time_dilation
    original = np.arange(len(values)) * native_dt / time_dilation
    requested = np.r_[np.arange(0.0, duration, 1.0 / control_hz), duration]
    return np.stack([np.interp(requested, original, values[:, i]) for i in range(values.shape[1])], axis=1)


def static_ik_state(current):
    """Static IK is an endpoint query, not a one-control-tick motion."""
    result = current.clone()
    result.dt = None
    return result


def chain_state(state):
    """Do not reindex active-only optimizer knots with augmented joint names."""
    state.knot = None
    state.knot_dt = None
    return state


def minimum_mesh_query_dimension(max_radius, activation_distance=0.01):
    if not np.isfinite(max_radius) or max_radius <= 0:
        raise ValueError("invalid collision radius")
    return 2 * (max_radius + activation_distance + 0.01)
