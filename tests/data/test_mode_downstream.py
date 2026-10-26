"""One saved classification drives deduplicated pairs and overlapping benchmark groups."""
import copy
from pathlib import Path
import tempfile
import unittest

from lastmile_dataflow.data.evaluation.diagnosis import diagnosed_pairs, diagnosed_starts, load_diagnoses
from lastmile_dataflow.data.evaluation.benchmark import summarize
from lastmile_dataflow.utils.io import write_json, write_rows


def candidate(split='train'):
    return {'scene_id': 'one', 'task': {'task_id': 'pick-target'}, 'dataset_split': split,
            'proposal_intent': 'reachability',
            'stations': [{'station_id': name} for name in ('s0', 'primary', 'secondary')]}


def diagnosis(split='train'):
    return {'scene_id': 'one', 'task_id': 'pick-target', 'dataset_split': split,
        'proposal_intent': 'reachability', 'calibration_complete': True, 'task_eligibility': 'eligible',
        's0_results': [{'s0': 's0', 'successes': 0,
            'final_labels': ['reachability', 'visibility', 'narrow_space'],
            'labels': {label: {'status': 'matched', 's1_successes': tier, 'qualifying_s1_ids': goals}
                       for label, tier, goals in [('reachability', 2, ['secondary']),
                           ('visibility', 3, ['primary']), ('narrow_space', 3, ['primary'])]}}]}


class ModeDownstreamTests(unittest.TestCase):
    def test_pair_union_keeps_each_labels_selected_tier_without_duplicate_execution(self):
        record = diagnosis()
        original = copy.deepcopy(record)
        pairs = diagnosed_pairs(candidate(), record)
        self.assertEqual(len(pairs), 2)
        by_goal = {row['s1']: row for row in pairs}
        self.assertEqual(by_goal['primary']['labels'], ['visibility', 'narrow_space'])
        self.assertEqual(by_goal['secondary']['s1_tiers'], {'reachability': 2})
        self.assertEqual(by_goal['secondary']['s1_success_rate'], 2/3)
        self.assertEqual(record, original)
        starts = diagnosed_starts(candidate(), record)
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0][1]['high_station_ids'], ['primary', 'secondary'])
        self.assertEqual(starts[0][1]['s1_by_label']['reachability'], ['secondary'])

    def test_visibility_only_start_can_have_three_of_three_operation_success(self):
        record = diagnosis()
        row = record['s0_results'][0]
        row.update(successes=3, final_labels=['visibility'])
        pair = diagnosed_pairs(candidate(), record)[0]
        self.assertEqual(pair['s0_success_rate'], 1.)
        self.assertEqual(diagnosed_starts(candidate(), record)[0][1]['labels'], ['visibility'])

    def test_zero_of_three_pairs_precede_one_of_three_even_when_ids_sort_later(self):
        scene, record = candidate(), diagnosis()
        scene['stations'].append({'station_id': 'a-one-third'})
        fallback = copy.deepcopy(record['s0_results'][0])
        fallback.update(s0='a-one-third', successes=1)
        record['s0_results'].insert(0, fallback)
        self.assertEqual([row['s0'] for row in diagnosed_pairs(scene, record)],
                         ['s0', 's0', 'a-one-third', 'a-one-third'])

    def test_incomplete_or_unqualified_task_has_no_recovery_pairs(self):
        for field, value in [('calibration_complete', False), ('task_eligibility', 'no_success'),
                             ('task_eligibility', 'no_high_rate_s1')]:
            record = diagnosis()
            record[field] = value
            self.assertEqual(diagnosed_pairs(candidate(), record), [])

    def test_loading_saved_diagnosis_checks_thresholds_split_and_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root/'config.json', {'mode_settings': {'visibility_low': .2}})
            write_rows(root/'diagnosis.jsonl', [diagnosis()])
            observed = load_diagnoses(root, [candidate()], {'visibility_low': .2})
            self.assertEqual(observed['one'], diagnosis())
            with self.assertRaisesRegex(ValueError, 'threshold'):
                load_diagnoses(root, [candidate()], {'visibility_low': .3})
            with self.assertRaisesRegex(ValueError, 'binding'):
                load_diagnoses(root, [candidate('val')], {'visibility_low': .2})


class MultiLabelSummaryTests(unittest.TestCase):
    def setUp(self):
        self.episodes = [{'episode_id': 'e0'}, {'episode_id': 'e1'}]
        self.groups = {'e0': {'labels': ['ground_obstacle', 'visibility'], 'task': 'pick'},
                       'e1': {'labels': ['visibility'], 'task': 'pick'}}
        self.results = [{'episode_id': 'e0', 'status': 'success', 'recording_complete': True,
                         'task_outcome': {'status': 'success'}},
                        {'episode_id': 'e1', 'status': 'failure', 'recording_complete': True,
                         'task_outcome': {'status': 'failure'}}]

    def test_overlapping_groups_do_not_expand_overall_denominator(self):
        result = summarize(self.episodes, self.results, self.groups)
        self.assertEqual(result['scheduled'], 2)
        self.assertEqual(result['task_success_rate'], .5)
        self.assertEqual(result['by_label']['ground_obstacle']['scheduled'], 1)
        self.assertEqual(result['by_label']['visibility']['scheduled'], 2)
        self.assertEqual(result['macro_by_label'], .75)
        self.assertEqual(result['primary_macro'], 'macro_by_label')
        self.assertNotIn('by_case', result)

    def test_missing_episode_suppresses_affected_official_rates_and_macro(self):
        result = summarize(self.episodes, self.results[:1], self.groups)
        self.assertEqual(result['coverage'], .5)
        self.assertIsNone(result['task_success_rate'])
        self.assertEqual(result['by_label']['ground_obstacle']['task_success_rate'], 1.)
        self.assertIsNone(result['by_label']['visibility']['task_success_rate'])
        self.assertIsNone(result['macro_by_label'])

    def test_unknown_timeout_or_incomplete_recording_is_not_task_failure(self):
        for updates in ({'status': 'timeout', 'task_outcome': {'status': 'unknown'}},
                        {'recording_complete': False}):
            records = copy.deepcopy(self.results)
            records[1].update(updates)
            result = summarize(self.episodes, records, self.groups)
            self.assertEqual(result['failures'], 0)
            self.assertEqual(result['unknown_or_infra'], 1)
            self.assertIsNone(result['task_success_rate'])
