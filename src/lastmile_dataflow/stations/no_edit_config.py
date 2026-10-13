"""Independent, frozen configuration for raw-scene collection (no edit agents)."""
from dataclasses import dataclass, field
import math
from pathlib import Path

from ..config import construct
from ..io import read_json


@dataclass(frozen=True)
class NoEditConfig:
    schema_version: str = 'no-edit-v1'
    operation_backend: str = 'curobo'
    policy_config: str | None = None
    augmented_variant: str | None = None  # frozen scene + verified coverage station set
    scene_package: str | None = None  # public scene_datasets robot/sensor package; any strategy
    lastmile_selection: bool = False  # opt-in; legacy collection rules stay unchanged
    lastmile_low: float = .2
    lastmile_high: float = .8
    lastmile_nearby_m: float = 1.
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
        if type(self.lastmile_selection) is not bool:
            raise ValueError('invalid lastmile selection flag')
        if any(type(v) not in (int, float) or not math.isfinite(v)
               for v in (self.lastmile_low, self.lastmile_high)) or not 0 <= self.lastmile_low < self.lastmile_high <= 1:
            raise ValueError('invalid lastmile low/high thresholds')
        if self.lastmile_selection and (self.operation_backend != 'curobo' or not self.scene_package):
            raise ValueError('lastmile selection requires a cuRobo scene package')
        if self.lastmile_selection and (self.start_count > 3 or self.max_s1_candidates > 5):
            raise ValueError('LastMile permits at most 3 starts and 5 S1 candidates per start')
        if self.scene_package is not None and (not isinstance(self.scene_package, str) or not self.scene_package):
            raise ValueError('invalid scene_package')
        if self.scene_package and self.augmented_variant:
            raise ValueError('scene_package and augmented_variant are mutually exclusive')
        if self.augmented_variant is not None and (not isinstance(self.augmented_variant, str) or not self.augmented_variant):
            raise ValueError('invalid augmented_variant')
        if self.operation_backend not in ('curobo', 'molmobot'):
            raise ValueError('unknown operation backend')
        if self.operation_backend == 'molmobot' and not self.policy_config:
            raise ValueError('MolmoBot requires a policy config')
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
        base_mode = 'policy_relative' if self.operation_backend == 'molmobot' else 'holonomic_joint_planning'
        if self.operation_base_mode != base_mode:
            raise ValueError('operation base mode does not match backend')
        if self.operation_backend == 'molmobot' and (self.width, self.height) != (1024, 576):
            raise ValueError('MolmoBot evaluation requires 1024x576')
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
    path = Path(path).resolve()
    value = read_json(path)
    if value.get('augmented_variant'):
        value['augmented_variant'] = str((path.parent / value['augmented_variant']).resolve())
    if value.get('scene_package'):
        value['scene_package'] = str((path.parent / value['scene_package']).resolve())
    if value.get('policy_config'):
        value['policy_config'] = str((path.parent / value['policy_config']).resolve())
    return construct(NoEditConfig, value)
