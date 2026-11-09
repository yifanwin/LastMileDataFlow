"""Shared strict-pick window update for NumPy evidence and batched Torch facts.

All inputs describe the same physics timestamp. Negative start means no window.
Collisions are telemetry; fixed-part drift is handled by the fact adapter.
"""
import math


PICK_THRESHOLDS = {
    'version': 'strict-pick-v3', 'lift_m': .05, 'hold_s': 2.,
    'base_translation_m': .002, 'base_yaw_rad': math.radians(.2),
    'lift_hold_error_m': .002, 'torso_error_rad': .002, 'head_error_rad': .002,
    'idle_arm_error_rad': .002, 'relative_translation_m': .01,
    'relative_rotation_rad': math.radians(5), 'max_sample_gap_s': .0041,
    'finger_force_min_n': 1e-6,
}


def update_hold_window(time, held, position, rotation, start, reference_position,
                       reference_rotation, *, xp):
    """Return updated window and duration; xp is numpy or torch."""
    displacement = xp.sqrt(xp.sum((position - reference_position) ** 2, axis=-1))
    # trace(R_ref.T R) is the Frobenius inner product.
    cosine = (xp.sum(reference_rotation * rotation, axis=(-2, -1)) - 1) / 2
    angle = xp.arccos(xp.clip(cosine, -1., 1.))
    reopen = held & ((start < 0) |
                     (displacement > PICK_THRESHOLDS['relative_translation_m']) |
                     (angle > PICK_THRESHOLDS['relative_rotation_rad']))
    start = xp.where(held, xp.where(reopen, time, start), -1.)
    reference_position = xp.where(reopen[..., None], position, reference_position)
    reference_rotation = xp.where(reopen[..., None, None], rotation, reference_rotation)
    duration = xp.where(held, time - start, 0.)
    return start, reference_position, reference_rotation, duration
