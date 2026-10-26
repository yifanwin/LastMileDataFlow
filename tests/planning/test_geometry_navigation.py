"""Real MuJoCo collision geometry on small fixtures; no real-robot evidence."""
import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.data.evaluation.benchmark_config import BenchmarkConfig
from lastmile_dataflow.data.evaluation.recovery import RecoveryRegion, difficulty
from lastmile_dataflow.planning.astar import Grid
from lastmile_dataflow.planning.navigation import GeometryNavigation, GeometryCheck, NavigationSettings, pose_samples, sampled_cost
from lastmile_dataflow.runtime.simulation import Simulation
from tests.helpers import fixtures


class GeometryNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source, self.robot = fixtures(self.tmp.name)
        tree = ET.parse(self.robot.model_path)
        ET.SubElement(tree.getroot().find('worldbody/body'), 'geom', name='chassis',
                      type='box', size='.3 .06 .1', pos='0 0 -.3', mass='1')
        tree.write(self.robot.model_path)
        self.grid = Grid(np.array([-2., -2.]), .1, np.zeros((41, 41), dtype=bool))

    def navigator(self, poses, obstacle=None, *, margin=.02, weight=2.):
        if obstacle is not None:
            tree = ET.parse(self.source.xml_path)
            ET.SubElement(tree.getroot().find('worldbody'), 'geom', **obstacle)
            tree.write(self.source.xml_path)
        sim = Simulation.from_source(self.source, self.robot, target='target')
        self.addCleanup(sim.close)
        stations = []
        for i, pose in enumerate(poses):
            initial = copy.deepcopy(self.robot.initial)
            initial[sim.robot.initial_base_field] = pose
            stations.append({'station_id': str(i), 'base': pose, 'xy': pose[:2], 'initial': initial})
        navigator = GeometryNavigation(sim, self.grid, stations, NavigationSettings(target_m=margin, clearance_weight=weight))
        self.addCleanup(navigator.close)
        navigator.hold(stations[0])
        return sim, navigator, stations

    def test_blocked_raster_does_not_reject_valid_table_side_route(self):
        sim, nav, stations = self.navigator([[0., 0., 0.], [-.5, 0., 0.]],
            {'name': 'table_top', 'type': 'box', 'pos': '.4 0 .7', 'size': '.2 .4 .04'})
        before = sim.state_vector().copy()
        result = nav.plan(stations[0]['base'], [{'xy': [-.5, 0.], 'yaw': 0., 'anchor_station_id': '1'}])
        self.assertTrue(result['conservative_start_blocked'])
        self.assertEqual(result['reason'], 'valid')
        self.assertAlmostEqual(result['path']['length_m'], .5)
        self.assertGreaterEqual(result['path']['clearance_lower_bound_m'], .02)
        np.testing.assert_array_equal(sim.state_vector(), before)
        self.assertFalse(nav.sim.started)

    def test_graph_detours_through_stations_and_rejects_wall_crossing(self):
        _, nav, stations = self.navigator([[-.7, 0., 0.], [-.7, .9, 0.], [.7, .9, 0.], [.7, 0., 0.]],
            {'name': 'wall', 'type': 'box', 'pos': '0 0 .2', 'size': '.05 .6 .2'})
        self.assertFalse(nav.check_edge(stations[0]['base'], stations[-1]['base']).valid)
        result = nav.plan(stations[0]['base'], [{'xy': [.7, 0.], 'yaw': 0., 'anchor_station_id': '3'}])
        self.assertEqual(result['reason'], 'valid')
        self.assertGreater(result['path']['length_m'], 1.4)
        self.assertTrue(any(abs(p[1]) > .6 for p in result['path']['poses']))
        nav.stations = []
        no_route = nav.plan(stations[0]['base'], [{'xy': [.7, 0.], 'yaw': 0.}])
        self.assertIsNotNone(no_route['path'])
        self.assertEqual(no_route['reason'], 'valid')

    def test_rotation_midpoint_collision_is_checked(self):
        _, nav, stations = self.navigator([[0., 0., 0.], [0., 0., np.pi / 2]],
            {'name': 'corner', 'type': 'box', 'pos': '.23 .23 .2', 'size': '.04 .04 .1'})
        self.assertTrue(all(nav.check_pose(s['base']).valid for s in stations))
        self.assertFalse(nav.check_edge(stations[0]['base'], stations[1]['base']).valid)

    def test_positive_gap_below_target_remains_valid_and_has_soft_cost(self):
        _, nav, stations = self.navigator([[0., 0., 0.]],
            {'name': 'near_wall', 'type': 'box', 'pos': '.36 0 .2', 'size': '.05 .3 .1'})
        checked = nav.check_pose(stations[0]['base'])
        self.assertTrue(checked.valid)
        self.assertEqual(checked.reason, 'valid')
        self.assertAlmostEqual(checked.clearance_m, .01)
        result = nav.plan([-.5, 0., 0.], [{'xy': [0., 0.], 'yaw': 0.}])
        self.assertIsNotNone(result['path'])
        self.assertEqual(result['reason'], 'valid')
        self.assertGreater(result['path']['clearance_penalty_cost'], 0.)

    def test_room_astar_supplies_detour_without_intermediate_stations(self):
        self.grid.free[:] = True
        self.grid.free[13:28, 16:25] = False
        _, nav, stations = self.navigator([[-.7, 0., 0.], [.7, 0., 0.]],
            {'name': 'wall', 'type': 'box', 'pos': '0 0 .2', 'size': '.05 .4 .2'})
        result = nav.plan(stations[0]['base'], [{'xy': [.7, 0.], 'yaw': 0.}])
        self.assertEqual(result['reason'], 'valid')
        self.assertEqual(result['path']['method'], 'geometry_soft_astar')
        self.assertGreater(result['path']['length_m'], 1.4)

    def test_recovery_region_keeps_blocked_anchor_and_roundtrips_pose_boxes(self):
        _, nav, stations = self.navigator([[0., 0., 0.], [-.5, 0., 0.]])
        measured = [{'station_id': s['station_id'], 'xy': s['xy'], 'yaw': s['base'][2],
                     'high': i == 1, 'successes': 3 if i else 0, 'trials': 3}
                    for i, s in enumerate(stations)]
        region = RecoveryRegion.build(self.grid, measured, BenchmarkConfig(), navigation=nav)
        self.assertTrue(region.contains([-.5, 0.], 0.))
        self.assertTrue(region.contains([-.49, .01], .03))
        self.assertFalse(region.contains([0., 0.], 0.))
        self.assertFalse(region.contains([-.5, 0.], np.pi))
        restored = RecoveryRegion.from_dict(region.to_dict())
        self.assertEqual(region.goals(), restored.goals())
        result = nav.plan(stations[0]['base'], restored.goals())
        metrics = difficulty(restored, stations[0], stations, reference_path=result['path'])
        self.assertGreater(metrics['path_length_m'], 0.)
        self.assertEqual(metrics['recovery_ratio'], .5)
        self.assertTrue(restored.contains(result['path']['goal']['xy'], result['path']['goal']['yaw']))

    def test_budget_exhaustion_is_not_reported_as_no_solution(self):
        _, nav, stations = self.navigator([[0., 0., 0.], [-.5, 0., 0.]])
        nav.budget_s = -1.
        result = nav.plan(stations[0]['base'], [{'xy': [-.5, 0.], 'yaw': 0.}])
        self.assertEqual(result['reason'], 'navigation_search_budget_exhausted')
        self.assertIsNone(result['path'])

    def test_shortest_yaw_samples_cross_wrap_without_full_rotation(self):
        samples = pose_samples([0., 0., np.pi - .01], [0., 0., -np.pi + .01])
        self.assertLess(abs(samples[-1, 2] - samples[0, 2]), .021)

    def test_soft_cost_selects_wider_detour_while_zero_weight_selects_shortcut(self):
        poses = [[-.9, 0., 0.], [.9, 0., 0.]]
        _, nav, _ = self.navigator(poses,
            {'name': 'nearby_wall', 'type': 'box', 'pos': '0 .075 .2', 'size': '.5 .01 .1'})
        direct = nav.check_edge(*poses)
        result = nav.plan(poses[0], [{'xy': poses[1][:2], 'yaw': 0.}])
        self.assertLess(result['path']['route_cost'], direct.cost)
        self.assertTrue(any(p[1] < -.05 for p in result['path']['poses']))
        _, unweighted, _ = self.navigator(poses, weight=0.)
        shortcut = unweighted.plan(poses[0], [{'xy': poses[1][:2], 'yaw': 0.}])
        self.assertAlmostEqual(shortcut['path']['length_m'], 1.8)
        self.assertEqual(shortcut['path']['clearance_penalty_cost'], 0.)

    def test_narrow_corridor_is_valid_without_a_twenty_mm_route(self):
        tree = ET.parse(self.source.xml_path)
        ET.SubElement(tree.getroot().find('worldbody'), 'geom', name='lower_wall', type='box',
                      pos='0 -.075 .2', size='.8 .01 .1')
        tree.write(self.source.xml_path)
        self.grid = Grid(np.array([-.5, -.01]), .01, np.zeros((3, 101), bool))
        _, nav, _ = self.navigator([[-.4, 0., 0.], [.4, 0., 0.]],
            {'name': 'upper_wall', 'type': 'box', 'pos': '0 .075 .2', 'size': '.8 .01 .1'})
        result = nav.plan([-.4, 0., 0.], [{'xy': [.4, 0.], 'yaw': 0.}])
        self.assertIsNotNone(result['path'])
        self.assertLess(result['path']['clearance_lower_bound_m'], .02)
        self.assertGreater(result['path']['route_cost'], result['path']['length_m'])

    def test_shallow_penetration_is_allowed_but_severe_penetration_is_rejected(self):
        _, nav, _ = self.navigator([[.012, 0., 0.]],
            {'name': 'wall', 'type': 'box', 'pos': '.36 0 .2', 'size': '.05 .3 .1'})
        shallow = nav.check_pose([.012, 0., 0.])
        self.assertTrue(shallow.valid)
        self.assertAlmostEqual(shallow.clearance_m, -.002)
        severe = nav.check_pose([.018, 0., 0.])
        self.assertFalse(severe.valid)
        self.assertEqual(severe.reason, 'navigation_severe_collision')

    def test_clearance_cost_is_continuous_and_independent_of_sample_density(self):
        settings = NavigationSettings()
        for distance in (.019, .021):
            coarse = np.array([[0., 0., 0.], [1., 0., .4]])
            fine = np.linspace(coarse[0], coarse[-1], 101)
            self.assertAlmostEqual(sampled_cost(coarse, [distance] * 2, settings)[0],
                                   sampled_cost(fine, [distance] * 101, settings)[0])
        low = sampled_cost(coarse, [.019] * 2, settings)[0]
        high = sampled_cost(coarse, [.021] * 2, settings)[0]
        self.assertLess(low - high, .01)
        rotation = np.array([[0., 0., 0.], [0., 0., .4]])
        self.assertGreater(sampled_cost(rotation, [.01] * 2, settings)[1], 0.)

    def test_budget_keeps_a_complete_feasible_incumbent(self):
        poses = [[0., 0., 0.], [-.5, 0., 0.]]
        _, nav, _ = self.navigator(poses,
            {'name': 'near_wall', 'type': 'box', 'pos': '.36 0 .2', 'size': '.05 .3 .1'})
        direct_key = (nav._key(poses[0]), nav._key(poses[1]))
        original = nav.check_edge
        def exhaust_after_candidate(start, goal):
            if direct_key in nav.edge_checks:
                nav.exhausted = True
                return GeometryCheck(False, 'navigation_search_budget_exhausted')
            return original(start, goal)
        with patch.object(nav, 'check_edge', side_effect=exhaust_after_candidate):
            result = nav.plan(poses[0], [{'xy': poses[1][:2], 'yaw': 0.}])
        self.assertEqual(result['reason'], 'valid')
        self.assertEqual(result['path']['optimization_status'], 'budget_limited')

    def test_room_refinement_budget_retains_a_multiedge_station_route(self):
        poses = [[-.7, 0., 0.], [-.7, .9, 0.], [.7, .9, 0.], [.7, 0., 0.]]
        _, nav, _ = self.navigator(poses,
            {'name': 'wall', 'type': 'box', 'pos': '0 0 .2', 'size': '.05 .6 .2'})
        def expire_refinement(*args):
            nav.exhausted = True
            return set()
        with patch.object(nav, '_neighbors', side_effect=expire_refinement):
            result = nav.plan(poses[0], [{'xy': poses[-1][:2], 'yaw': 0.}])
        self.assertEqual(result['reason'], 'valid')
        self.assertGreaterEqual(len(result['path']['poses']), 3)
        self.assertEqual(result['path']['optimization_status'], 'budget_limited')
