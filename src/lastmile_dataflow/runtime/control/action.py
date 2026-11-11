"""RBY1 20D protocol. Base/arms are deltas from measured state; grippers/torso are absolute.

Position profile units and indices are defined below. Native motor conversion lives in native_actions."""
import numpy as np

# 供人阅读/文档生成用的布局表：(组名, 起始索引, 结束索引, 语义)
ACTION_LAYOUT = (("base", 0, 3, "delta_world_xy_yaw"),
                 ("left_arm", 3, 10, "delta_joint_rad"),
                 ("left_gripper", 10, 11, "absolute_m"),
                 ("right_arm", 11, 18, "delta_joint_rad"),
                 ("right_gripper", 18, 19, "absolute_m"),
                 ("torso", 19, 20, "absolute_height_parameter"))


class InvalidAction(ValueError):
    pass


def vector(value, size):
    """Return an independent finite float vector of the requested size."""
    try:
        a = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise InvalidAction("action must be numeric") from exc
    if a.shape != (size,) or not np.isfinite(a).all():
        raise InvalidAction(f"expected finite vector of size {size}; got {a.shape}")
    return a.copy()


def torso_joints(h):
    """Expand coupling parameter h into six joint angles; h is not a world height."""
    return np.array([0., h, -2 * h, h, 0., 0.])


def validate_action(value, config, *, fixed_base=False):
    """Check the 20D position-profile protocol before joint/actuator mapping."""
    a = vector(value, 20)
    if np.linalg.norm(a[:2]) > config.max_base_delta or abs(a[2]) > config.max_yaw_delta:
        raise InvalidAction("base delta exceeds configured bound")
    if fixed_base and np.any(a[:3] != 0):
        raise InvalidAction("fixed-base trial prohibits base commands")
    if np.max(np.abs(np.r_[a[3:10], a[11:18]])) > config.max_arm_delta:
        raise InvalidAction("arm delta exceeds configured bound")
    for i in (10, 18):
        if not config.gripper_limits[0] <= a[i] <= config.gripper_limits[1]:
            raise InvalidAction("gripper absolute target out of range")
    if not config.torso_limits[0] <= a[19] <= config.torso_limits[1]:
        raise InvalidAction("torso height out of range")
    return a


ANGULAR_TOLERANCE_RAD = .003
LINEAR_TOLERANCE_M = .001


def joint_command_interval(robot, group, index):
    name = robot.groups[group][index]
    joint = robot.joints[name]
    lo, hi = -np.inf, np.inf
    if robot.model.jnt_limited[joint]:
        lo, hi = map(float, robot.model.jnt_range[joint])
    if index < len(robot.actuators[group]):
        actuator = robot.act_ids[robot.actuators[group][index]]
        if robot.model.actuator_ctrllimited[actuator]:
            alo, ahi = robot.model.actuator_ctrlrange[actuator]
            lo, hi = max(lo, float(alo)), min(hi, float(ahi))
    if lo > hi:
        raise ValueError('inconsistent joint and actuator limits: '+name)
    return lo, hi


def yaw_interval(robot):
    return joint_command_interval(robot, 'base', 2)


def _bounded_command(value, limits, tolerance, message):
    lo, hi = limits
    if not np.isfinite(value) or value < lo - tolerance or value > hi + tolerance:
        raise InvalidAction(message)
    return float(np.clip(value, lo, hi))


def project(value, limits, tolerance, name, events):
    lo, hi = limits
    bounded = _bounded_command(
        value, limits, tolerance, f'command limit: {name}={value}, [{lo}, {hi}]')
    if bounded != value:
        events.append({'joint':name, 'input_target':float(value), 'submitted_target':bounded,
                       'limits':[lo,hi], 'tolerance':tolerance})
    return bounded


def _project_delta_commands(robot, action, events):
    for group, offset, count in (('base', 0, 3), ('left_arm', 3, 7), ('right_arm', 11, 7)):
        measured = robot.group(group)
        for index in range(count):
            tolerance = LINEAR_TOLERANCE_M if group == 'base' and index < 2 else ANGULAR_TOLERANCE_RAD
            target = project(
                float(measured[index] + action[offset + index]),
                joint_command_interval(robot, group, index), tolerance,
                robot.groups[group][index], events)
            action[offset + index] = target - measured[index]


def _gripper_command_limits(robot, group):
    lo, hi = joint_command_interval(robot, group, 0)
    paired_lo, paired_hi = joint_command_interval(robot, group, 1)
    return (max(lo, -paired_hi, robot.config.gripper_limits[0]),
            min(hi, -paired_lo, robot.config.gripper_limits[1]))


def _torso_command_limits(robot):
    lo, hi = robot.config.torso_limits
    for index, multiplier in ((1, 1), (2, -2), (3, 1)):
        lower, upper = joint_command_interval(robot, 'torso', index)
        lower, upper = sorted((lower / multiplier, upper / multiplier))
        lo, hi = max(lo, lower), min(hi, upper)
    return lo, hi


def sanitize_action(robot, value, *, fixed_base=False):
    """Project tiny overshoots against joint, actuator and protocol limits.

    Yaw is a limited scalar: wrapping across ±pi would command an illegal jump.
    """
    action = vector(value, 20)
    events = []
    _project_delta_commands(robot, action, events)
    for side, offset in (('left', 10), ('right', 18)):
        group = side + '_gripper'
        action[offset] = project(action[offset], _gripper_command_limits(robot, group),
                                 LINEAR_TOLERANCE_M, group, events)
    action[19] = project(action[19], _torso_command_limits(robot),
                         ANGULAR_TOLERANCE_RAD, 'torso_h', events)
    for index in (0, 4, 5):
        robot._check_joint(robot.groups['torso'][index], 0.)
    validate_action(action, robot.config, fixed_base=fixed_base)
    return action, events


def local_yaw_bounds(robot, reference_yaw):
    lo,hi=yaw_interval(robot)
    return lo-reference_yaw,hi-reference_yaw


COMMAND_TOLERANCE_RAD = .003
FEEDBACK_TOLERANCE_RAD = .003
# Gross-violation guard for measured torso height, NOT a tracking spec.
# Under `torso_error_policy='record_only'` a small feedback excursion is telemetry;
# only a margin this large (≈6.8% of the 0.738 m stroke) indicates real loss of
# control and stops the attempt. The measured excursions observed in practice sit
# ~2e-5 rad past FEEDBACK_TOLERANCE_RAD, i.e. >1000x below this.
TORSO_SEVERE_FEEDBACK_RAD = .05


def bounded_torso_command(value, limits):
    value = float(value)
    return _bounded_command(value, limits, COMMAND_TOLERANCE_RAD,
                            'torso height exceeds clipping tolerance')


def torso_feedback_margin(value, limits):
    """Positive amount outside the h range; NaN is unsafe, not tolerated."""
    value = float(value)
    if not np.isfinite(value):
        return float('inf')
    return max(0., limits[0]-value, value-limits[1])
