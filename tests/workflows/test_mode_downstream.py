"""Train/val consumption of saved labels; simplified scenes are software fixtures."""
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from lastmile_dataflow.data.evaluation.benchmark_config import BenchmarkConfig
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.mode_config import ModeConfig
from lastmile_dataflow.utils.io import create_run, write_json, write_rows, read_json, rows
from lastmile_dataflow.workflows.collection import _plan_collection
from lastmile_dataflow.workflows import lastmilebench
from tests.helpers import DatasetTestCase


class ModeDownstreamWorkflowTests(DatasetTestCase):
    def setUp(self):
        super().setUp()
        initial = self.robot.initial
        self.points[:] = [{'station_id': name, 'xy_id': name, 'geometry': 'valid', 'xy': [x, 0.],
                           'base': [x, 0., 0.], 'initial': {**initial, 'base': [x, 0., 0.]}}
                          for name, x in [('low', 0.), ('visual', -.4), ('primary', .4), ('secondary', .8)]]
        self.scene.update(proposal_intent='reachability', dataset_split='val', station_hints=['low'])
        self.config = ExecutionConfig(trials_per_station=3, mode_config='configs/task_modes/mode_defaults.json',
                                      navigation_whole_body=False)
        self.raw = [{'scene_id': 'one', 'task_id': 'pick-target', 'station_id': name, 'trial': trial,
                     'seed': 42 + trial, 'rollout_id': f'{name}-{trial}', 'recording_complete': True,
                     'observation_complete': True, 'executed_steps': 1,
                     'status': 'success' if trial < successes else 'failure',
                     'task_outcome': {'status': 'success' if trial < successes else 'failure'}}
                    for name, successes in [('low', 0), ('visual', 3), ('primary', 3), ('secondary', 2)]
                    for trial in range(3)]
        def evidence(label, successes, goals):
            return {'status': 'matched', 's1_successes': successes, 'qualifying_s1_ids': goals}
        self.diagnosis = {'scene_id': 'one', 'task_id': 'pick-target', 'proposal_intent': 'reachability',
            'dataset_split': 'val', 'task_eligibility': 'eligible', 'calibration_complete': True,
            'station_measurements': {s['station_id']: {'status': 'measured', 'base': s['base'], 'yaw': 0.} for s in self.points},
            's0_results': [
                {'s0': 'low', 'successes': 0, 'final_labels': ['reachability', 'narrow_space'],
                 'labels': {'reachability': evidence('reachability', 2, ['secondary']),
                            'narrow_space': evidence('narrow_space', 2, ['secondary'])}},
                {'s0': 'visual', 'successes': 3, 'final_labels': ['visibility'],
                 'labels': {'visibility': evidence('visibility', 3, ['primary'])}}]}

    def test_train_pair_union_and_direct_demonstrations_reuse_the_raw_trials(self):
        self.scene['dataset_split'] = self.diagnosis['dataset_split'] = 'train'
        root = create_run(self.root / 'collection', {})
        pair = _plan_collection([self.scene], self.raw, self.config, root, '../calibration',
                                diagnoses={'one': self.diagnosis})
        self.assertEqual(len(pair), 2)
        by_start = {r['s0']: r for r in pair}
        self.assertEqual(by_start['low']['labels'], ['reachability', 'narrow_space'])
        self.assertEqual(by_start['low']['s1_success_rate'], 2/3)
        self.assertEqual(by_start['visual']['s0_success_rate'], 1.)
        self.assertEqual(len(rows(root/'train_index.jsonl')), 8)
        self.assertFalse((root/'trials.jsonl').exists())

    def test_val_scene_never_enters_train_pairs_or_demonstrations(self):
        root = create_run(self.root / 'val-filter', {})
        self.assertEqual(_plan_collection([self.scene], self.raw, self.config, root, '.', diagnoses={'one': self.diagnosis}), [])
        self.assertEqual(rows(root/'train_index.jsonl'), [])

    def test_collection_scene_summary_reports_saved_mode_pair_count(self):
        from lastmile_dataflow.workflows.collection import _collect_scene
        self.scene['dataset_split'] = self.diagnosis['dataset_split'] = 'train'
        root = create_run(self.root/'scene-counts', {})
        pairs = _plan_collection([self.scene], self.raw, self.config, root, '../calibration',
                                 diagnoses={'one': self.diagnosis})
        with patch('lastmile_dataflow.workflows.collection._recovery_trials', return_value=[]):
            summary = _collect_scene(self.sim, self.scene, 'assets', self.config, self.collection,
                root, None, self.raw, None, attempt_ids=iter(()), pairs=pairs)
        self.assertEqual(summary['eligible_pairs'], 2)

    def test_missing_observations_do_not_turn_numeric_success_into_training_data(self):
        self.scene['dataset_split'] = self.diagnosis['dataset_split'] = 'train'
        for trial in self.raw:
            trial['observation_complete'] = False
        root = create_run(self.root / 'numeric', {})
        _plan_collection([self.scene], self.raw, self.config, root, '../calibration', diagnoses={'one': self.diagnosis})
        self.assertEqual(rows(root/'train_index.jsonl'), [])
        self.assertEqual(len(rows(root/'pair_index.jsonl')), 2)

    def test_numeric_success_uses_readonly_state_replay_before_entering_train_index(self):
        self.scene['dataset_split'] = self.diagnosis['dataset_split'] = 'train'
        calibration = create_run(self.root/'calibration', {
            'profiles': {'one': {'robot': {'cameras': list(self.robot.cameras)}}},
            'collection': {'record_depth': False}})
        root = create_run(self.root/'collection-replay', {})
        for trial in self.raw:
            trial.update(observation_complete=False, replay_complete=True)
        with patch('lastmile_dataflow.data.rendering.replay.render_observations', return_value={'complete': True}) as render:
            _plan_collection([self.scene], self.raw, self.config, root, '../calibration', diagnoses={'one': self.diagnosis})
        self.assertEqual(render.call_count, 8)
        training = rows(root/'train_index.jsonl')
        self.assertEqual(len(training), 8)
        self.assertTrue(all(row['observation_source'] == 'measured_state_replay' for row in training))
        self.assertTrue(all(not row['observation_complete'] for row in self.raw))
        self.assertEqual(len(rows(root/'pair_index.jsonl')), 2)

    def test_numeric_continuous_success_replays_observations_once_per_deduplicated_pair(self):
        from itertools import count
        from lastmile_dataflow.workflows.collection import _recovery_trials
        self.scene['dataset_split'] = self.diagnosis['dataset_split'] = 'train'
        root = create_run(self.root/'continuous-replay', {
            'profiles': {'one': {'robot': {'cameras': list(self.robot.cameras)}}},
            'collection': {'record_depth': False}})
        pairs = _plan_collection([self.scene], self.raw, self.config, root, '../calibration', diagnoses={'one': self.diagnosis})
        outcomes = [{'status': 'success', 'task_outcome': {'status': 'success'}, 'recording_complete': True,
                     'observation_complete': False, 'replay_complete': True, 'executed_steps': 1,
                     'navigation': {'status': 'success', 'steps': 1}, 'rollout_id': f'continuous-{index}'} for index in range(2)]
        with (patch('lastmile_dataflow.workflows.collection._execute', side_effect=outcomes) as execute,
              patch('lastmile_dataflow.data.rendering.replay.render_observations', return_value={'complete': True}) as render):
            _recovery_trials(self.sim, self.scene, pairs, 'assets', self.config, self.collection, root, attempt_ids=count())
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(render.call_count, 2)
        continuous = [row for row in rows(root/'train_index.jsonl') if row['source_type'] == 's0_to_s1_continuous']
        self.assertEqual(len(continuous), 2)
        self.assertTrue(all(row['observation_source'] == 'measured_state_replay' for row in continuous))
        self.assertEqual(set(continuous[0]['labels']), {'reachability', 'narrow_space'})

    def test_mode_summary_counts_one_third_starts_and_two_thirds_goals(self):
        dataset = self.dataset()
        self.raw[0].update(status='success', task_outcome={'status': 'success'})
        calibration = create_run(self.root/'calibration-counts', {'dataset': str(dataset),
            'config': asdict(self.config), 'profiles': {'one': {'robot': asdict(self.robot)}},
            'mode_settings': asdict(ModeConfig())})
        write_rows(calibration/'trials.jsonl', self.raw)
        self.diagnosis.update(trials_ref=str(calibration/'trials.jsonl'), s0_results=[])
        write_rows(calibration/'diagnosis.jsonl', [self.diagnosis])
        settings = self.root/'generic-thresholds.json'
        write_json(settings, asdict(BenchmarkConfig(low=0., high=1.)))
        root = lastmilebench.build(dataset, self.root/'bench-counts', calibration_dir=calibration, config_path=settings)
        report = read_json(root/'summary.json')['scenes'][0]
        self.assertEqual(report['low_stations'], 1)
        self.assertEqual(report['high_stations'], 3)

    def test_mode_bench_uses_per_episode_qualified_anchors_and_private_labels(self):
        dataset = self.dataset()
        calibration = create_run(self.root/'calibration', {'dataset': str(dataset), 'config': asdict(self.config),
            'profiles': {'one': {'robot': asdict(self.robot)}}, 'mode_settings': asdict(ModeConfig())})
        write_rows(calibration/'trials.jsonl', self.raw)
        self.diagnosis['trials_ref'] = str(calibration/'trials.jsonl')
        write_rows(calibration/'diagnosis.jsonl', [self.diagnosis])
        settings = self.root/'benchmark.json'
        write_json(settings, asdict(BenchmarkConfig(navigation_whole_body=False)))
        before = (calibration/'trials.jsonl').read_bytes()
        root = lastmilebench.build(dataset, self.root/'bench', calibration_dir=calibration, config_path=settings)
        episodes = rows(root/'episodes.jsonl')
        oracle = read_json(root/'oracle.json')
        self.assertEqual(len(episodes), 2)
        for episode in episodes:
            self.assertNotIn('labels', episode)
            self.assertNotIn('s1', episode)
            evidence = oracle['episodes'][episode['episode_id']]
            region = oracle['regions'][evidence['region_id']]
            anchors = [patch['anchor']['station_id'] for patch in region['patches']]
            if episode['station']['station_id'] == 'low':
                self.assertEqual(anchors, ['secondary'])  # An unrelated 3/3 must not replace this 2/3.
                self.assertEqual(evidence['labels'], ['reachability', 'narrow_space'])
            else:
                self.assertEqual(anchors, ['primary'])
                self.assertEqual(evidence['start_success_rate'], 1.)
                self.assertEqual(evidence['labels'], ['visibility'])
            self.assertNotIn('case', evidence)
        self.assertEqual((calibration/'trials.jsonl').read_bytes(), before)
        self.assertEqual(read_json(root/'summary.json')['new_calibration_rollouts'], 0)
        self.assertTrue((root/'visualization/e000000.png').is_file())
