"""Run resources, separate from physical execution and calibration matching."""
from dataclasses import dataclass, replace
import os


def calibration_recording(collection, mode):
    visual = mode == 'visual'
    return replace(collection, record_images=visual,
                   record_video=visual and collection.record_video,
                   record_depth=visual and collection.record_depth)


@dataclass(frozen=True)
class RunResources:
    gpu_ids: tuple[int, ...]
    cpu_workers: int = 60
    preparation_workers: int = 12
    recording_mode: str = 'numerical'
    reuse_planners: bool = True
    planning_workers_per_gpu: object = 1

    def __post_init__(self):
        if not self.gpu_ids or len(set(self.gpu_ids)) != len(self.gpu_ids) or any(g < 0 for g in self.gpu_ids):
            raise ValueError('select one or more distinct GPU indices')
        if not 1 <= self.cpu_workers <= max(1, os.cpu_count() or 1):
            raise ValueError(f'cpu_workers must be between 1 and {max(1, os.cpu_count() or 1)}')
        if not 1 <= self.preparation_workers <= min(64, max(1, os.cpu_count() or 1)):
            raise ValueError(f'preparation_workers must be between 1 and {min(64, max(1, os.cpu_count() or 1))}')
        values = (self.planning_workers_per_gpu if isinstance(self.planning_workers_per_gpu, (list, tuple))
                  else [self.planning_workers_per_gpu])
        if len(values) == 1:
            values = list(values) * len(self.gpu_ids)
        if len(values) != len(self.gpu_ids) or any(
                not isinstance(value, int) or not 1 <= value <= 4 for value in values):
            raise ValueError('planning workers per GPU must be one value or one 1-4 value per GPU')
        object.__setattr__(self, 'planning_workers_per_gpu', tuple(values))
        if self.recording_mode not in ('numerical', 'visual'):
            raise ValueError('recording_mode must be numerical or visual')
