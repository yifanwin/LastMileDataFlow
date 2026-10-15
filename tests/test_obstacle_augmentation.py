"""Synthetic logic/lifecycle regressions. Real ProcTHOR smoke is separate."""
import copy
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mujoco
import numpy as np

from helpers import fixtures
from lastmile_dataflow.scenes.loading.assets import EditAssetCatalog
from lastmile_dataflow.scenes.augmentation.config import ObstacleConfig, load_obstacle_config
from lastmile_dataflow.scenes.augmentation.regions import rotated_placement, station_regions, augmented_grid
from lastmile_dataflow.io import write_json
from lastmile_dataflow.runtime.construction.session import ObstacleEditSession, clone_preparation
from lastmile_dataflow.runtime.construction.preparation import SettleConfig, settle_scene
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.augmentation.checks import collision_pairs, load_support, matched_source_effects
from lastmile_dataflow.runtime.construction.session import matched_control
from lastmile_dataflow.scenes.graph import build_scene_graph
from lastmile_dataflow.data.rendering.obstacle_views import render_obstacle_pair
from lastmile_dataflow.data.rendering.edit_views import ViewConfig


def config():
    return ObstacleConfig('baseline.json', 'dataset', 'objects', 'target')


class ConfigTests(unittest.TestCase):
    def test_defaults_and_bad_count(self):
        self.assertEqual(config().obstacle_count, [1, 1])
        for count in ([0, 1], [1, 4], [3, 1]):
            with self.assertRaises(ValueError):
                replace(config(), obstacle_count=count)

    def test_mandatory_categories_cannot_silently_disappear(self):
        with self.assertRaises(ValueError):
            replace(config(), ground_categories=['Stool'])

    def test_unknown_keys_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            values = {'data_root': tmp, 'baseline_config': 'base.json', 'dataset_dir': 'data', 'asset_library': 'assets',
                      'target_instance': 'target', 'input_snapshot': '../scene'}
            write_json(path, values)
            loaded = load_obstacle_config(path)
            self.assertEqual(loaded.baseline_config, str(Path(__file__).resolve().parents[1] / 'base.json'))
            self.assertEqual(loaded.input_snapshot, str((Path(tmp) / '../scene').resolve()))
            write_json(path, {**values, 'agent_calls': 1})
            with self.assertRaises(TypeError):
                load_obstacle_config(path)

    def test_five_variants_not_five_station_trials(self):
        root = Path(__file__).resolve().parents[1]
        c = load_obstacle_config(root / 'configs/case_edits/procedural_obstacles_val103_cup30_chair_debug.json')
        self.assertEqual(c.variant_count, 5)
        self.assertEqual(c.obstacle_count, [1, 1])
        self.assertEqual(c.mandatory_categories, ['Chair'])
        self.assertEqual(c.target_instance, 'cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2')
        self.assertTrue(Path(c.baseline_config).is_file())

    def test_nonfinite_budget_rejected(self):
        with self.assertRaises(ValueError):
            replace(config(), variant_timeout_s=float('inf'))


class GeometryTests(unittest.TestCase):
    def test_off_center_root_and_rotation(self):
        points = np.array([[1, 2, -.2], [3, 4, .8]])
        for yaw in (0., np.pi / 2):
            pose, placed = rotated_placement(points, [7., 9.], yaw, .5, .003)
            np.testing.assert_allclose((placed[:, :2].min(0) + placed[:, :2].max(0)) / 2, [7, 9])
            self.assertAlmostEqual(placed[:, 2].min(), .503)
            self.assertEqual(len(pose['quaternion_wxyz']), 4)

    def test_midpoint_not_inherited_from_endpoints(self):
        xml = '''<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/>
        <body name="block" pos="0 0 .1"><geom name="block_geom" type="box" size=".08 .08 .1"/></body></worldbody></mujoco>'''
        m = mujoco.MjModel.from_xml_string(xml)
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d)
        stations = [{'station_id': 'a', 'xy': [-.2, 0.], 'grid_index': [0, 0], 'geometry': 'valid'},
                    {'station_id': 'b', 'xy': [.2, 0.], 'grid_index': [1, 0], 'geometry': 'valid'}]
        task = {'anchor_world': [0, 0, .5], 'support_obstacles': []}
        regions = station_regions(sim, stations, task, ExecutionConfig(radius_m=.6, map_resolution_m=.025), .02, .12)
        self.assertEqual(len(regions['rejected_midpoints']), 1)
        self.assertEqual(len(regions['points']), 2)
        self.assertTrue(regions['edges'][0]['connected'])

    def test_no_all_pairs_midpoint_explosion(self):
        m = mujoco.MjModel.from_xml_string('<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/></worldbody></mujoco>')
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d)
        stations = [{'station_id': 'a', 'xy': [-.2, 0.], 'grid_index': [0, 0], 'geometry': 'valid'},
                    {'station_id': 'b', 'xy': [.2, 0.], 'grid_index': [2, 0], 'geometry': 'valid'}]
        regions = station_regions(sim, stations, {'anchor_world': [0, 0, .5], 'support_obstacles': []}, ExecutionConfig(radius_m=.6), .02, .12)
        self.assertEqual(regions['edges'], [])

    def test_seat_above_chassis_still_blocks_navigation(self):
        m = mujoco.MjModel.from_xml_string('<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/><body name="chair" pos="0 0 .7"><geom type="box" size=".2 .2 .1"/></body></worldbody></mujoco>')
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d)
        grid = augmented_grid(sim, {'anchor_world': [0, 0, .5], 'support_obstacles': []}, ExecutionConfig(radius_m=.6), .1, ['chair'])
        self.assertFalse(grid.free[grid.cell([0, 0])])

    def test_surface_coverage_rechecked_after_settle_not_from_flag(self):
        xml = '''<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/>
        <body name="table" pos="0 0 .4"><geom name="table_top" type="box" size=".3 .3 .04"/></body>
        <body name="procedural_obstacle_0" pos="0 0 .493"><freejoint name="object_free"/>
        <geom name="object_geom" type="box" size=".05 .05 .05" mass=".1"/></body></worldbody></mujoco>'''
        m = mujoco.MjModel.from_xml_string(xml)
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(model=m, data=d, started=False, closed=False,
                              robot=SimpleNamespace(execution_started=False, config=SimpleNamespace(namespace='robot_0/')),
                              state_vector=lambda: np.r_[d.qpos, d.qvel, d.ctrl])
        settle_scene(sim, SettleConfig())
        p = {'kind': 'surface', 'support_geom': 'table_top', 'support_instance': 'table',
             'support_height_m': .44, 'surface_coverage_verified': True}
        self.assertTrue(load_support(sim, [p], config())['valid'])
        q = int(m.jnt_qposadr[m.joint('object_free').id])
        d.qpos[q] = .29  # center is on table, but the settled footprint extends beyond edge
        mujoco.mj_forward(m, d)
        report = load_support(sim, [p], config())
        self.assertFalse(report['valid'])
        self.assertFalse(report['objects'][0]['coverage'])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source, robot = fixtures(self.root)
        self.sim = Simulation.from_source(source, robot, target='target')
        asset = self.root / 'chair.xml'
        asset.write_text('<mujoco><worldbody><body name="chair"><freejoint/><geom name="chair_geom" type="box" size=".2 .2 .5" mass="2"/><site name="receptacle_debug" type="box" size=".1 .1 .1" rgba="1 0 0 1"/></body></worldbody></mujoco>')
        manifest = self.root / 'assets.json'
        write_json(manifest, {'asset_catalog_version': '0.1', 'assets': [{'asset_id': 'chair', 'xml_path': str(asset), 'root_body': 'chair', 'category': 'Chair'}]})
        self.assets = EditAssetCatalog(manifest)
        self.session = ObstacleEditSession(self.sim, self.assets, SettleConfig(require_source_stability=False))
        self.placement = {'asset_id': 'chair', 'kind': 'ground', 'pose': {'position': [4., 0., .503], 'quaternion_wxyz': [1, 0, 0, 0]}, 'support_height_m': 0., 'support_geom': None}

    def tearDown(self):
        self.session.close()
        self.sim.close()
        self.tmp.cleanup()

    def test_add_real_body_and_migrate_target_state(self):
        candidate = self.session.candidate([self.placement])
        self.assertGreater(candidate.model.nq, self.sim.model.nq)
        self.assertEqual(candidate.model.body(candidate.target_id).name, 'target')
        b = candidate.model.body('target').id
        np.testing.assert_array_equal(candidate.data.xpos[b], self.sim.data.xpos[self.sim.target_id])
        self.assertTrue(collision_pairs(candidate, [self.placement], config())['valid'])

    def test_forward_occurs_only_after_all_new_poses_are_set(self):
        placements = [copy.deepcopy(self.placement) for _ in range(6)]
        for i, p in enumerate(placements):
            p['pose']['position'][0] += i
        native = mujoco.mj_forward
        calls = []
        def checked_forward(model, data):
            try:
                model.body('procedural_obstacle_0')
            except KeyError:
                return native(model, data)
            for i, p in enumerate(placements):
                b = model.body(f'procedural_obstacle_{i}').id
                q = int(model.jnt_qposadr[model.body_jntadr[b]])
                np.testing.assert_allclose(data.qpos[q:q + 3], p['pose']['position'])
            calls.append(True)
            return native(model, data)
        with patch('lastmile_dataflow.runtime.construction.session.mujoco.mj_forward', side_effect=checked_forward):
            self.session.candidate(placements)
        self.assertEqual(len(calls), 1)

    def test_reject_keeps_exact_baseline_and_new_candidate_data(self):
        old = self.sim.state_vector().copy()
        a = self.session.candidate([self.placement])
        self.session.settle()
        self.session.reject()
        np.testing.assert_array_equal(old, self.sim.state_vector())
        b = self.session.candidate([self.placement])
        self.assertTrue(a.closed)
        self.assertIsNot(a.data, b.data)

    def test_post_begin_candidate_and_rollback_prohibited(self):
        candidate = self.session.candidate([self.placement])
        candidate.begin()
        with self.assertRaises(RuntimeError):
            self.session.reject()
        with self.assertRaises(RuntimeError):
            self.session.candidate([self.placement])

    def test_full_height_collision_catches_chair_body(self):
        p = copy.deepcopy(self.placement)
        p['pose']['position'] = [2., 0., .5]
        candidate = self.session.candidate([p])
        self.assertFalse(collision_pairs(candidate, [p], config())['valid'])

    def test_load_bearing_floor_contact_after_actual_settle(self):
        candidate = self.session.candidate([self.placement])
        self.session.settle()
        self.assertTrue(load_support(candidate, [self.placement], config())['valid'])

    def test_matched_control_same_time_and_source_effects(self):
        candidate = self.session.candidate([self.placement])
        report = self.session.settle()
        control, _ = matched_control(self.sim, report['simulated_s'], self.session.config, None)
        try:
            self.assertAlmostEqual(control.data.time, candidate.data.time)
            self.assertTrue(matched_source_effects(control, candidate, config())['valid'])
            j = candidate.model.body('target').jntadr[0]
            q = int(candidate.model.jnt_qposadr[j])
            candidate.data.qpos[q] += .1
            mujoco.mj_forward(candidate.model, candidate.data)
            self.assertFalse(matched_source_effects(control, candidate, config())['valid'])
        finally:
            control.close()

    def test_wrong_control_time_rejected(self):
        candidate = self.session.candidate([self.placement])
        candidate.data.time = 1.
        with self.assertRaises(ValueError):
            matched_source_effects(self.sim, candidate, config())

    def test_receptacle_site_hidden_without_changing_collision_geom(self):
        candidate = self.session.candidate([self.placement])
        site = candidate.model.site('procedural_obstacle_0/receptacle_debug').id
        self.assertEqual(candidate.model.site_group[site], 5)
        g = candidate.model.geom('procedural_obstacle_0/chair_geom').id
        self.assertEqual(candidate.model.geom_contype[g], 1)
        np.testing.assert_allclose(candidate.model.geom_size[g], [.2, .2, .5])

    def test_three_added_bodies_and_mutual_collision(self):
        placements = [copy.deepcopy(self.placement) for _ in range(3)]
        for i, p in enumerate(placements):
            p['pose']['position'][0] = 4. + i
        candidate = self.session.candidate(placements)
        self.assertTrue(collision_pairs(candidate, placements, config())['valid'])
        self.assertTrue(all(candidate.model.body(f'procedural_obstacle_{i}').id for i in range(3)))
        placements[2]['pose']['position'][0] = 4.
        candidate = self.session.candidate(placements)
        self.assertFalse(collision_pairs(candidate, placements, config())['valid'])

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'opt-in actual OpenGL rendering')
    def test_observer_search_records_actual_segmentation(self):
        candidate = self.session.candidate([self.placement])
        before_graph = build_scene_graph(self.sim)
        after_graph = build_scene_graph(candidate)
        packet = render_obstacle_pair(self.sim, candidate, before_graph, after_graph,
                      {'procedural_obstacle_0'}, 'target', set(), self.root / 'rgb', sample_id='unit',
                      config=ViewConfig(width=320, height=240), min_pixels=24)
        for view in ('top', 'oblique_a', 'oblique_b'):
            counts = packet['visible_pixels_by_instance']
            self.assertGreaterEqual(counts['before/' + view]['target'], 24)
            self.assertGreaterEqual(counts['after/' + view]['target'], 24)
            self.assertGreaterEqual(counts['after/' + view]['procedural_obstacle_0'], 24)
            self.assertEqual(counts['before/' + view]['procedural_obstacle_0'], 0)

    def test_clone_preserves_target_and_prohibits_post_begin(self):
        clone = clone_preparation(self.sim)
        self.assertEqual(clone.target_id, self.sim.target_id)
        clone.close()
        self.sim.begin()
        with self.assertRaises(RuntimeError):
            clone_preparation(self.sim)


if __name__ == '__main__':
    unittest.main()
