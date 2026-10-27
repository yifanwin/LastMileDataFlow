"""A* 障碍物约束与规划动作桥接。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lastmile_dataflow.planning.navigation.astar import Grid, search, scene_grid
from lastmile_dataflow.planning.waypoints import (
    PlanResult,
    arm_waypoint_action,
    navigation_action,
)
from tests.helpers import make_sim


class NavigationTests(unittest.TestCase):
    def test_room_navigation_can_detour_outside_sampling_circle(self):
        import xml.etree.ElementTree as ET
        from tests.helpers import fixtures
        from lastmile_dataflow.runtime.simulation import Simulation

        with tempfile.TemporaryDirectory() as directory:
            source, robot = fixtures(directory)
            tree = ET.parse(source.xml_path)
            ET.SubElement(tree.getroot().find('worldbody'), 'geom', name='corridor_blocker',
                          type='box', size='.1 1.1 .2', pos='0 0 .2')
            tree.write(source.xml_path)
            sim = Simulation.from_source(source, robot, target='target')
            try:
                disk = scene_grid(sim, [0, 0], 1., .1, .1, 0.)
                room = scene_grid(sim, [0, 0], None, .1, .1, 0.)
                self.assertIsNone(search(disk, [-.8, 0], [.8, 0]))
                path = search(room, [-.8, 0], [.8, 0])
                self.assertIsNotNone(path)
                self.assertTrue(any(np.linalg.norm(xy) > 1. for xy in path['xy']))
                self.assertIsNone(room.cell([6., 0.]))
                measured = np.linalg.norm(np.diff(path['xy'], axis=0), axis=1).sum()
                self.assertAlmostEqual(path['length_m'], measured)
                sim.local_model = {'removed_roots': ['deleted_furniture']}
                with self.assertRaisesRegex(ValueError, 'full scene'):
                    scene_grid(sim, [0, 0], None, .1, .1, 0.)
            finally:
                sim.close()

    def test_path_length_includes_exact_station_connectors(self):
        grid = Grid(np.zeros(2), 1., np.ones((3, 3), bool))
        path = search(grid, [.2, .1], [1.8, .1])
        measured = np.linalg.norm(np.diff(path['xy'], axis=0), axis=1).sum()
        self.assertAlmostEqual(path['length_m'], measured)
        self.assertGreater(path['length_m'], 2.)

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
