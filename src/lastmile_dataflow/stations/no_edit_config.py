"""Independent, frozen configuration for raw-scene collection (no edit agents)."""
from dataclasses import dataclass, field
import math
from pathlib import Path

from ..config import construct
from ..io import read_json


@dataclass(frozen=True)
class NoEditConfig:
    schema_version: str = 'no-edit-v1'
    planner_backend: str = 'legacy'
    operation_base_mode: str = 'holonomic_joint_planning'
    torso_error_policy: str = 'record_only'
    third_person_enabled: bool = True
    head_fov_enabled: bool = True
    head_fov_margin: float = .9
    head_fov_weight: float = 10000.
    head_fov_min_depth_m: float = .01
    radius_m: float = 2.0
    spacing_m: float | None = None
    trials_per_station: int = 5
    discard_below: float = .05
    start_count: int = 3
    min_successful_rollouts: int = 1
    operation_retries: int = 1
    min_target_pixels: int = 24
    map_resolution_m: float = .05
    smoothing_sigma_m: float | None = None
    smoothing_support_distance_m: float = .35
    navigation_margin_m: float = .02
    navigation_speed_m_s: float = .20
    navigation_yaw_speed_rad_s: float = .4
    arrival_tolerance_m: float = .015
    arrival_tolerance_rad: float = .025
    max_navigation_steps: int = 2400
    max_s1_candidates: int = 0  # 0 = all observed-success stations
    attempt_timeout_s: float = 300.
    scene_timeout_s: float = 86400.
    max_operation_plans: int = 12
    max_open_plans: int = 128
    max_grasp_candidates: int = 4
    num_ik_seeds: int = 32
    num_trajopt_seeds: int = 4
    max_attempts: int = 3
    time_dilation: float = .5
    approach_offset_m: float = .01
    torso_heights: list = field(default_factory=lambda: [0., .369, .738])
    operations: list = field(default_factory=lambda: ['pick', 'open'])
    seed: int = 20261009
    width: int = 320
    height: int = 240
    min_free_disk_gb: float = 50.
    gripper_width_m: float = .10  # RBY1M model jaw travel; measured geometry saved per scene
    open_fraction: float = .3
    open_hinge_rad: float = math.pi / 6
    open_slide_m: float = .10
    open_hold_s: float = 1.
    open_waypoint_step_rad: float = .04
    open_waypoint_step_m: float = .008

    def __post_init__(self):
        if type(self.head_fov_enabled) is not bool:
            raise ValueError('invalid head FOV flag')
        if type(self.head_fov_margin) not in (int, float) or not 0 < self.head_fov_margin < 1 or not math.isfinite(self.head_fov_margin):
            raise ValueError('invalid head FOV margin')
        if type(self.head_fov_weight) not in (int, float) or not math.isfinite(self.head_fov_weight) or self.head_fov_weight <= 0:
            raise ValueError('invalid head FOV weight')
        if self.planner_backend not in ('legacy', 'curobo_v2_v080'):
            raise ValueError('unknown planner backend')
        if self.planner_backend == 'curobo_v2_v080' and self.operation_retries > 1:
            raise ValueError('S1 permits at most one outer retry')
        if self.planner_backend == 'curobo_v2_v080' and self.max_attempts != 5:
            raise ValueError('V2 uses native plan_pose default max_attempts=5')
        if self.schema_version != 'no-edit-v1':
            raise ValueError('unknown no-edit schema')
        if self.operation_base_mode != 'holonomic_joint_planning':
            raise ValueError('no-edit manipulation requires free holonomic base planning')
        if self.torso_error_policy != 'record_only':
            raise ValueError('no-edit v2 torso error policy must be record_only')
        if type(self.third_person_enabled) is not bool:
            raise ValueError('invalid third person flag')
        if self.min_successful_rollouts > self.start_count:
            raise ValueError('minimum exceeds preferred rollout count')
        for key in ('trials_per_station', 'start_count', 'min_successful_rollouts', 'min_target_pixels',
                    'max_navigation_steps', 'max_operation_plans', 'max_open_plans', 'max_grasp_candidates',
                    'num_ik_seeds', 'num_trajopt_seeds', 'max_attempts', 'width', 'height'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('invalid ' + key)
        for key in ('seed', 'operation_retries', 'max_s1_candidates'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError('invalid ' + key)
        for key, value in self.__dict__.items():
            if key.endswith(('_m', '_s', '_rad', '_m_s', '_rad_s', '_gb')) and value is not None:
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError('invalid ' + key)
                minimum_ok = value >= 0 if key in ('approach_offset_m', 'navigation_margin_m') else value > 0
                if not minimum_ok:
                    raise ValueError('invalid ' + key)
        for key in ('discard_below', 'time_dilation', 'open_fraction'):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError('invalid ' + key)
        if not 0 <= self.approach_offset_m <= .02:
            raise ValueError('invalid approach offset')
        if self.width % 2 or self.height % 2:
            raise ValueError('image dimensions must be even')
        if not self.operations or set(self.operations) - {'pick', 'open'}:
            raise ValueError('unsupported operation')
        if not self.torso_heights or any(type(h) not in (int, float) or not math.isfinite(h)
                                        or not 0 <= h <= .738 for h in self.torso_heights):
            raise ValueError('invalid torso heights')


def load_no_edit_config(path):
    return construct(NoEditConfig, read_json(Path(path)))
