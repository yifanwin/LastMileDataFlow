"""Shared construction/diagnosis budgets and initial, uncalibrated thresholds."""
from dataclasses import dataclass
import math

from ..config import read_config


MODE_NAMES = ('reachability', 'ground_obstacle', 'visibility', 'arm_path_collision',
              'approach', 'narrow_space', 'orientation', 'articulated_interaction')


@dataclass(frozen=True)
class ModeConfig:
    max_proposals: int = 32
    max_physical_candidates: int = 3
    max_repairs: int = 1
    asset_shortlist: int = 5
    variant_timeout_s: float = 1800.
    max_ik_probes_per_s0: int = 1
    probe_grasps: int = 16
    probe_ik_seeds: int = 32
    probe_timeout_s: float = 60.
    ik_feasible_ratio_max: float = .01
    reach_improvement_m: float = .20
    lateral_improvement_m: float = .20
    visibility_low: float = .20
    visibility_good: float = .60
    visibility_gain: float = .30
    clearance_low_m: float = .08
    clearance_gain_m: float = .10
    construction_detour_min: float = 1.20
    orientation_delta_deg: float = 45.
    repair_translation_m: float = .05
    repair_yaw_deg: float = 15.

    def __post_init__(self):
        for key in ('max_proposals', 'max_physical_candidates', 'asset_shortlist', 'probe_grasps', 'probe_ik_seeds'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError('positive mode budget required: ' + key)
        if self.max_repairs not in (0, 1) or self.max_ik_probes_per_s0 not in (0, 1) or self.asset_shortlist > 5:
            raise ValueError('repair/probe budget is at most one; asset shortlist is at most five')
        for key in ('variant_timeout_s', 'probe_timeout_s', 'reach_improvement_m', 'lateral_improvement_m',
                    'clearance_low_m', 'clearance_gain_m', 'orientation_delta_deg', 'repair_translation_m', 'repair_yaw_deg'):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError('positive mode threshold required: ' + key)
        if not 0 <= self.visibility_low < self.visibility_good <= 1 or not 0 < self.visibility_gain <= 1:
            raise ValueError('invalid visibility thresholds')
        if not 0 <= self.ik_feasible_ratio_max <= 1 or not math.isfinite(self.construction_detour_min) or self.construction_detour_min < 1:
            raise ValueError('invalid IK/detour threshold')


def load_mode_config(path):
    return ModeConfig(**read_config(path))
