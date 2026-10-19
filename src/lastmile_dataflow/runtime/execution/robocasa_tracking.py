"""cuRobo references → FK → closed-loop RoboCasa actions.

The adapter's scratch data supplies reference FK. Only ctx.tick advances
the live simulation; no planner joint targets bypass the common OSC.
"""
import numpy as np

from ..control.interface import RobotCommand
from ...scenes.geometry import tcp_pose
from ..outcomes.pick import rotation_error
from .context import OperationFailure


def follow_points(ctx, planner, positions, side, grip, goal=None):
    robot = ctx.sim.robot
    for point in positions:
        point = np.asarray(point)
        base = planner.base_target_world(point)
        command = RobotCommand(base_xyyaw=base, arm_q={side: planner.model.arm_target(point)},
                               gripper_width={side: grip})
        # FK is evaluated without mutating live qpos or controller targets.
        robot.execution_action(command)
        target = np.eye(4)
        site = robot.tcp_site(side)
        target[:3,3] = robot.scratch.site_xpos[site]
        target[:3,:3] = robot.scratch.site_xmat[site].reshape(3,3)
        for _ in range(120):
            ctx.tick(command)
            measured = tcp_pose(ctx.sim,side)
            gap = base-robot.base_pose()
            if (np.linalg.norm(target[:3,3]-measured[:3,3]) <= .01
                and rotation_error(measured[:3,:3],target[:3,:3]) <= .06
                and np.linalg.norm(gap[:2]) <= ctx.config.arrival_tolerance_m
                and abs(ctx.yaw_gap(base[2],robot.base_pose()[2])) <= ctx.config.arrival_tolerance_rad):
                break
        else:
            raise OperationFailure('robocasa_reference_tracking_timeout')
    measured = tcp_pose(ctx.sim,side)
    if goal is not None and (np.linalg.norm(measured[:3,3]-goal[:3,3])>.015
                            or rotation_error(measured[:3,:3],goal[:3,:3])>.06):
        raise OperationFailure('tcp_tracking_timeout')
