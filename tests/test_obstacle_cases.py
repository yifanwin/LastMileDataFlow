"""Case policy and dense-station selection, not real-scene acceptance."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from lastmile_dataflow.scenes.obstacle_config import ObstacleConfig, load_obstacle_config
from lastmile_dataflow.scenes.obstacle_sampling import case_assets, substantial_asset, sample_surface_point
from lastmile_dataflow.scenes.obstacle_selection import coverage_select
from lastmile_dataflow.scenes.sampling import sample_stations
from lastmile_dataflow.workflows.obstacle_augmentation import augment_obstacles
from lastmile_dataflow.scenes.obstacle_regions import support_regions
import mujoco
import itertools
from lastmile_dataflow.scenes.geometry import SupportRegion
from lastmile_dataflow.data.obstacle_views import visibility_pass, render_obstacle_pair
from lastmile_dataflow.data.edit_views import ViewConfig


ROOT = Path(__file__).resolve().parents[1]


class CaseTests(unittest.TestCase):
    def test_union_cannot_accept_wall_only_oblique_pairs(self):
        visibility = {}
        for view in ('top', 'oblique_a', 'oblique_b'):
            visibility['before/' + view] = {'target': 100 if view == 'top' else 0}
            visibility['after/' + view] = {'target': 100 if view == 'top' else 0, 'a': 100 if view == 'top' else 0, 'b': 100 if view == 'top' else 0}
        self.assertFalse(visibility_pass(visibility, ['a', 'b'], 'target', 24, True))
        for view in ('oblique_a', 'oblique_b'):
            visibility['before/' + view]['target'] = 100
            visibility['after/' + view]['a'] = 100
        self.assertTrue(visibility_pass(visibility, ['a', 'b'], 'target', 24, True))
        self.assertFalse(visibility_pass(visibility, ['a', 'b'], 'target', 24, False))

    def test_camera_search_includes_pair_without_common_45_degree_view(self):
        calls = []
        def fake_render(before, after, bg, ag, names, folder, **kwargs):
            pair = kwargs['oblique_azimuths_rad']
            calls.append(pair)
            Path(folder).mkdir(parents=True)
            visibility = {}
            for view, visible in zip(('top', 'oblique_a', 'oblique_b'), (True, not np.isclose(pair[0], np.pi / 4), not np.isclose(pair[1], np.pi / 4))):
                visibility['before/' + view] = {'target': 100 if visible else 0}
                visibility['after/' + view] = {'target': 100 if visible else 0, 'a': 100 if visible else 0}
            return {'visible_pixels_by_instance': visibility, 'images': []}
        with tempfile.TemporaryDirectory() as tmp, patch('lastmile_dataflow.data.obstacle_views.render_edit_pair', side_effect=fake_render):
            packet = render_obstacle_pair(None, None, None, None, {'a'}, 'target', set(), Path(tmp) / 'rgb',
                                         sample_id='fixture', config=ViewConfig(), coverage_union=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (0., -np.pi / 4))
        self.assertEqual(packet['visibility_policy'], 'three_view_union_with_each_pair_observed')

    def test_explicit_cases_and_unknown_nested_keys(self):
        for case, count in [('case1', [1, 3]), ('case2', [6, 12])]:
            c = load_obstacle_config(ROOT / f'configs/case_edits/procedural_obstacles_val103_cup30_{case}.json')
            self.assertEqual(c.case_policy.case.lower(), case)
            self.assertEqual(c.obstacle_count, count)
            self.assertEqual(c.case_policy.min_prefilter_points, 5)
            with self.assertRaises(ValueError):
                replace(c, construction={'case': 'Case9'})
            with self.assertRaises(ValueError):
                replace(c, construction={'case': c.case_policy.case, 'typo': 1})

    def test_case1_exact_one_chair_and_surface_remainder(self):
        c = load_obstacle_config(ROOT / 'configs/case_edits/procedural_obstacles_val103_cup30_case1.json')
        geometries = {name: {'asset_id': name, 'category': cat} for name, cat in [('chair', 'Chair'), ('cup', 'Cup')]}
        selected = case_assets(np.random.default_rng(3), 3, {'ground': ['Chair'], 'surface': ['Cup']}, geometries, c.case_policy, c.mandatory_categories)
        self.assertEqual([x['category'] for x in selected], ['Chair', 'Cup', 'Cup'])

    def test_case2_measurements_not_category_alone(self):
        c = load_obstacle_config(ROOT / 'configs/case_edits/procedural_obstacles_val103_cup30_case2.json')
        g = {name: {'asset_id': name, 'category': cat, 'size': dict(width=w, depth=.15, height=h)}
             for name, cat, w, h in [('large', 'Vase', .3, .35), ('small', 'Vase', .1, .12), ('cup', 'Cup', .1, .1)]}
        self.assertTrue(substantial_asset(g['large'], c.case_policy))
        self.assertFalse(substantial_asset(g['small'], c.case_policy))
        selected = case_assets(np.random.default_rng(1), 12, {'ground': [], 'surface': ['Vase', 'Cup']}, g, c.case_policy, [])
        self.assertEqual(len(selected), 12)
        self.assertGreaterEqual(sum(substantial_asset(a, c.case_policy) for a in selected), 2)

    def test_coverage_not_prefix_and_preserves_range_and_bearing(self):
        rows = []
        for radius in (.2, .5, .8):
            for angle in np.linspace(-np.pi, np.pi, 24, endpoint=False):
                rows.append({'station_id': str(len(rows)), 'xy': [radius * np.cos(angle), radius * np.sin(angle)], 'geometry': 'valid'})
        selected, info = coverage_select(rows, [0, 0], 20)
        self.assertEqual(len(selected), 20)
        self.assertNotEqual(info['selected_ids'], [s['station_id'] for s in rows[:20]])
        radius = np.linalg.norm([s['xy'] for s in selected], axis=1)
        self.assertAlmostEqual(radius.min(), .2); self.assertAlmostEqual(radius.max(), .8)
        theta = np.arctan2(*np.asarray([s['xy'] for s in selected]).T[::-1])
        self.assertEqual(len(set(((theta + np.pi) / (2 * np.pi) * 8).astype(int) % 8)), 8)
        self.assertEqual(len(info['discarded']), 52)
        self.assertEqual(selected, coverage_select(rows, [0, 0], 20)[0])

    def test_filtered_points_never_reappear(self):
        rows = [{'station_id': 'bad', 'xy': [0, 0], 'geometry': 'geometry_filtered'},
                {'station_id': 'good', 'xy': [1, 0], 'geometry': 'valid'}]
        self.assertEqual(coverage_select(rows, [0, 0], 20)[0], [rows[1]])

    def test_target_plane_not_leg_tops_or_lower_shelves(self):
        from types import SimpleNamespace
        model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody><body name="table">
        <geom name="top" type="box" pos="0 0 .75" size=".5 .8 .02"/>
        <geom name="leg" type="box" pos=".4 .6 .35" size=".05 .05 .35"/>
        <geom name="shelf" type="box" pos="0 0 .3" size=".4 .7 .02"/>
        </body></worldbody></mujoco>''')
        data = mujoco.MjData(model); mujoco.mj_forward(model, data)
        regions = support_regions(SimpleNamespace(model=model, data=data),
                                  {'anchor_world': [0, 0, .82], 'support_obstacles': [{'body': 'table'}]})
        self.assertEqual([r.geom for r in regions], ['top'])

    def test_surface_sampler_conditions_on_size_and_free_region(self):
        c = load_obstacle_config(ROOT / 'configs/case_edits/procedural_obstacles_val103_cup30_case2.json')
        region = SupportRegion('r', 'table', 'top', 'plane', [0, 0, .75], np.eye(3).tolist(), [-1, 1, -1, 1], .75, 'fixture')
        points = [{'point_id': str(i), 'xy': xy, 'region': region} for i, xy in enumerate([[-.8, 0], [0, 0], [.6, 0]])]
        asset = {'local_points': np.array(list(itertools.product((-.2, .2), (-.2, .2), (0., .4))))}
        occupied = np.array([[[-.4, -.4, .75], [.4, .4, 1.2]]])
        rng = np.random.default_rng(1)
        choices = [sample_surface_point(rng, asset, points, [], occupied, c.case_policy, .003)[0]['point_id'] for _ in range(100)]
        self.assertGreater(choices.count('2'), 95)
        self.assertNotIn('0', choices)  # too close to edge for the complete rotated footprint
        huge = {'local_points': np.array([[-3, -3, 0], [3, 3, .4]])}
        self.assertIsNone(sample_surface_point(rng, huge, points, [], occupied, c.case_policy, .003)[0])

    def test_static_checks_every_candidate_without_renderer_and_fresh_sim(self):
        from types import SimpleNamespace
        clones = []
        def clone(_):
            probe = SimpleNamespace(close=lambda: None, data=SimpleNamespace(warning=[]))
            clones.append(probe)
            return probe
        task = {'anchor_world': [0, 0, 1]}
        config = SimpleNamespace(radius_m=.3, spacing_m=.1)
        with patch('lastmile_dataflow.scenes.sampling.clone_preparation', side_effect=clone), \
             patch('lastmile_dataflow.scenes.sampling.initialize_station', side_effect=lambda sim, station, *a, **kw: {**station, 'geometry': 'valid'}) as init, \
             patch('lastmile_dataflow.scenes.sampling.mujoco.Renderer') as renderer:
            rows = sample_stations(None, task, config, static_only=True)
        self.assertEqual(init.call_count, len(rows))
        self.assertEqual(len(set(id(c) for c in clones)), len(rows))
        renderer.assert_not_called()
        self.assertTrue(all(call.kwargs['check_visibility'] is False for call in init.call_args_list))

    def test_solver_warning_cannot_be_a_valid_initialization(self):
        from types import SimpleNamespace
        def clone(_):
            return SimpleNamespace(close=lambda: None, data=SimpleNamespace(warning=[SimpleNamespace(number=0, lastinfo=0)]))
        def initialize(sim, station, *args, **kwargs):
            sim.data.warning[0].number += 1
            return {**station, 'geometry': 'valid'}
        with patch('lastmile_dataflow.scenes.sampling.clone_preparation', side_effect=clone), \
             patch('lastmile_dataflow.scenes.sampling.initialize_station', side_effect=initialize):
            rows = sample_stations(None, {'anchor_world': [0, 0, 1]}, SimpleNamespace(radius_m=.1, spacing_m=.1), static_only=True)
        self.assertTrue(all(s['geometry'] == 'geometry_filtered' and s['reason'] == 'robot_initialization_solver_warning' for s in rows))



if __name__ == '__main__':
    unittest.main()
