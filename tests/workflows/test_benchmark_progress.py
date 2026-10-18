"""Formal calibrated builds and partial evaluation accounting."""
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lastmile_dataflow.data.evaluation.benchmark_config import BenchmarkConfig
from lastmile_dataflow.policies.molmobot_config import MolmoBotConfig
from lastmile_dataflow.runtime.construction.session import clone_preparation
from lastmile_dataflow.utils.io import read_json, rows, create_run, write_rows, write_json
from lastmile_dataflow.workflows import lastmilebench as workflow
from tests.helpers import DatasetTestCase


class BenchmarkProgressTests(DatasetTestCase):
    def make_benchmark(self):
        self.points.insert(1, {'station_id': 's2', 'geometry': 'valid', 'xy': [-.4, 0.],
                              'base': [-.4, 0., 0.], 'initial': {**self.robot.initial, 'base': [-.4, 0., 0.]}})
        dataset = self.dataset()
        self.policy_config = MolmoBotConfig('unused-checkpoint', 'unused-python')
        calibration = create_run(self.root / 'calibration', {
            'dataset': str(dataset), 'config': {'operation_backend': 'curobo', 'trials_per_station': 2,
                'operation_base_mode': 'holonomic_joint_planning', 'radius_m': 3.},
            'profiles': {'one': {'robot': asdict(self.robot)}}})
        write_rows(calibration / 'trials.jsonl', [
            {'scene_id': 'one', 'task_id': self.task['task_id'], 'station_id': station['station_id'],
             'trial': trial, 'status': 'success' if station['station_id'] == 's1' else 'failure', 'reason': 'fixture'}
            for station in self.points for trial in range(2)])
        config_path = self.root / 'benchmark.json'
        write_json(config_path, asdict(BenchmarkConfig(navigation_whole_body=False)))
        with patch.object(workflow, 'native_scene') as convert:
            root = workflow.build(dataset, self.root / 'bench', calibration_dir=calibration, config_path=config_path)
        convert.assert_not_called()
        return root

    def test_build_keeps_all_calibrated_starts_and_private_oracle(self):
        with self.assertLogs(workflow.logger, level='INFO') as logs:
            root = self.make_benchmark()
        episodes = rows(root / 'episodes.jsonl')
        self.assertEqual(len(episodes), 2)
        self.assertTrue(any('已写入episode=2' in record.message for record in logs.records))
        self.assertNotIn('difficulty', episodes[0])
        self.assertNotIn('s1', episodes[0])
        oracle = read_json(root / 'oracle.json')
        self.assertEqual(set(oracle['episodes']), {e['episode_id'] for e in episodes})
        self.assertEqual(read_json(root / 'summary.json')['new_calibration_rollouts'], 0)
        self.assertTrue((root / 'visualization' / 'summary.png').exists())
        self.assertTrue((root / 'visualization' / 'e000000.png').exists())
        self.assertFalse((root / 'visualization' / 'index.html').exists())

    def test_early_exit_keeps_full_plan_coverage_and_closes_policy(self):
        bench = self.make_benchmark()
        original = {p.relative_to(bench): p.read_bytes() for p in bench.rglob('*') if p.is_file()}
        policy = Mock(config=self.policy_config)
        output = self.root / 'evaluation'
        with (
            patch.object(workflow, 'load_policy_config', return_value=self.policy_config),
            patch.object(workflow, 'native_scene', side_effect=clone_preparation),
            patch.object(workflow, 'MolmoBotClient', return_value=policy),
            patch.object(workflow, 'run_vla_attempt', side_effect=[
                {'status': 'success', 'reason': 'fixture', 'executed_steps': 1, 'recording_complete': True},
                OSError('fixture write error')]),
            self.assertRaisesRegex(OSError, 'fixture write error'),
        ):
            workflow.evaluate(bench, output, 'unused-policy.json', gpu_ids=[0], log_dir=self.root / 'logs')
        policy.close.assert_called_once()
        self.assertEqual(len(rows(output / 'results.jsonl')), 1)
        summary = read_json(output / 'summary.json')
        self.assertEqual(summary['coverage'], .5)
        self.assertIsNone(summary['task_success_rate'])
        self.assertIsNone(summary['macro_by_case'])
        self.assertEqual(original, {p.relative_to(bench): p.read_bytes() for p in bench.rglob('*') if p.is_file()})
        self.assertTrue((output / 'visualization' / 'summary.png').exists())

    def test_unsupported_robot_does_not_start_policy_or_fake_rollouts(self):
        bench = self.make_benchmark()
        output = self.root / 'unsupported-evaluation'
        with (
            patch.object(workflow, 'load_policy_config', return_value=self.policy_config),
            patch.object(workflow, 'robot_config', return_value=SimpleNamespace(name='panda_omron')),
            patch.object(workflow, 'MolmoBotClient') as client,
        ):
            workflow.evaluate(bench, output, 'unused-policy.json', gpu_ids=[0], log_dir=self.root / 'logs')
        client.assert_not_called()
        summary = read_json(output / 'summary.json')
        self.assertEqual(summary['not_executed'], 2)
        self.assertEqual(summary['coverage'], 0.)
        self.assertIsNone(summary['task_success_rate'])
        self.assertFalse((output / 'rollouts').exists())
