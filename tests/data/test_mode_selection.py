"""Three raw trials, completeness and per-label evidence-goal priority."""
import copy
import unittest

from lastmile_dataflow.data.evaluation.selection import (
    mode_calibration, mode_station_pools, select_mode_goal, station_calibration,
)
from lastmile_dataflow.runtime.execution_config import ExecutionConfig


class ModeSelectionTests(unittest.TestCase):
    binding = {'scene_id': 'scene', 'task_id': 'pick-target'}

    def sample(self, successes):
        stations = [{'station_id': f's{index}', 'geometry': 'valid'} for index in range(len(successes))]
        trials = [{**self.binding, 'station_id': station['station_id'], 'trial': trial,
                   'rollout_id': f'r{index}-{trial}', 'recording_complete': True,
                   'status': 'success' if trial < successes[index] else 'failure',
                   'task_outcome': {'status': 'success' if trial < successes[index] else 'failure'}}
                  for index, station in enumerate(stations) for trial in range(3)]
        return stations, trials

    def test_three_trial_order_and_complete_plan(self):
        stations, trials = self.sample([1, 0, 2, 3])
        config = ExecutionConfig(trials_per_station=3, mode_config='configs/task_modes/mode_defaults.json')
        calibration = station_calibration(stations, trials, 3, self.binding, config)
        self.assertTrue(calibration['complete'])
        self.assertEqual(calibration['task_eligibility'], 'eligible')
        pools = mode_station_pools(stations, calibration)
        self.assertEqual([r['station_id'] for r in pools['starts']], ['s1', 's0'])
        self.assertEqual([r['station_id'] for r in pools['goals']], ['s3', 's2'])

    def test_missing_or_non_evaluable_trial_never_fills_denominator(self):
        stations, raw = self.sample([0, 3])
        failures = [('status', 'infrastructure_error'), ('status', 'initialization_error'),
                    ('recording_complete', False), ('failure_scope', 'shared'),
                    ('task_outcome', {'status': 'unknown'})]
        for key, value in failures:
            with self.subTest(key=key, value=value):
                trials = copy.deepcopy(raw)
                trials[0][key] = value
                calibration = mode_calibration(stations, trials, self.binding, 'curobo')
                self.assertFalse(calibration['complete'])
                self.assertEqual(calibration['stations'][0]['trials'], 2)
                self.assertEqual(calibration['task_eligibility'], 'incomplete')
                self.assertEqual(mode_station_pools(stations, calibration), {'starts': [], 'goals': []})
        calibration = mode_calibration(stations, raw[:-1], self.binding, 'curobo')
        self.assertEqual(calibration['expected_trials'], 6)
        self.assertEqual(calibration['stations'][1]['missing_trials'], [2])

    def test_unknown_timeout_and_known_no_solution_are_distinct(self):
        stations, trials = self.sample([0, 3])
        trials[0].update(status='timeout', task_outcome={'status': 'unknown'})
        self.assertFalse(mode_calibration(stations, trials, self.binding, 'curobo')['complete'])
        trials[0]['status'] = 'planning_no_solution'
        self.assertTrue(mode_calibration(stations, trials, self.binding, 'curobo')['complete'])
        trials[0].update(status='timeout', task_outcome={'status': 'failure'})
        self.assertTrue(mode_calibration(stations, trials, self.binding, 'curobo')['complete'])

    def test_wrong_binding_duplicate_or_foreign_plan_is_rejected(self):
        stations, trials = self.sample([0, 3])
        for key, value in [('scene_id', 'other'), ('task_id', 'other'), ('station_id', 'other'), ('trial', 3)]:
            with self.subTest(key=key):
                bad = copy.deepcopy(trials)
                bad[0][key] = value
                with self.assertRaises(ValueError):
                    mode_calibration(stations, bad, self.binding, 'curobo')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            mode_calibration(stations, trials + trials[:1], self.binding, 'curobo')

    def test_zero_success_no_high_rate_and_incomplete_have_distinct_eligibility(self):
        for successes, expected in [([0, 0], 'no_success'), ([0, 1], 'no_high_rate_s1'), ([0, 2], 'eligible')]:
            stations, trials = self.sample(successes)
            self.assertEqual(mode_calibration(stations, trials, self.binding, 'curobo')['task_eligibility'], expected)

    def test_nonqualifying_primary_does_not_hide_qualifying_secondary(self):
        goals = [{'station_id': 'bad-primary', 'successes': 3},
                 {'station_id': 'good-secondary', 'successes': 2}]
        chosen = select_mode_goal(goals, lambda goal: {
            'status': 'matched' if goal['station_id'] == 'good-secondary' else 'unmatched'})
        self.assertEqual(chosen['evidence_s1'], 'good-secondary')
        self.assertEqual(chosen['successes'], 2)
        chosen = select_mode_goal(goals, lambda goal: {'status': 'matched'})
        self.assertEqual(chosen['evidence_s1'], 'bad-primary')

    def test_same_tier_uses_path_then_id_and_missing_evidence_is_unresolved(self):
        goals = [{'station_id': name, 'successes': 3} for name in ('c', 'b', 'a')]
        chosen = select_mode_goal(goals, lambda goal: {
            'status': 'matched', 'path_length_m': 2. if goal['station_id'] == 'c' else 1.})
        self.assertEqual(chosen['evidence_s1'], 'a')
        self.assertEqual(chosen['qualifying_s1_ids'], ['a', 'b', 'c'])
        chosen = select_mode_goal(goals, lambda goal: {'status': 'matched', 'path_length_m': None})
        self.assertEqual(chosen['evidence_s1'], 'a')
        self.assertEqual(select_mode_goal(goals, lambda goal: {'status': 'unresolved'})['status'], 'unresolved')
        self.assertEqual(select_mode_goal([], lambda goal: {'status': 'matched'})['status'], 'unmatched')
