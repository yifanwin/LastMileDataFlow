"""合成轨迹与图像验证物化和搬迁读取，不代表真实机器人任务成功。"""
import builtins
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import imageio.v2 as imageio
import numpy as np

from lastmile_dataflow.data.export.lastmile_dataset import (
    load_training_batch, materialize_lastmile, training_record,
)
from lastmile_dataflow.utils.io import create_run, read_json, rows, write_json, write_rows


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'config': {'operation_backend': 'curobo'},
                       'collection': {'record_images': True, 'record_depth': True},
                       'profiles': {'one': {'robot': {'cameras': ['head_camera']}}}}
        self.calibration = create_run(self.root / 'calibration', self.config)
        self.reference = create_run(self.root / 'reference', self.config)
        direct = self.rollout(self.calibration, continuous=False)
        continuous = self.rollout(self.reference, continuous=True)
        direct = training_record(direct, '../calibration', 's1_direct_operation')
        continuous = training_record(continuous, '.', 's0_to_s1_continuous')
        write_rows(self.reference / 'train_index.jsonl', [direct, continuous, direct])
        write_rows(self.reference / 'pair_index.jsonl', [
            {**continuous, 'status': 'success'},
            {'pair_id': 'p1', 'status': 'path_unavailable', 'reason': 'astar_no_path', 'rollout_id': None},
            {'pair_id': 'p2', 'status': 'not_executed', 'reason': 'shared_failure', 'rollout_id': None},
        ])

    def rollout(self, root, *, continuous):
        path = root / 'rollouts' / 'r0'
        path.mkdir(parents=True)
        n = 3
        actions = np.full((n, 20), 2 if continuous else 1, dtype=np.float32)
        np.savez_compressed(path / 'trajectory.npz', actions=actions, state22=np.zeros((n + 1, 22)),
                            time_s=np.arange(n + 1) * .1,
                            phases=np.array(['navigation', 'arrival', 'operation'] if continuous else ['operation'] * n))
        write_json(path / 'recording.json', {'complete': True, 'steps': n, 'task': {'instruction': 'fixture only'}})
        directory = path / 'images' / 'head_camera'
        directory.mkdir(parents=True)
        for step in range(n + 1):
            imageio.imwrite(directory / f'{step:06d}.png', np.full((8, 8, 3), step, dtype=np.uint8))
            np.save(directory / f'{step:06d}-depth.npy', np.ones((8, 8), dtype=np.float32))
        record = {'scene_id': 'one', 'task_id': 'pick', 'station_id': 'high', 'trial': 0, 'seed': 42,
                  'status': 'success', 'task_outcome': {'status': 'success'}, 'recording_complete': True,
                  'executed_steps': n, 'rollout_id': 'r0'}
        if continuous:
            record.update(pair_id='p0', s0='low', s1='high', navigation={'status': 'success', 'steps': 1},
                          navigation_steps=1, euclidean_distance_m=1.42, astar_path_length_m=2.31)
        return record

    def test_copy_deduplicate_and_load_batch_after_relocation_without_sources(self):
        output = materialize_lastmile(self.reference, self.root / 'materialized')
        training = rows(output / 'train_index.jsonl')
        self.assertEqual(len(training), 2)
        self.assertEqual(len(list((output / 'rollouts').iterdir())), 2)
        self.assertNotEqual(training[0]['rollout_path'], training[1]['rollout_path'])
        self.assertNotIn('navigation', training[0])
        self.assertNotIn('s0', training[0])
        self.assertEqual(training[0]['source']['rollout_id'], training[1]['source']['rollout_id'])
        self.assertNotEqual(training[0]['source']['run'], training[1]['source']['run'])
        pairs = rows(output / 'pair_index.jsonl')
        self.assertEqual(len(pairs), 3)
        self.assertEqual(pairs[0]['rollout_path'], training[1]['rollout_path'])
        self.assertEqual(pairs[1]['status'], 'path_unavailable')
        summary = read_json(output / 'summary.json')
        self.assertEqual((summary['input_demonstrations'], summary['unique_source_attempts'], summary['materialized_rollouts']), (3, 2, 2))
        moved = self.root / 'copied_elsewhere'
        shutil.copytree(output, moved)
        source_directories = (self.calibration, self.reference, output)
        original_path_open, original_open = Path.open, builtins.open

        def guard(path):
            if isinstance(path, (str, os.PathLike)):
                resolved = Path(path).resolve()
                if any(resolved.is_relative_to(source) for source in source_directories):
                    raise AssertionError(f'attempted to access source after relocation: {path}')

        def path_open(path, *args, **kwargs):
            guard(path)
            return original_path_open(path, *args, **kwargs)

        def file_open(path, *args, **kwargs):
            guard(path)
            return original_open(path, *args, **kwargs)

        with patch.object(Path, 'open', path_open), patch('builtins.open', file_open):
            batch = load_training_batch(moved, [0, 1], steps=2)
        self.assertEqual(batch['state22'].shape, (2, 2, 22))
        self.assertEqual(batch['actions'].shape, (2, 2, 20))
        self.assertEqual(batch['images']['head_camera'].shape, (2, 2, 8, 8, 3))
        self.assertEqual(batch['depth']['head_camera'].shape, (2, 2, 8, 8))
        np.testing.assert_array_equal(batch['actions'][:, 0, 0], [1, 2])
        self.assertEqual(batch['source_types'], ['s1_direct_operation', 's0_to_s1_continuous'])
        self.assertTrue((self.calibration / 'rollouts/r0/trajectory.npz').exists())
        with self.assertRaises(FileExistsError):
            materialize_lastmile(self.reference, output)

    def test_missing_required_observation_is_incomplete_not_silently_dropped(self):
        # 仅移除本测试临时生成的合成附件，不触碰真实资产。
        (self.calibration / 'rollouts/r0/images/head_camera/000001.png').unlink()
        output = self.root / 'incomplete'
        with self.assertRaises(FileNotFoundError):
            materialize_lastmile(self.reference, output)
        self.assertEqual(read_json(output / 'summary.json')['status'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            load_training_batch(output, [0], steps=1)

    def test_installed_cli_materializes_without_gpu_or_assets_outside_repo(self):
        result = subprocess.run(
            [sys.executable, '-m', 'lastmile_dataflow.cli', 'collect-lastmile',
             '--materialize-from', str(self.reference), '--output-dir', str(self.root),
             '--run-id', 'cli-materialized'], cwd=self.root, check=True, capture_output=True, text=True,
            env={**os.environ, 'CUDA_VISIBLE_DEVICES': ''})
        self.assertIn('GPU=CPU', result.stderr)
        output = Path(result.stdout.strip())
        batch = load_training_batch(output, [0, 1], steps=1)
        self.assertEqual(batch['actions'].shape, (2, 1, 20))
