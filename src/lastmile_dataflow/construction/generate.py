"""Case-neutral candidate generation primitives.

Line sampling follows the design report: candidates are enumerated *along the local axes of the
support region through the target's current position*, never as a full-region grid, because the case
intent is a controlled displacement of one object rather than a search for any free spot.
"""
import numpy as np

from ..io import digest
from ..scenes.geometry import placement_pose_keeping_orientation, quat_angle


def line_axis_values(region, sim, instance, step_m=.06, max_per_axis=24):
    """(axis, local coordinate) pairs on the two support-local lines through the current position."""
    body = sim.model.body(instance).id
    current = region.local(sim.data.xpos[body])[:2]
    a, b, c, d = region.bounds
    rows = []
    for axis, low, high in ((0, a, b), (1, c, d)):
        first = np.floor((low - current[axis]) / step_m) * step_m + current[axis]
        count = 0
        value = first
        while value <= high + 1e-9 and count < max_per_axis:
            if abs(value - current[axis]) >= 1e-9:
                rows.append((axis, float(value)))
                count += 1
            value += step_m
    return rows


def candidate_record(session, instance, pose, current_pose, *, violations, expected, reason,
                     region_id=None, allowed_operations=('move', 'rotate')):
    """One revision-bound, minimal-edit candidate with same-dimension rank evidence.

    `rank_evidence` carries raw numbers of one kind only (out-of-range count, then a translation
    distance, then a yaw angle). The shared rank is a tuple comparison over exactly those; no
    cross-dimension weighting and no penalty sums.
    """
    pose = [float(x) for x in pose]
    translation = float(np.linalg.norm(np.asarray(pose[:3]) - np.asarray(current_pose[:3])))
    if translation < .008:
        # A sub-millimetre move is a rotation; the precheck forbids a rotate that translates, so the
        # position is carried over exactly rather than re-derived.
        pose[:3] = [float(x) for x in current_pose[:3]]
        translation = 0.
        operation_kind = 'rotate'
    else:
        operation_kind = 'move'
    rotation = float(quat_angle(np.asarray(pose[3:]), np.asarray(current_pose[3:])))
    if operation_kind not in allowed_operations: return None
    operation = {'op': operation_kind, 'instance': instance, 'pose': pose}
    if region_id: operation['region_id'] = region_id
    identity = [{**operation, 'pose': [round(x, 3) for x in pose[:3]] + [round(x, 5) for x in pose[3:]]}]
    key = digest(identity)
    return {'candidate_id': key, 'revision': session.revision, 'instance': instance,
            'operations': [operation], 'reason': reason, 'expected': expected,
            'rank_evidence': {'violations': int(violations), 'edit_magnitude_m': translation,
                              'dyaw_rad': rotation},
            '_identity': key}


def support_move_candidates(sim, session, *, step_m=None, yaw_candidates=(0.), max_per_axis=24,
                            expected=None, region_id=None):
    """Support-plane move candidates for the target, posture preserved, minimal edit ranked first."""
    config = session.config
    parameters = config.parameters
    step_m = float(step_m if step_m is not None else parameters.get('line_step_m', .06))
    max_per_axis = int(parameters.get('max_line_samples', max_per_axis))
    region = session.placements[config.target]
    instance = config.target
    body = sim.model.body(instance).id
    current = np.r_[sim.data.xpos[body], sim.data.xquat[body]]
    rows, seen = [], set()
    for axis, value in line_axis_values(region, sim, instance, step_m, max_per_axis):
        for yaw in yaw_candidates:
            if not np.isfinite(yaw) or abs(yaw) > np.pi: continue  # base_theta limit: filter, never clip
            xy = list(region.local(sim.data.xpos[body])[:2])
            xy[axis] = value
            pose = placement_pose_keeping_orientation(sim, body, region, xy, yaw)
            if pose is None: continue
            hint = dict(expected(pose, xy, axis)) if expected else {}
            record = candidate_record(session, instance, pose, current,
                                      violations=hint.pop('violations', 0), expected=hint,
                                      reason='support_local_line_displacement',
                                      region_id=region_id,
                                      allowed_operations=config.allowed_operations)
            if record is None or record['_identity'] in seen: continue
            seen.add(record['_identity'])
            rows.append(record)
    return rows
