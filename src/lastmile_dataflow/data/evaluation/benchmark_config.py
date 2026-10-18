"""Parameters for benchmark construction, independent of the reference policy."""
from dataclasses import dataclass
import math

from ...config import read_config


@dataclass(frozen=True)
class BenchmarkConfig:
    low: float = .20
    high: float = .60
    region_radius_m: float = .10
    region_yaw_tolerance_rad: float = math.pi / 12
    map_resolution_m: float = .05
    navigation_margin_m: float = .02
    navigation_whole_body: bool = True
    local_candidate_radius_m: float | None = None
    max_episodes_per_scene: int = 0
    effective_translation_m: float = .02
    effective_yaw_rad: float = math.pi / 90
    seed: int = 42
    horizon: int = 400
    wall_timeout_s: float = 600.

    def __post_init__(self):
        if not 0 <= self.low < self.high <= 1:
            raise ValueError('benchmark requires 0 <= low < high <= 1')
        for key in ('region_radius_m', 'region_yaw_tolerance_rad', 'map_resolution_m',
                    'effective_translation_m', 'effective_yaw_rad', 'wall_timeout_s'):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError('invalid benchmark parameter: ' + key)
        if not 0 < self.region_yaw_tolerance_rad <= math.pi or self.navigation_margin_m < 0:
            raise ValueError('invalid region yaw tolerance or navigation margin')
        if self.local_candidate_radius_m is not None and self.local_candidate_radius_m <= 0:
            raise ValueError('local candidate radius must be positive or null')
        if self.max_episodes_per_scene < 0 or self.seed < 0 or self.horizon <= 0:
            raise ValueError('invalid episode count, seed or horizon')


def load_benchmark_config(path):
    return BenchmarkConfig(**read_config(path))
