"""Region geometry, reference counts and actual motion diagnostics."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lastmile_dataflow.data.evaluation.benchmark import reference_calibration, select_starts, summarize
from lastmile_dataflow.data.evaluation.benchmark_config import BenchmarkConfig
from lastmile_dataflow.data.evaluation.recovery import RecoveryRegion, difficulty, motion_diagnostics
from lastmile_dataflow.planning.astar import Grid, search_region


def measured(sid, xy, high, yaw=0.):
    return {'station_id': sid, 'xy': xy, 'yaw': yaw, 'high': high, 'successes': 5 if high else 0, 'trials': 5}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = BenchmarkConfig(region_radius_m=.4)
        self.grid = Grid(np.array([0., 0.]), 1., np.ones((5, 7), dtype=bool))

    def test_region_routes_around_wall_and_serializes(self):
        self.grid.free[1:, 3] = False
        low, high = measured('s0', [1., 2.], False), measured('h', [5., 2.], True)
        region = RecoveryRegion.build(self.grid, [low, high], self.config)
        stations = [{'xy': s['xy'], 'base': [*s['xy'], s['yaw']]} for s in [low, high]]
        result = difficulty(region, stations[0], stations)
        self.assertGreater(result['detour_factor'], 1.)
        self.assertEqual(result['recovery_ratio'], .5)
        self.assertFalse(region.contains(low['xy'], 0.))
        self.assertTrue(region.contains(high['xy'], 0.))
        restored = RecoveryRegion.from_dict(region.to_dict())
        self.assertEqual(restored.goals(), region.goals())
        self.grid.free[0, 3] = False
        blocked = RecoveryRegion.build(self.grid, [low, high], self.config)
        self.assertIsNone(difficulty(blocked, stations[0], stations))

    def test_yaw_only_recovery_has_zero_translation(self):
        low, high = measured('s0', [1., 2.], False), measured('h', [1., 2.], True, np.pi)
        region = RecoveryRegion.build(self.grid, [low, high], self.config)
        result = difficulty(region, {'xy': low['xy'], 'base': [*low['xy'], 0.]}, [])
        self.assertEqual(result['path_length_m'], 0.)
        self.assertIsNone(result['detour_factor'])
        self.assertFalse(region.contains(low['xy'], 0.))
        self.assertTrue(region.contains(low['xy'], np.pi))

    def test_low_measurement_clips_expanded_patch(self):
        low, high = measured('s0', [1., 2.], False), measured('h', [2., 2.], True)
        region = RecoveryRegion.build(self.grid, [low, high], replace(self.config, region_radius_m=3.))
        self.assertFalse(region.contains(low['xy'], 0.))
        self.assertFalse(region.contains([2., 2.], np.pi))

    def test_region_path_includes_exact_endpoint_connectors(self):
        path = search_region(self.grid, [.2, .2], [{'xy': [2.2, .2], 'yaw': 0.}])
        actual = np.linalg.norm(np.diff(np.asarray(path['xy']), axis=0), axis=1).sum()
        self.assertAlmostEqual(path['length_m'], actual)
        self.assertGreater(path['length_m'], 2.)

    def test_round_trip_motion_and_partial_trajectory(self):
        low, high = measured('s0', [0., 0.], False), measured('h', [2., 0.], True)
        region = RecoveryRegion.build(self.grid, [low, high], self.config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trajectory.npz'
            np.savez(path, time_s=[7., 8., 9.], base_xyyaw=[[0., 0., 0.], [2., 0., 0.], [0., 0., 0.]])
            result = motion_diagnostics(path, region, self.config, recording_complete=True)
            self.assertTrue(result['recovery_success'])
            self.assertEqual(result['recovery_first_time_s'], 1.)
            self.assertEqual(result['base_path_length_m'], 4.)
            self.assertEqual(result['final_displacement_m'], 0.)
            np.savez(path, time_s=[0., 1.], base_xyyaw=[[0., 0., 0.], [0., 0., .2]])
            result = motion_diagnostics(path, region, self.config, recording_complete=False)
            self.assertIsNone(result['recovery_success'])
            self.assertTrue(result['effective_adjustment'])

    def test_mobile_reference_counts_follow_original_trial_plan(self):
        stations = [{'station_id': sid, 'geometry': 'valid', 'xy': [i, 0.]} for i, sid in enumerate(['s0', 'h', 'missing'])]
        binding = {'scene_id': 'scene', 'task_id': 'pick'}
        trials = [{**binding, 'station_id': sid, 'trial': i, 'status': status, 'base_displacement_m': .3}
                  for sid, status in [('s0', 'failure'), ('h', 'success')] for i in range(2)]
        trials.append({**binding, 'station_id': 'missing', 'trial': 0, 'status': 'infrastructure_error'})
        cal = reference_calibration(stations, trials, 2, binding, 'curobo')
        self.assertFalse(cal['complete'])
        starts = select_starts(stations, cal, binding)
        self.assertEqual([s['station_id'] for s, _ in starts], ['s0'])
        self.assertEqual(starts[0][1]['trials'], 2)
        self.assertEqual(starts[0][1]['high_station_ids'], ['h'])

    def test_macro_keeps_incomplete_groups(self):
        episodes = [{'episode_id': str(i)} for i in range(4)]
        groups = {str(i): {'case': 'A' if i == 0 else 'B'} for i in range(4)}
        results = [{'episode_id': str(i), 'status': 'success' if i == 0 else 'failure'} for i in range(4)]
        summary = summarize(episodes, results, groups)
        self.assertEqual(summary['task_success_rate'], .25)
        self.assertEqual(summary['macro_by_case'], .5)
        partial = summarize(episodes, results[:1], groups)
        self.assertIsNone(partial['macro_by_case'])
        self.assertIn('B', partial['by_case'])
        self.assertEqual(partial['not_executed'], 3)
