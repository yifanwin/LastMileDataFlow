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
