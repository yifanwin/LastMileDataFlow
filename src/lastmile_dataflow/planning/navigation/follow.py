"""Closed-loop base navigation shared by the cuRobo and VLA execution backends."""

from dataclasses import asdict
from typing import Protocol

import numpy as np

from lastmile_dataflow.robots.interface import RobotCommand
from .routes import yaw_gap
from lastmile_dataflow.sim.simulation import OperationFailure

ARRIVAL_TICKS = 10


class NavigationRunner(Protocol):
    """What navigate() needs from an execution backend."""

    sim: object
    config: object
    navigation_contacts: object
    phase: str

    def tick(self, command: RobotCommand) -> None: ...


def base_error(target, current):
    """[dx, dy, dyaw] from current to target base pose, yaw wrapped to (-π, π]."""
    target = np.asarray(target, float)
    error = target - current
    error[2] = yaw_gap(target[2], current[2])
    return error


def arrived(error, config):
    return np.linalg.norm(error[:2]) <= config.arrival_tolerance_m and abs(error[2]) <= config.arrival_tolerance_rad


def base_step_limits(robot_config, speed, yaw_speed):
    """Per-control-tick translation and yaw limits for base tracking."""
    return (
        min(robot_config.max_base_delta, speed / robot_config.control_hz),
        min(robot_config.max_yaw_delta, yaw_speed / robot_config.control_hz),
    )


def navigate(runner: NavigationRunner, path, goal):
    """Track path poses, settle onto the goal base, and report arrival error."""
    sim, config = runner.sim, runner.config
    robot = sim.robot
    held = {side: robot.arm_positions(side).copy() for side in robot.arms}
    limit, yaw_limit = base_step_limits(robot.config, config.navigation_speed_m_s, config.navigation_yaw_speed_rad_s)
    runner.phase = "navigation"
    steps = 0
    for pose in path["poses"]:
        while not arrived(error := base_error(pose, robot.base_pose()), config):
            steps += 1
            if steps > config.max_navigation_steps:
                raise OperationFailure("navigation_tracking_timeout")
            scale = max(1.0, np.linalg.norm(error[:2]) / limit, abs(error[2]) / yaw_limit)
            runner.tick(RobotCommand(base_xyyaw=robot.base_pose() + error / scale, arm_q=held))
    runner.phase = "arrival"
    for _ in range(ARRIVAL_TICKS):
        error = base_error(goal["base"], robot.base_pose())
        if np.linalg.norm(error[:2]) > robot.config.max_base_delta or abs(error[2]) > robot.config.max_yaw_delta:
            raise OperationFailure("arrival_tracking_error")
        runner.tick(RobotCommand(base_xyyaw=robot.base_pose() + error, arm_q=held))
    actual = robot.base_pose()
    error = base_error(goal["base"], actual)
    if not arrived(error, config):
        raise OperationFailure("arrival_tracking_error")
    return {
        "status": "success",
        "actual_base": actual.tolist(),
        "steps": steps,
        **asdict(runner.navigation_contacts),
        "xy_error_m": float(np.linalg.norm(error[:2])),
        "yaw_error_rad": abs(float(error[2])),
        "arrival_tolerance_m": config.arrival_tolerance_m,
        "arrival_tolerance_rad": config.arrival_tolerance_rad,
    }
