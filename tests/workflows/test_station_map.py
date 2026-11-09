"""Base-quality map bookkeeping: headings, grasp subset, budget, best-of-valid and counts."""
import math
import unittest

import numpy as np

from lastmile_dataflow.config import BaseMapConfig
from lastmile_dataflow.data.visualization.quality_map import cell_categories
from lastmile_dataflow.scenes.stations.sampling import heading_fan
from lastmile_dataflow.workflows.station_map import (
    CODE, MAP_KIND, _counts, _initial_values, _candidate_seeds, _write_chunk, WarmTrajectory,
    heading_mean, select_grasps, tcp_local, _neighbours)


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
    def test_history_is_only_a_seed_and_one_source_is_not_copied_to_all_representatives(self):
        bounds = (np.full(7, -2.), np.full(7, 2.))
        history = [WarmTrajectory(-.1, 12, 3, np.array([np.full(7, .2), np.full(7, .8)]))]
        candidates, paths = _candidate_seeds(np.array([np.full(7, .8), np.full(7, -1.)]), np.zeros(7),
                                             16, bounds, BaseMapConfig(), 13, history)
        self.assertEqual([c.seed_kind for c in candidates], ['linear', 'history', 'linear', 'perturbed'])
        self.assertEqual(candidates[1].source_query, 12)
        self.assertTrue(all(c.curve is None for c in candidates))
        np.testing.assert_allclose(paths[:, 0], 0.)
        np.testing.assert_allclose(paths[0, -1], paths[1, -1])
        np.testing.assert_allclose(paths[2, -1], paths[3, -1])

    def test_zero_warm_budget_disables_history_for_ik_and_trajectory_seeds(self):
        from dataclasses import replace
        entry = WarmTrajectory(0., 1, 0, np.zeros((2, 7)))
        self.assertEqual(_neighbours({(0, 0): {0: [entry]}}, (0, 0), 0., 0,
                                     replace(BaseMapConfig(), warm_start_seeds=0)), [])

    def test_warm_starts_come_from_finished_neighbours_inside_the_budget(self):
        config = BaseMapConfig()
        bounds = (np.full(7, -1.), np.full(7, 1.))
        self.assertEqual(_initial_values({}, (0, 0), 0., [0, 1], 2, config, bounds, [1, 2]),
                         (None, {0: 0, 1: 0}))
        near = [np.full(7, .1 * i) for i in range(12)]
        cache = {(0, -1): {0: [WarmTrajectory(.1, 0, i, np.array([q])) for i, q in enumerate(near)]},
                 (5, 5): {1: [WarmTrajectory(0., 1, 0, np.array([near[0]]))]},
                 (1, -1): {0: [WarmTrajectory(math.pi, 2, 0, np.array([near[0]]))]}}
        seeds, warm = _initial_values(cache, (0, 0), 0., [0, 1], 2, config, bounds, [1, 2])
        self.assertEqual(warm, {0: config.warm_start_seeds, 1: 0})  # far cell and opposite yaw ignored
        self.assertEqual(seeds.shape, (2, config.warm_start_seeds, 7))
        self.assertLess(config.warm_start_seeds, config.seeds_per_goal)
        np.testing.assert_allclose(seeds[0], near[:config.warm_start_seeds])
        self.assertTrue(np.all(np.abs(seeds[1]) <= 1.))



class SchedulingTests(unittest.TestCase):
    def test_grasps_share_gpu_batches_and_cpu_jobs_start_before_next_solve(self):
        from types import SimpleNamespace
        from lastmile_dataflow.workflows.station_map import TrajectoryQuery, _submit_trajectories
        from lastmile_dataflow.planning.trajectory_quality import TrajectoryCandidate
        calls, jobs = [], []
        def solve(goals, candidates, seeds):
            calls.append((goals.copy(), len(jobs)))
            for candidate in candidates:
                candidate.submitted = True
            return candidates, 2.
        def submit(function, args):
            jobs.append(args[0])
            return len(jobs)
        planner = SimpleNamespace(batch_size=8, trajectory_candidates=solve)
        pool = SimpleNamespace(apply_async=submit)
        requests = [TrajectoryQuery(k, k, 10+k,
            [TrajectoryCandidate(i, i//2, np.zeros(7), 'linear') for i in range(n)],
            np.zeros((n, 16, 7))) for k, n in enumerate((2, 4, 6))]
        goals = np.tile(np.eye(4), (3, 1, 1))
        goals[:, 0, 3] = [1, 2, 3]
        elapsed = _submit_trajectories(planner, pool, requests, (0, 0, 0), goals,
                                       np.zeros(7), np.ones(7), np.ones(7))
        self.assertEqual(elapsed, [2., 2.])
        np.testing.assert_equal(calls[0][0][:, 0, 3], [1, 1, 2, 2, 2, 2, 3, 3])
        self.assertEqual(calls[1][1], 8)  # CPU validation already queued
        self.assertEqual([len(r.pending) for r in requests], [2, 4, 6])
        self.assertEqual(len(jobs), 12)
        self.assertTrue(all(len(job['candidates']) == 1 for job in jobs))
        self.assertAlmostEqual(sum(r.planning_s for r in requests), sum(elapsed))
        self.assertTrue(all(c.submitted for r in requests for c in r.candidates))


    def test_identical_curves_share_cpu_work_but_keep_all_attempts(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from lastmile_dataflow.workflows.station_map import TrajectoryQuery, _submit_trajectories
        from lastmile_dataflow.planning.trajectory_quality import JointCurve, TrajectoryCandidate
        curve = JointCurve([0., 1.], np.zeros((2, 7)), np.zeros((2, 7)), np.zeros((2, 7)))
        candidates = [TrajectoryCandidate(i, i//2, np.zeros(7), 'linear', curve=curve) for i in range(4)]
        planner = SimpleNamespace(batch_size=8, trajectory_candidates=Mock(return_value=(candidates, 1.)))
        pool = SimpleNamespace(apply_async=Mock())
        request = TrajectoryQuery(0, 0, 5, candidates, np.zeros((4, 16, 7)))
        _submit_trajectories(planner, pool, [request], (0, 0, 0), np.eye(4)[None],
                             np.zeros(7), np.ones(7), np.ones(7))
        self.assertEqual(len(request.candidates), 4)
        self.assertEqual(pool.apply_async.call_count, 1)
        self.assertEqual([reused for _, reused in request.pending], [False, True, True, True])

    def test_worker_reuses_one_yaw_and_discards_scores_when_yaw_changes(self):
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        from lastmile_dataflow.workflows import station_map
        initialize = Mock()
        worker = dict(sim=SimpleNamespace(robot=SimpleNamespace(initialize=initialize)),
                      initial={'base_xyyaw': [0, 0, 0]}, validator=object())
        with patch.object(station_map, '_WORKER', worker):
            station_map._place_worker((1., 2., 0.))
            worker['quality_cache']['test'] = .5
            station_map._place_worker((1., 2., 0.))
            self.assertEqual(initialize.call_count, 1)
            self.assertEqual(worker['quality_cache']['test'], .5)
            station_map._place_worker((1., 2., .1))
            self.assertEqual(initialize.call_count, 2)
            self.assertEqual(worker['quality_cache'], {})


class ScoreTests(unittest.TestCase):
    def test_scoring_point_collision_rejects_candidate_without_query_exception(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from lastmile_dataflow.workflows import station_map
        from lastmile_dataflow.planning.trajectory_quality import JointCurve, TrajectoryCandidate
        from lastmile_dataflow.planning.trajectory_validation import TrajectoryValidation
        curve = JointCurve([0., 1.], np.zeros((2, 7)), np.zeros((2, 7)), np.zeros((2, 7)))
        candidate = TrajectoryCandidate(0, 0, np.zeros(7), 'linear', curve=curve)
        validation = TrajectoryValidation(True, 'valid', approach_time=.5, trace={})
        model = SimpleNamespace(lower=np.full(7, -1.), upper=np.ones(7))
        validator = SimpleNamespace(model=model, set_q=Mock(), validate=Mock(return_value=validation))
        worker = dict(model=model, config=BaseMapConfig(), quality_cache={})
        job = dict(base=(0, 0, 0), goal=np.eye(4), candidates=[candidate], q0=np.zeros(7),
                   velocity_limits=np.ones(7), acceleration_limits=np.ones(7))
        score = SimpleNamespace(samples=[dict(valid=False, time=.7)])
        with patch.object(station_map, '_WORKER', worker), \
             patch.object(station_map, '_place_worker', return_value=(None, validator)), \
             patch.object(station_map, 'tcp_pose', return_value=np.eye(4)), \
             patch('lastmile_dataflow.planning.trajectory_quality.score_trajectory', return_value=score):
            rows, _, _ = station_map._evaluate_trajectories(job)
        self.assertIsNone(rows[0]['quality'])
        self.assertEqual(rows[0]['validation'].reason, 'collision_at_score')
        self.assertFalse(rows[0]['validation'].valid)
        self.assertEqual(rows[0]['validation'].failure_time, .7)

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
    def test_candidate_chunks_preserve_identity_timing_and_all_attempts_without_pickle(self):
        import json
        import tempfile
        from pathlib import Path
        from lastmile_dataflow.planning.trajectory_quality import JointCurve, TrajectoryCandidate, TrajectoryQuality
        from lastmile_dataflow.planning.trajectory_validation import TrajectoryValidation
        curve = JointCurve([0., 1.], np.zeros((2, 7)), np.zeros((2, 7)), np.zeros((2, 7)))
        sample = dict(q=np.zeros(7), tcp=np.eye(4), quality=0., time=0., s=0., u=0., phase='approach')
        quality = TrajectoryQuality(0., None, 0., [sample], [], sample, None, 'no_distinct_configuration')
        rows = [dict(candidate=TrajectoryCandidate(i, i//2, np.zeros(7), 'linear', curve=curve),
                     validation=TrajectoryValidation(True, 'valid'), quality=quality) for i in range(8)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'chunk.npz'
            _write_chunk(path, [dict(query=17, base=(1, 2, -.3), grasp=0, rows=rows,
                                     kept=rows[:4], n_attempted=8, n_completed=8, n_valid=8, n_retained=4)])
            with np.load(path, allow_pickle=False) as data:
                record = json.loads(str(data['summary_json'][0]))
                self.assertEqual(record['n_attempted'], 8)
                self.assertEqual(len(record['candidates']), 8)
                np.testing.assert_equal(data['trajectory_query'], [17]*4)
                np.testing.assert_equal(data['trajectory_offsets'], [0, 2, 4, 6, 8])
                np.testing.assert_equal(data['trajectory_time'][:2], [0., 1.])
                self.assertEqual(record['n_valid']/record['n_attempted'], 1.)
                self.assertIsNone(record['candidates'][0]['quality']['post'])
                self.assertIn('samples', record['candidates'][0]['quality'])
                self.assertNotIn('samples', record['candidates'][-1]['quality'])

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

            # New trajectory diagnostics use count-weighted rates and explicit yaw details.
            with np.load(scene/'map.npz') as z:
                data = {key: z[key] for key in z.files}
            data.update(n_attempted=np.full(status.shape, 8), n_completed=np.full(status.shape, 8),
                n_valid=np.full(status.shape, 6), n_retained=np.full(status.shape, 4),
                r_plan=np.full(status.shape, .75), post_quality=quality,
                cluster_share=np.full(status.shape, 1.), bottleneck_phase=np.full(status.shape, 'transit'))
            np.savez(scene/'map.npz', **data)
            output = plot_station_map(run, Path(d)/'trajectory-figures')
            self.assertTrue((output/'one'/'planning-valid-rate.png').is_file())
            html = (output/'one'/'base-quality.html').read_text()
            self.assertIn('R_plan', html)
            self.assertIn('post_quality', html)


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
