"""Trajectory-to-close transition must hold the last planned mobile base pose."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.robots.interface import RobotCommand
from lastmile_dataflow.operation.control.context import ContinuousContext


class RobocasaTrackingTests(unittest.TestCase):
    def test_close_holds_the_base_reached_by_the_trajectory(self):
        robot = SimpleNamespace(
            base=np.zeros(3), scratch=SimpleNamespace(site_xpos=np.zeros((1, 3)), site_xmat=np.eye(3).reshape(1, 9))
        )
        robot.base_pose = lambda: robot.base.copy()
        robot.tcp_site = lambda side: 0
        robot.manipulation_command = lambda side, q, grip, height, base: RobotCommand(
            base_xyyaw=base, arm_q={side: q}, gripper_width={side: grip}
        )

        def reference(command):
            robot.scratch.site_xpos[0] = [*command.base_xyyaw[:2], 1.0]

        def tick(command):
            robot.base = np.asarray(command.base_xyyaw).copy()

        def measured(sim, side):
            pose = np.eye(4)
            pose[:3, 3] = [*robot.base[:2], 1.0]
            return pose

        robot.execution_action = reference
        ctx = SimpleNamespace(
            sim=SimpleNamespace(robot=robot),
            manip_base_target=np.zeros(3),
            config=SimpleNamespace(arrival_tolerance_m=0.015, arrival_tolerance_rad=0.025),
            tick=tick,
        )
        planner = SimpleNamespace(
            base_target_world=lambda point: point.copy(), model=SimpleNamespace(arm_target=lambda point: np.zeros(7))
        )
        points = [np.array([0.1, 0.05, 0.1]), np.array([0.3, 0.15, 0.4])]
        with patch("lastmile_dataflow.operation.control.context.tcp_pose", side_effect=measured):
            ContinuousContext._follow_robocasa(ctx, planner, points, "right", 0.08)
        reached = measured(ctx.sim, "right")
        ContinuousContext.arm_tick(ctx, "right", np.zeros(7), 0.0, 0.0)
        np.testing.assert_allclose(robot.base, points[-1])
        np.testing.assert_allclose(measured(ctx.sim, "right"), reached)
