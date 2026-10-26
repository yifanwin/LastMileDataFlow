"""All labels consume the same raw three-trial plan; geometry fixtures are not physics evidence."""
import copy
import unittest

from lastmile_dataflow.data.evaluation.diagnosis import diagnose_task
from lastmile_dataflow.data.evaluation.selection import mode_calibration
from lastmile_dataflow.scenes.mode_config import ModeConfig, MODE_NAMES


def phase(name, status='success', component=None, ik=True, active=True):
    return {'phase': name, 'status': status, 'failure_component': component,
            'ik_found': ik, 'input_eligible': active}


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.scene = {'scene_id': 'one', 'task': {'task_id': 'pick-target', 'operation': 'pick'},
            'proposal_intent': 'ground_obstacle', 'dataset_split': 'train', 'station_hints': ['s0'],
            'stations': [{'station_id': name, 'xy_id': 'same' if name in ('s0', 'primary') else name, 'geometry': 'valid'}
                         for name in ('s0', 'primary', 'secondary', 'visual')]}
        self.trials = [{ 'scene_id': 'one', 'task_id': 'pick-target', 'station_id': name, 'trial': i,
                        'rollout_id': f'{name}-{i}', 'status': 'success' if i < successes else 'failure',
                        'recording_complete': True,
                        'task_outcome': {'status': 'success' if i < successes else 'failure', 'reason': 'physical_open'}}
                       for name, successes in [('s0', 0), ('primary', 3), ('secondary', 2), ('visual', 3)]
                       for i in range(3)]
        self.measurements = {name: {'status': 'measured', 'target_distance_m': distance,
            'base_in_target_frame': [distance, 0., 0.], 'clearance_base_m': clearance,
            'visibility': {'complete': True, 'value': visible, 'known_max': visible},
            'non_base_initial': {'arm_q': [0.]}, 'yaw': yaw, 'initialization_valid': True}
            for name, distance, clearance, visible, yaw in [('s0', 1., .04, .1, 0.),
                ('primary', .9, .3, .9, .8), ('secondary', .5, .3, .9, 0.), ('visual', .9, .3, .1, 0.)]}
        self.planning = {'s0-0': {'attempts': [{'phase_evidence': [phase('goalset', 'failure', 'trajopt')]}]},
                         's0-1': {'attempts': [{'phase_evidence': [phase('goalset'), phase('pregrasp', 'failure', 'ik', ik=False)]}]},
                         's0-2': {'attempts': [{'phase_evidence': [phase('goalset'), phase('pregrasp'), phase('grasp')]}]}}
        self.probes = {'s0': {'status': 'measured', 'feasible_ratio': 0., 'query_ref': 'probe.json'}}
        self.paths = []

    def path(self, start, goal):
        self.paths.append((start['station_id'], goal['station_id']))
        return {'status': 'measured', 'segment_free': False, 'path_exists': True, 'path_length_m': 1.1}

    def run_diagnosis(self):
        binding = {'scene_id': 'one', 'task_id': 'pick-target'}
        calibration = mode_calibration(self.scene['stations'], self.trials, binding, 'curobo')
        return diagnose_task(self.scene, calibration, self.trials, self.measurements,
                             self.planning, self.probes, self.path, ModeConfig())

    def s0(self, result, identity='s0'):
        return next(row for row in result['s0_results'] if row['s0'] == identity)

    def test_all_eight_checked_and_labels_can_overlap(self):
        result = self.run_diagnosis()
        row = self.s0(result)
        self.assertEqual(set(row['labels']), set(MODE_NAMES))
        self.assertEqual(set(row['final_labels']), set(MODE_NAMES) - {'articulated_interaction'})
        self.assertTrue(result['intent_hit'])
        self.assertEqual(row['labels']['articulated_interaction']['status'], 'not_applicable')
        arm = row['labels']['arm_path_collision']['metrics']
        self.assertEqual((arm['supporting_failures'], arm['failed_trials']), (1, 3))

    def test_qualifying_secondary_chosen_for_label_even_with_nonqualifying_primary(self):
        labels = self.s0(self.run_diagnosis())['labels']
        self.assertEqual(labels['reachability']['s1'], 'secondary')
        self.assertEqual(labels['reachability']['s1_successes'], 2)
        self.assertEqual(labels['ground_obstacle']['s1_successes'], 3)

    def test_visibility_only_start_does_not_need_low_operation_rate(self):
        row = self.s0(self.run_diagnosis(), 'visual')
        self.assertEqual(row['successes'], 3)
        self.assertEqual(row['final_labels'], ['visibility'])

    def test_missing_probe_or_visibility_keeps_other_proven_labels(self):
        self.probes = {}
        self.measurements['s0']['visibility'].update(complete=False, value=None, known_max=None)
        labels = self.s0(self.run_diagnosis())['labels']
        self.assertEqual(labels['reachability']['status'], 'unresolved')
        self.assertEqual(labels['visibility']['status'], 'unresolved')
        self.assertEqual(labels['narrow_space']['status'], 'matched')
        self.assertEqual(labels['ground_obstacle']['status'], 'matched')

    def test_articulated_needs_full_open_success_and_no_contact_gate_on_s0(self):
        self.scene['task']['operation'] = 'open'
        for trial in self.trials[:3]:
            trial.update(status='planning_no_solution', task_outcome={'status': 'unknown'})
        row = self.s0(self.run_diagnosis())
        self.assertEqual(row['labels']['articulated_interaction']['status'], 'matched')
        for trial in self.trials:
            if trial['status'] == 'success':
                trial['task_outcome']['reason'] = 'reached_handle_only'
        self.assertEqual(self.s0(self.run_diagnosis())['labels']['articulated_interaction']['status'], 'unresolved')

    def test_late_slip_and_fallback_solve_are_not_approach_failure(self):
        for name in self.planning:
            self.planning[name] = {'attempts': [{'phase_evidence': [phase('goalset'), phase('pregrasp'), phase('grasp')]}]}
        row = self.s0(self.run_diagnosis())
        self.assertEqual(row['labels']['approach']['status'], 'unmatched')
        for name in self.planning:
            self.planning[name] = {'attempts': [{'phase_evidence': [phase('goalset', 'failure', 'ik', ik=False),
                phase('pregrasp', 'failure', 'trajopt', active=False), phase('grasp', active=False)]}]}
        self.assertEqual(self.s0(self.run_diagnosis())['labels']['approach']['status'], 'unmatched')

    def test_no_hidden_navigation_gate_for_other_labels(self):
        def blocked(start, goal):
            return {'status': 'measured', 'segment_free': False, 'path_exists': False, 'path_length_m': None}
        self.path = blocked
        labels = self.s0(self.run_diagnosis())['labels']
        self.assertEqual(labels['ground_obstacle']['status'], 'unmatched')
        self.assertEqual(labels['visibility']['status'], 'unmatched')
        for mode in ('reachability', 'orientation', 'narrow_space', 'arm_path_collision', 'approach'):
            self.assertEqual(labels[mode]['status'], 'matched')

    def test_yaw_changes_with_changed_nonbase_settings_do_not_qualify(self):
        self.measurements['primary']['non_base_initial'] = {'arm_q': [1.]}
        self.assertEqual(self.s0(self.run_diagnosis())['labels']['orientation']['status'], 'unmatched')

    def test_incomplete_and_zero_success_do_not_produce_recovery_labels(self):
        self.trials[0]['status'] = 'infrastructure_error'
        result = self.run_diagnosis()
        self.assertEqual(result['task_eligibility'], 'incomplete')
        self.assertIsNone(result['intent_hit'])
        self.assertEqual(result['s0_results'], [])
        for trial in self.trials:
            trial.update(status='failure', task_outcome={'status': 'failure'})
        result = self.run_diagnosis()
        self.assertEqual(result['task_eligibility'], 'no_success')
        self.assertFalse(result['intent_hit'])
        self.assertEqual(result['s0_results'], [])

    def test_unhinted_discovery_does_not_count_as_intent_hit(self):
        self.scene['proposal_intent'] = 'ground_obstacle'
        self.scene['station_hints'] = ['primary']
        result = self.run_diagnosis()
        self.assertFalse(result['intent_hit'])
        self.assertIn('ground_obstacle', self.s0(result)['final_labels'])
        self.assertFalse(self.s0(result)['is_intended_start'])
        self.assertEqual(self.s0(result)['additional_discovery'], ['ground_obstacle'])
