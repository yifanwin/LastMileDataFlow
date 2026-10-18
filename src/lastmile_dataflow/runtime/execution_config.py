"""Execution parameters and the shared robot/camera/backend profile."""
from dataclasses import asdict, dataclass, field
import math

from ..config import read_config
from ..policies.config import load_policy_config


@dataclass(frozen=True)
class ExecutionConfig:
    operation_backend: str = 'curobo'
    policy_config: str | None = None
    lastmile_low: float = .2
    lastmile_high: float = .8
    lastmile_nearby_m: float = 1.
    planner_backend: str = 'curobo_v2_v080'
    operation_base_mode: str = 'holonomic_joint_planning'
    torso_error_policy: str = 'record_only'
    initial_settle_s: float = 0.
    grasp_roll_rad: float = 0.
    grasp_candidate_order: str = "facing"
    local_base_radius_m: float = .35
    third_person_enabled: bool = True
    head_fov_enabled: bool = True
    head_fov_margin: float = .9
    head_fov_weight: float = 10000.
    head_fov_min_depth_m: float = .01
    radius_m: float = 2.0
    spacing_m: float | None = None
    trials_per_station: int = 5
    start_count: int = 3
    operation_retries: int = 1
    min_target_pixels: int = 24
    map_resolution_m: float = .05
    smoothing_sigma_m: float | None = None
    smoothing_support_distance_m: float = .35
    navigation_margin_m: float = .02
    navigation_whole_body: bool = False
    navigation_speed_m_s: float = .20
    navigation_yaw_speed_rad_s: float = .4
    arrival_tolerance_m: float = .015
    arrival_tolerance_rad: float = .025
    max_navigation_steps: int = 2400
    max_s1_candidates: int = 0  # 不参与 lastmile-datasets 全组合配对
    attempt_timeout_s: float = 300.
    max_operation_plans: int = 12
    max_open_plans: int = 128
    max_grasp_candidates: int = 4
    num_ik_seeds: int = 32
    num_trajopt_seeds: int = 4
    max_attempts: int = 5
    time_dilation: float = .5
    approach_offset_m: float = .01
    torso_heights: list = field(default_factory=lambda: [0., .369, .738])
    operations: list = field(default_factory=lambda: ['pick', 'open'])
    seed: int = 42
    width: int = 320
    height: int = 240
    gripper_width_m: float = .10  # RBY1M model jaw travel; measured geometry saved per scene
    open_fraction: float = .3
    open_hinge_rad: float = math.pi / 6
    open_slide_m: float = .10
    open_hold_s: float = 1.
    open_waypoint_step_rad: float = .04
    open_waypoint_step_m: float = .008

    def __post_init__(self):
        if not 0 <= self.lastmile_low < self.lastmile_high <= 1:
            raise ValueError('invalid low/high thresholds')
        if self.operation_backend not in ('curobo', 'molmobot', 'pi05'):
            raise ValueError('unknown operation backend')
        if self.operation_backend in ('molmobot', 'pi05') and not self.policy_config:
            raise ValueError('VLA requires a policy config')
        if not 0 < self.head_fov_margin < 1:
            raise ValueError('invalid head FOV margin')
        if not math.isfinite(self.head_fov_weight) or self.head_fov_weight <= 0:
            raise ValueError('invalid head FOV weight')
        if self.grasp_candidate_order not in ('facing', 'tcp_distance'):
            raise ValueError('unknown grasp candidate order')
        if not math.isfinite(self.grasp_roll_rad) or not math.isfinite(self.initial_settle_s) or self.initial_settle_s < 0:
            raise ValueError('invalid grasp roll/settling')
        if self.planner_backend != 'curobo_v2_v080':
            raise ValueError('unknown planner backend')
        if self.operation_retries > 1:
            raise ValueError('S1 permits at most one outer retry')
        if self.max_attempts != 5:
            raise ValueError('V2 uses native plan_pose default max_attempts=5')
        base_mode = 'policy_relative' if self.operation_backend in ('molmobot','pi05') else 'holonomic_joint_planning'
        if self.operation_base_mode not in ((base_mode, 'fixed') if self.operation_backend == 'curobo' else (base_mode,)):
            raise ValueError('operation base mode does not match backend')
        if self.operation_backend == 'molmobot' and (self.width, self.height) != (1024, 576):
            raise ValueError('MolmoBot evaluation requires 1024x576')
        if self.torso_error_policy != 'record_only':
            raise ValueError('V2 torso error policy must be record_only')
        for key in ('trials_per_station', 'start_count', 'min_target_pixels',
                    'max_navigation_steps', 'max_operation_plans', 'max_open_plans', 'max_grasp_candidates',
                    'num_ik_seeds', 'num_trajopt_seeds', 'max_attempts', 'width', 'height'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('invalid ' + key)
        for key in ('seed', 'operation_retries', 'max_s1_candidates'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError('invalid ' + key)
        for key in ('local_base_radius_m', 'lastmile_nearby_m', 'head_fov_min_depth_m', 'radius_m', 'map_resolution_m',
                    'smoothing_support_distance_m', 'navigation_speed_m_s', 'navigation_yaw_speed_rad_s',
                    'arrival_tolerance_m', 'arrival_tolerance_rad', 'attempt_timeout_s', 'gripper_width_m',
                    'open_hinge_rad', 'open_slide_m', 'open_hold_s', 'open_waypoint_step_rad', 'open_waypoint_step_m'):
            value = getattr(self, key)
            if not math.isfinite(value) or value <= 0:
                raise ValueError('invalid ' + key)
        for value in (self.spacing_m, self.smoothing_sigma_m):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError('spacing and smoothing must be positive')
        if not math.isfinite(self.navigation_margin_m) or self.navigation_margin_m < 0:
            raise ValueError('invalid navigation margin')
        for key in ('time_dilation', 'open_fraction'):
            value = getattr(self, key)
            if not 0 < value <= 1:
                raise ValueError('invalid ' + key)
        if not 0 <= self.approach_offset_m <= .02:
            raise ValueError('invalid approach offset')
        if self.width % 2 or self.height % 2:
            raise ValueError('image dimensions must be even')
        if not self.operations or set(self.operations) - {'pick', 'open'}:
            raise ValueError('unsupported operation')
        if not self.torso_heights or any(not 0 <= h <= .738 for h in self.torso_heights):
            raise ValueError('invalid torso heights')


def load_execution_config(path):
    return ExecutionConfig(**read_config(path, repo_fields=('policy_config',)))


def execution_profile(robot, config):
    return {'robot': asdict(robot), 'width': config.width, 'height': config.height,
            'backend': config.operation_backend, 'policy': asdict(load_policy_config(config.policy_config)) if config.policy_config else None}


def calibration_execution(config):
    """标定匹配执行条件，不把选点和展示参数当作物理执行条件。"""
    selection = {'lastmile_low', 'lastmile_high', 'lastmile_nearby_m', 'start_count',
                 'max_s1_candidates', 'smoothing_sigma_m', 'smoothing_support_distance_m'}
    return {key: value for key, value in config.items() if key not in selection}
