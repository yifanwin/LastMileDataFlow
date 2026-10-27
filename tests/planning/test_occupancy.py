"""Measured obstacle distance differs from travel distance and robot inflation."""
from types import SimpleNamespace
import unittest

import mujoco
import numpy as np

from lastmile_dataflow.planning.navigation.astar import scene_occupancy, segment_free, search, distance_field


class OccupancyTests(unittest.TestCase):
    def scene(self):
        model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
            <geom name="thin_obstacle" type="box" pos=".025 0 .2" size=".001 .3 .2"/>
            </worldbody></mujoco>''')
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        return SimpleNamespace(model=model, data=data)

    def test_thin_obstacles_block_raster_and_direct_path_but_allow_detour(self):
        occupancy = scene_occupancy(self.scene(), [0, 0], 1., .1)
        nav = occupancy.navigation(.1, .02)
        self.assertTrue(occupancy.occupied[nav.cell([0., 0.])])
        self.assertFalse(nav.free[nav.cell([0., 0.])])
        self.assertFalse(segment_free(nav, [-.5, 0], [.5, 0]))
        path = search(nav, [-.5, 0], [.5, 0])
        self.assertIsNotNone(path)
        self.assertGreater(path['length_m'], 1.)
        self.assertTrue(all(segment_free(nav, a, b) for a, b in zip(path['xy'], path['xy'][1:])))

    def test_physical_clearance_does_not_subtract_navigation_margin(self):
        occupancy = scene_occupancy(self.scene(), [0, 0], 1., .05)
        tight = occupancy.navigation(.1, 0.)
        wide_margin = occupancy.navigation(.1, .2)
        cell = tight.cell([.25, 0.])
        self.assertTrue(tight.free[cell])
        self.assertFalse(wide_margin.free[cell])
        measured = occupancy.clearance_base(.1)[cell]
        self.assertGreater(measured, 0.)
        self.assertLess(measured, .2)
        self.assertAlmostEqual(measured, occupancy.obstacle_distance_m[cell] - .1)
        self.assertFalse(np.any(wide_margin.free & occupancy.occupied))
        distances = distance_field(tight, [-.5, 0], 4.)
        self.assertGreater(distances[cell], 1.)
        self.assertLess(occupancy.obstacle_distance_m[cell], .3)

    def test_zero_footprint_does_not_make_obstacle_or_off_map_free(self):
        occupancy = scene_occupancy(self.scene(), [0, 0], 1., .1)
        raw = occupancy.navigation(0., 0.)
        np.testing.assert_array_equal(raw.free, ~occupancy.occupied)
        self.assertFalse(raw.free[0, 0])
        self.assertIsNone(raw.cell([1.2, 0.]))
