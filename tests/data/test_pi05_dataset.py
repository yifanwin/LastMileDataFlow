"""Synthetic episodes check temporal alignment, normalization and episode tails."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

import imageio.v2 as imageio
import numpy as np

from lastmile_dataflow.data.export.pi05_dataset import CAMERAS, convert
from lastmile_dataflow.policies.pi05.data_config import Rby1DataConfig
from lastmile_dataflow.utils.io import write_json, write_rows, read_json


@unittest.skipUnless(importlib.util.find_spec('lerobot'), 'install pi05-data extra')
class Pi05DatasetTests(unittest.TestCase):
    def test_real_rows_normalized_once_and_chunks_stay_inside_episode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'materialized'
            write_json(source / 'config.json', {'kind':'materialized_lastmile'})
            write_json(source / 'summary.json', {'status':'completed'})
            records = []
            for episode in range(2):
                relative = f'rollouts/d{episode:06d}'
                rollout = source / relative
                write_json(rollout / 'metadata.json', {'cameras':list(CAMERAS.values()), 'profile':{'robot':{
                    'name':'rby1m','control_profile':'position-v1','control_hz':20}}})
                write_json(rollout / 'recording.json', {'task':{'instruction':'synthetic only','category':'Cup','operation':'pick'}})
                np.savez(rollout / 'trajectory.npz', state22=np.full((4,22),episode,dtype=np.float32),
                         actions=np.full((3,20),episode,dtype=np.float32), time_s=np.array([0,.048,.1,.148]))
                for camera in CAMERAS.values():
                    directory = rollout / 'images' / camera
                    directory.mkdir(parents=True)
                    for step in range(3):
                        imageio.imwrite(directory / f'{step:06d}.png',np.full((8,8,3),step,np.uint8))
                records.append({'rollout_path':relative})
            write_rows(source / 'train_index.jsonl',records)
            output = convert(source,root/'lerobot')
            config = Rby1DataConfig.load(output / 'data_config.json')
            stats = read_json(output / 'assets/norm_stats.json')['norm_stats']
            self.assertEqual(config.values['frames'],6)
            np.testing.assert_allclose(stats['state']['mean'][:22],.5)
            dataset = config.create_dataset()
            item = dataset[2]
            self.assertEqual(item['actions'].shape,(50,32))
            np.testing.assert_allclose(item['actions'][:,:20],-1)
            np.testing.assert_array_equal(item['actions'][:,20:],0)
            self.assertTrue(item['actions_is_pad'][1:].all())
            self.assertFalse(item['actions_is_pad'][0])
            self.assertEqual(item['image']['base_0_rgb'].shape,(8,8,3))
            np.testing.assert_array_equal(item['image']['base_0_rgb'],2)
            self.assertEqual(item['prompt'],'Pick up the cup.')
