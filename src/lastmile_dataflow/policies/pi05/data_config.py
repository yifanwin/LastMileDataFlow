"""RBY-1 LeRobot view in OpenPI's pre-model-transform dictionary format.

Images remain HWC uint8; resizing and π0.5 tokenization belong to the model
transform. Existing base/arm deltas are never differenced a second time.
"""
from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np

from .normalization import normalize

PADDED_SIZE = 32


@dataclass(frozen=True)
class Rby1DataConfig:
    """The fields of ``data_config.json`` (written by ``export_pi05``) that training reads."""
    root: Path
    repo_id: str
    fps: int
    action_horizon: int
    norm_stats: str
    task_prompts: dict
    images: dict

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        values = json.loads(path.read_text())
        return cls(path.parent, values['repo_id'], values['fps'], values['action_horizon'],
                   values['norm_stats'], values['task_prompts'], values['images'])

    def create_dataset(self):
        import datasets
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        datasets.config.HF_DATASETS_CACHE = self.root.parent / 'dependencies' / 'datasets-cache'
        source = LeRobotDataset(self.repo_id, root=self.root,
            delta_timestamps={'actions': [i/self.fps for i in range(self.action_horizon)]})
        stats = json.loads((self.root / self.norm_stats).read_text())['norm_stats']
        return Rby1Dataset(source, self, stats)


class Rby1Dataset:
    def __init__(self, source, config, stats):
        self.source, self.config, self.stats = source, config, stats

    def __len__(self):
        return len(self.source)

    def __getitem__(self, index):
        item = self.source[index]
        result = {'image': {}, 'image_mask': {}, 'prompt': self.config.task_prompts[item['task']]}
        for key in self.config.images:
            # LeRobot decodes image features as CHW float32 [0,1].
            pixels = np.asarray(item[key]).transpose(1, 2, 0)
            result['image'][key] = np.rint(pixels*255).astype(np.uint8)
            result['image_mask'][key] = np.True_
        for field in ('state', 'actions'):
            values = normalize(np.asarray(item[field], dtype=np.float32), self.stats[field], 'quantile')
            result[field] = np.pad(values, [(0, 0)]*(values.ndim-1) + [(0, PADDED_SIZE-values.shape[-1])])
        result['actions_is_pad'] = np.asarray(item['actions_is_pad'])
        return result
