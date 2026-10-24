"""Run resources, separate from physical execution and calibration matching."""
from dataclasses import dataclass, replace


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

    def __post_init__(self):
        if not self.gpu_ids or len(set(self.gpu_ids)) != len(self.gpu_ids) or any(g < 0 for g in self.gpu_ids):
            raise ValueError('select one or more distinct GPU indices')
        if not 1 <= self.cpu_workers <= 60:
            raise ValueError('cpu_workers must be between 1 and 60')
        if not 1 <= self.preparation_workers <= 12:
            raise ValueError('preparation_workers must be between 1 and 12')
        if self.recording_mode not in ('numerical', 'visual'):
            raise ValueError('recording_mode must be numerical or visual')
