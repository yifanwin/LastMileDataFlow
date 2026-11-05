"""Base-quality map bookkeeping: headings, grasp subset, budget, best-of-valid and counts."""
import math
import unittest

import numpy as np

from lastmile_dataflow.config import BaseMapConfig
from lastmile_dataflow.data.visualization.quality_map import cell_categories
from lastmile_dataflow.scenes.stations.sampling import heading_fan
from lastmile_dataflow.workflows.station_map import (
    CODE, MAP_KIND, SEED, _counts, _initial_values, best_valid, distinct, heading_mean, select_grasps, tcp_local)


class HeadingTests(unittest.TestCase):
    def test_nine_headings_open_120_degrees_towards_the_target(self):
        offsets = np.deg2rad(BaseMapConfig().heading_offsets_deg)
        self.assertEqual(len(offsets), 9)
        yaws = heading_fan((0., 0.), (1., 0.), offsets)
        np.testing.assert_allclose(np.rad2deg(yaws), np.arange(-60, 61, 15))
        behind = heading_fan((0., 0.), (-1., 0.), offsets)  # facing pi: wraps across +-pi
        self.assertTrue(all(-math.pi <= y <= math.pi for y in behind))
        self.assertAlmostEqual(abs(behind[4]), math.pi)
        self.assertAlmostEqual(math.cos(behind[0] - behind[8]), math.cos(math.radians(120)))
        self.assertIsNone(heading_fan((1., 0.), (1., 0.), offsets))


class GraspTests(unittest.TestCase):
    def test_farthest_point_subset_is_deterministic_and_spread(self):
        poses = []
        for angle in np.linspace(0, 2 * np.pi, 24, endpoint=False):
            pose = np.eye(4)
            pose[:2, 3] = .05 * np.cos(angle), .05 * np.sin(angle)
            poses.append(pose)
        chosen = select_grasps(poses, 4, .05, math.pi / 2)
        self.assertEqual(chosen, select_grasps(poses, 4, .05, math.pi / 2))
        self.assertEqual(chosen[:2], [0, 12])  # opposite side first
        self.assertEqual(sorted(chosen), [0, 6, 12, 18])
        self.assertEqual(len(select_grasps(poses[:3], 5, .05, 1.)), 3)

    def test_tcp_offset_follows_the_approach_axis_and_roll_keeps_it(self):
        pose = tcp_local(np.eye(4), .01, math.pi / 2)
        np.testing.assert_allclose(pose[:3, 3], [0, 0, .01])
        np.testing.assert_allclose(pose[:3, 2], [0, 0, 1], atol=1e-12)
        np.testing.assert_allclose(pose[:3, 0], [0, 1, 0], atol=1e-12)


class BudgetTests(unittest.TestCase):
    def test_warm_starts_come_from_finished_neighbours_inside_the_budget(self):
        config = BaseMapConfig()
        bounds = (np.full(7, -1.), np.full(7, 1.))
        self.assertEqual(_initial_values({}, (0, 0), 0., [0, 1], 2, config, bounds, [1, 2]),
                         (None, {0: 0, 1: 0}))
        near = [np.full(7, .1 * i) for i in range(12)]
        cache = {(0, -1): {0: [(0.1, near)]}, (5, 5): {1: [(0., near)]}, (1, -1): {0: [(math.pi, near)]}}
        seeds, warm = _initial_values(cache, (0, 0), 0., [0, 1], 2, config, bounds, [1, 2])
        self.assertEqual(warm, {0: config.warm_start_seeds, 1: 0})  # far cell and opposite yaw ignored
        self.assertEqual(seeds.shape, (2, config.warm_start_seeds, 7))
        self.assertLess(config.warm_start_seeds, config.seeds_per_goal)
        np.testing.assert_allclose(seeds[0], near[:config.warm_start_seeds])
        self.assertTrue(np.all(np.abs(seeds[1]) <= 1.))

    def test_distinct_counts_clusters_without_dropping_the_first(self):
        q = [np.zeros(7), np.full(7, 5e-4), np.ones(7)]
        self.assertEqual(len(distinct(q, 1e-3)), 2)


class ScoreTests(unittest.TestCase):
    def test_best_of_valid_seeds_only(self):
        reasons = np.array([SEED['valid'], SEED['touching_contact'], SEED['valid'], SEED['solver_failed']])
        quality = np.array([.2, np.nan, .5, np.nan])
        self.assertEqual(best_valid(reasons, quality), 2)
        self.assertEqual(best_valid(reasons, np.array([.2, 9., .1, 9.])), 0)
        self.assertIsNone(best_valid(np.full(4, SEED['mujoco_contact']), quality))

    def test_filtered_queries_stay_out_of_the_ik_denominator(self):
        status = np.full((1, 1, 2, 2), CODE['distance_filtered'], np.int8)
        status[0, 0, 0, 0] = CODE['valid']
        status[0, 0, 1, 0] = CODE['ik_no_solution']
        executed = np.zeros(status.shape, np.int16)
        executed[0, 0, :, 0] = 32
        valid = np.zeros(status.shape, np.int16)
        valid[0, 0, 0, 0] = 8
        counts = _counts(status, valid, valid, executed, np.ones(status.shape, bool))
        self.assertEqual(counts['executed_seeds'], 64)
        self.assertAlmostEqual(counts['ik_valid_rate'], 8 / 64)
        self.assertEqual(counts['query_status']['distance_filtered'], 2)

    def test_figures_keep_unevaluated_filtered_failed_and_zero_quality_apart(self):
        status = np.array([[[[0], [0]], [[4], [3]], [[5], [4]], [[6], [5]]]], np.int8)  # (1, 4, 2, 1)
        np.testing.assert_array_equal(cell_categories(status[..., 0]), [[0, 1, 2, 3]])


class AggregationTests(unittest.TestCase):
    def test_position_quality_is_the_mean_of_valid_headings_only(self):
        status = np.array([[[[CODE['valid']], [CODE['valid']], [CODE['ik_no_solution']]],
                            [[CODE['distance_filtered']]] * 3]], np.int8)  # (1, 2, 3, 1)
        quality = np.array([[[[.2], [.4], [np.nan]], [[np.nan]] * 3]])
        mean, count = heading_mean(status, quality)
        self.assertAlmostEqual(mean[0, 0, 0], .3)
        self.assertEqual(count[0, 0, 0], 2)
        self.assertTrue(np.isnan(mean[0, 1, 0]))

    def test_plotting_reads_saved_arrays_only(self):
        import tempfile
        from pathlib import Path
        from lastmile_dataflow.utils.io import write_json
        from lastmile_dataflow.workflows.station_map_plot import plot_station_map
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / 'run'
            scene = run / 'one'
            scene.mkdir(parents=True)
            write_json(run / 'config.json', {'kind': MAP_KIND, 'scenes': ['one']})
            status = np.full((2, 2, 3, 1), CODE['valid'], np.int8)
            status[0, 0] = CODE['base_collision']
            quality = np.where(status == CODE['valid'], .05, np.nan)
            yaw = np.zeros((2, 2, 3))
            np.savez(scene / 'map.npz', x=np.array([0., .1]), y=np.array([0., .1]), heading_offsets_rad=np.zeros(3),
                     yaw=yaw, status=status, status_names=np.array(['x'] * 7), quality=quality,
                     position_quality=np.full((2, 2, 1), .9), valid_seeds=np.ones(status.shape, int) * 4,
                     executed_seeds=np.ones(status.shape, int) * 32, distinct_valid=np.ones(status.shape, int),
                     warm_start_seeds=np.zeros(status.shape, int), best_q=np.zeros((*status.shape, 7)),
                     background_free=np.ones((20, 20), bool), background_origin=np.array([-.2, -.2]),
                     background_resolution_m=.02)
            write_json(scene / 'scene.json', {'scene_id': 'one', 'asset_id': 'Cup', 'object_center_world': [.3, .05, .8],
                       'grid': {'spacing_m': .1}, 'grasp_candidates': 1,
                       'grasps': [{'source_row': 0, 'tcp_world': np.eye(4).tolist()}]})
            write_json(scene / 'summary.json', {'quality_range': [.05, .05]})
            output = plot_station_map(run, Path(d) / 'figures')
            names = {p.name for p in (output / 'one').iterdir()}
            self.assertTrue({'base-quality-g0.png', 'heading-quality-g0.png', 'base-quality.html'} <= names)
            self.assertNotIn('heading-quality-g0-slices.png', names)


class DownstreamTests(unittest.TestCase):
    def test_collection_and_benchmark_refuse_a_quality_map_as_calibration(self):
        import tempfile
        from pathlib import Path
        from lastmile_dataflow.utils.io import write_json
        from lastmile_dataflow.workflows.station_map import MAP_KIND, reject_quality_map
        with self.assertRaisesRegex(ValueError, 'not a physical calibration'):
            reject_quality_map({'kind': MAP_KIND, 'dataset': None})
        reject_quality_map({'dataset': 'scenes', 'config': {}})
        from lastmile_dataflow.workflows.lastmilebench import build
        with tempfile.TemporaryDirectory() as d:
            write_json(Path(d) / 'config.json', {'kind': MAP_KIND, 'dataset': None})
            with self.assertRaisesRegex(ValueError, 'not a physical calibration'):
                build(d, Path(d) / 'bench', calibration_dir=d, config_path='configs/benchmark/v0.1.json', gpu_ids=[0])


if __name__ == '__main__':
    unittest.main()
