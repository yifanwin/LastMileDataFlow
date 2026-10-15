"""A* 障碍物约束与规划动作桥接。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lastmile_dataflow.planning.astar import Grid, search
from lastmile_dataflow.planning.waypoints import (
    PlanResult,
    arm_waypoint_action,
    navigation_action,
)
from tests.helpers import make_sim


class NavigationTests(unittest.TestCase):
    def test_astar_obstacles_and_no_corner_cut(self):
        grid = Grid(np.zeros(2), 1.0, np.array([[1, 0], [0, 1]], bool))
        self.assertIsNone(search(grid, [0, 0], [1, 1]))
        free = np.ones((5, 5), bool)
        free[:4, 2] = False
        grid = Grid(np.zeros(2), 1.0, free)
        path = search(grid, [0, 0], [4, 0])
        self.assertIsNotNone(path)
        self.assertTrue(all(free[tuple(cell)] for cell in path["cells"]))


class WaypointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim, self.robot = make_sim(self.root)

    def tearDown(self):
        self.sim.close()
        self.tmp.cleanup()

    def test_planning_navigation_bridge(self):
        q = self.sim.robot.group("left_arm") + 0.01
        p = PlanResult("success", tuple(f"left_arm_{i}" for i in range(7)), (q.tolist(),), {})
        a = arm_waypoint_action(p, 0, self.sim.robot, "left")
        np.testing.assert_allclose(a[3:10], [0.01] * 7)
        with self.assertRaises(ValueError):
            PlanResult("no_solution", p.joint_names, p.positions, {})
        with self.assertRaises(ValueError):
            PlanResult("success", p.joint_names, (), {})
        a = navigation_action([1, 0, 1], self.sim.robot)
        self.assertLessEqual(np.linalg.norm(a[:2]), 0.1)
        self.assertLessEqual(abs(a[2]), 0.2)
