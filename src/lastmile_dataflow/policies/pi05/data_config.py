"""RBY-1 LeRobot view in OpenPI's pre-model-transform dictionary format.

Images remain HWC uint8; resizing and π0.5 tokenization belong to the model
transform. Existing base/arm deltas are never differenced a second time.
"""
from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np


@dataclass(frozen=True)
class Rby1DataConfig:
    root: Path
    values: dict

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls(path.parent, json.loads(path.read_text()))

    def create_dataset(self):
        import datasets
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        datasets.config.HF_DATASETS_CACHE = self.root.parent / 'dependencies' / 'datasets-cache'
        source = LeRobotDataset(self.values['repo_id'], root=self.root,
            delta_timestamps={'actions': [i/self.values['fps'] for i in range(self.values['action_horizon'])]})
        stats = json.loads((self.root / self.values['norm_stats']).read_text())['norm_stats']
        return Rby1Dataset(source, self.values, stats)


class Rby1Dataset:
    def __init__(self, source, config, stats):
        self.source, self.config, self.stats = source, config, stats

    def __len__(self):
        return len(self.source)

    def __getitem__(self, index):
        item = self.source[index]
        result = {'image': {}, 'image_mask': {}, 'prompt': self.config['task_prompts'][item['task']]}
        for key in self.config['images']:
            # LeRobot decodes image features as CHW float32 [0,1].
            pixels = np.asarray(item[key]).transpose(1, 2, 0)
            result['image'][key] = np.rint(pixels*255).astype(np.uint8)
            result['image_mask'][key] = np.True_
        for field in ('state', 'actions'):
            values = np.asarray(item[field], dtype=np.float32)
            size = values.shape[-1]
            low = np.asarray(self.stats[field]['q01'][:size], dtype=np.float32)
            high = np.asarray(self.stats[field]['q99'][:size], dtype=np.float32)
            values = (values-low)/(high-low+1e-6)*2-1
            result[field] = np.pad(values, [(0,0)]*(values.ndim-1)+[(0,32-size)])
        result['actions_is_pad'] = np.asarray(item['actions_is_pad'])
        return result
