"""规划/导航的执行边界：只接受显式成功结果和当前实测状态。

cuRobo GPU 规划器与导航路径搜索在后续阶段实现，本模块不冒充规划能力。
"""
from dataclasses import dataclass
import numpy as np

from ..robots.action import InvalidAction, validate_action, vector


@dataclass(frozen=True)
class PlanResult:
    status: str  # success / no_solution / fov_constraint_failed / infrastructure_error / not_tested
    joint_names: tuple
    positions: tuple
    diagnostics: dict
    source: str = "curobo"

    def __post_init__(self):
        if self.status not in ("success", "no_solution", "fov_constraint_failed", "infrastructure_error", "not_tested"):
            raise ValueError("unknown planner result")
        if self.status != "success" and self.positions:
            raise ValueError("non-success plan must not contain executable waypoints")
        if self.status == "success" and not self.positions:
            raise ValueError("success plan requires actual waypoints")


def arm_waypoint_action(plan, index, robot, side, *, torso_height=None):
    if side not in ("left", "right"):
        raise ValueError("unknown arm")
    expected = tuple(f"{side}_arm_{i}" for i in range(7))
    if plan.status != "success" or tuple(plan.joint_names) != expected:
        raise InvalidAction("plan unsuccessful or joint ordering mismatched")
    a = robot.neutral_action()
    start = 3 if side == "left" else 11
    a[start:start + 7] = vector(plan.positions[index], 7) - robot.group(side + "_arm")
    if torso_height is not None:
        a[19] = torso_height
    return validate_action(a, robot.config, fixed_base=True)


def navigation_action(goal_xy_yaw, robot):
    delta = vector(goal_xy_yaw, 3) - robot.group("base")
    delta[2] = (delta[2] + np.pi) % (2 * np.pi) - np.pi
    scale = max(1., np.linalg.norm(delta[:2]) / robot.config.max_base_delta,
                abs(delta[2]) / robot.config.max_yaw_delta)
    a = robot.neutral_action()
    a[:3] = delta / scale
    return validate_action(a, robot.config)
