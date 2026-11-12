"""Execution parameters and the shared robot/camera/backend profile."""

from dataclasses import dataclass
import math

from lastmile_dataflow.utils.io import read_config

# Fixed execution contract; written into results, not configurable.
PLANNER_BACKEND = "curobo_v2_v080"
TORSO_ERROR_POLICY = "record_only"
MAX_OPERATION_RETRIES = 1  # S1 permits at most one outer retry after navigation

_POSITIVE_INTS = (
    "trials_per_station",
    "min_target_pixels",
    "max_navigation_steps",
    "max_operation_plans",
    "max_open_plans",
    "max_grasp_candidates",
    "num_ik_seeds",
    "num_trajopt_seeds",
    "width",
    "height",
)
_NONNEGATIVE_INTS = ("seed", "operation_retries")
_POSITIVE = (
    "grasp_fps_position_scale_m",
    "grasp_fps_rotation_scale_rad",
    "local_base_radius_m",
    "head_fov_min_depth_m",
    "radius_m",
    "map_resolution_m",
    "navigation_speed_m_s",
    "navigation_yaw_speed_rad_s",
    "arrival_tolerance_m",
    "arrival_tolerance_rad",
    "attempt_timeout_s",
    "gripper_width_m",
    "open_hinge_rad",
    "open_slide_m",
    "open_hold_s",
    "open_waypoint_step_rad",
    "open_waypoint_step_m",
)
_NONNEGATIVE = ("navigation_margin_m", "navigation_clearance_weight", "navigation_max_penetration_m")
_UNIT = ("time_dilation", "open_fraction")  # (0, 1]


@dataclass(frozen=True)
class ExecutionConfig:
    operation_backend: str = "curobo"
    policy_config: str | None = None
    lastmile_low: float = 0.2
    lastmile_high: float = 0.8
    # merged_hull: every environment geom as a closed hull in one mesh;
    # box_cuboids: native box geoms within the robot's reach as posed cuboids, the rest still merged
    environment_representation: str = "merged_hull"
    operation_base_mode: str = "holonomic_joint_planning"
    initial_settle_s: float = 0.0
    grasp_roll_rad: float = 0.0
    grasp_candidate_order: str = "facing"
    local_base_radius_m: float = 0.35
    third_person_enabled: bool = True
    head_fov_enabled: bool = False
    head_fov_margin: float = 0.9
    head_fov_weight: float = 10000.0
    head_fov_min_depth_m: float = 0.01
    radius_m: float = 2.0
    spacing_m: float | None = None
    trials_per_station: int = 5
    operation_retries: int = 1
    min_target_pixels: int = 24
    map_resolution_m: float = 0.05
    smoothing_sigma_m: float | None = None
    navigation_margin_m: float = 0.02
    navigation_clearance_weight: float = 2.0
    navigation_max_penetration_m: float = 0.005
    navigation_whole_body: bool = False
    navigation_speed_m_s: float = 0.20
    navigation_yaw_speed_rad_s: float = 0.4
    arrival_tolerance_m: float = 0.015
    arrival_tolerance_rad: float = 0.025
    max_navigation_steps: int = 2400
    attempt_timeout_s: float = 300.0
    max_operation_plans: int = 12
    max_open_plans: int = 128
    max_grasp_candidates: int = 4
    num_ik_seeds: int = 32
    num_trajopt_seeds: int = 4
    time_dilation: float = 0.5
    grasp_fps_position_scale_m: float = 0.1
    grasp_fps_rotation_scale_rad: float = math.pi / 2
    approach_offset_m: float = 0.0
    export_figures: bool = True
    seed: int = 42
    width: int = 320
    height: int = 240
    gripper_width_m: float = 0.10  # RBY1M model jaw travel; measured geometry saved per scene
    open_fraction: float = 0.3
    open_hinge_rad: float = math.pi / 6
    open_slide_m: float = 0.10
    open_hold_s: float = 1.0
    open_waypoint_step_rad: float = 0.04
    open_waypoint_step_m: float = 0.008

    def __post_init__(self):
        if not 0 <= self.lastmile_low < self.lastmile_high <= 1:
            raise ValueError("invalid low/high thresholds")
        if self.operation_backend not in ("curobo", "molmobot", "pi05"):
            raise ValueError("unknown operation backend")
        if self.operation_backend in ("molmobot", "pi05") and not self.policy_config:
            raise ValueError("VLA requires a policy config")
        if not 0 < self.head_fov_margin < 1:
            raise ValueError("invalid head FOV margin")
        if not math.isfinite(self.head_fov_weight) or self.head_fov_weight <= 0:
            raise ValueError("invalid head FOV weight")
        if self.grasp_candidate_order not in ("facing", "tcp_distance"):
            raise ValueError("unknown grasp candidate order")
        if (
            not math.isfinite(self.grasp_roll_rad)
            or not math.isfinite(self.initial_settle_s)
            or self.initial_settle_s < 0
        ):
            raise ValueError("invalid grasp roll/settling")
        if self.environment_representation not in ("merged_hull", "box_cuboids"):
            raise ValueError("unknown environment representation")
        if self.operation_retries > MAX_OPERATION_RETRIES:
            raise ValueError("S1 permits at most one outer retry")
        base_mode = "policy_relative" if self.operation_backend in ("molmobot", "pi05") else "holonomic_joint_planning"
        allowed = (base_mode, "fixed") if self.operation_backend == "curobo" else (base_mode,)
        if self.operation_base_mode not in allowed:
            raise ValueError("operation base mode does not match backend")
        if self.operation_backend == "molmobot" and (self.width, self.height) != (1024, 576):
            raise ValueError("MolmoBot evaluation requires 1024x576")
        for key in _POSITIVE_INTS:
            _check(key, type(getattr(self, key)) is int and getattr(self, key) > 0)
        for key in _NONNEGATIVE_INTS:
            _check(key, type(getattr(self, key)) is int and getattr(self, key) >= 0)
        for key in _POSITIVE:
            _check(key, math.isfinite(getattr(self, key)) and getattr(self, key) > 0)
        for key in _NONNEGATIVE:
            _check(key, math.isfinite(getattr(self, key)) and getattr(self, key) >= 0)
        for key in _UNIT:
            _check(key, 0 < getattr(self, key) <= 1)
        for value in (self.spacing_m, self.smoothing_sigma_m):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("spacing and smoothing must be positive")
        if not 0 <= self.approach_offset_m <= 0.02:
            raise ValueError("invalid approach offset")
        if self.width % 2 or self.height % 2:
            raise ValueError("image dimensions must be even")


def _check(key, ok):
    if not ok:
        raise ValueError("invalid " + key)


def load_execution_config(path):
    return ExecutionConfig(**read_config(path, repo_fields=("policy_config",)))


@dataclass(frozen=True)
class CollectionConfig:
    output_dir: str = "outputs"
    seed: int = 42
    max_steps: int = 10
    max_initialization_trials: int = 80
    width: int = 320
    height: int = 240
    record_video: bool = True
    video_cameras: dict[str, str] | None = None
    record_images: bool = True
    penetration_ratio: float = 0.10
    penetration_floor_m: float = 0.010
    penetration_ceiling_m: float = 0.030
    feedback_warning_rad: float = 0.05

    def __post_init__(self):
        for key in ("max_steps", "max_initialization_trials", "width", "height"):
            v = getattr(self, key)
            if not isinstance(v, int) or v <= 0:
                raise ValueError(f"{key} must be positive integer")
        if self.seed < 0:
            raise ValueError("seed must be nonnegative")
        if self.width % 2 or self.height % 2:
            raise ValueError("video dimensions must be even")
        for key in ("penetration_ratio", "penetration_floor_m", "penetration_ceiling_m", "feedback_warning_rad"):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"invalid {key}")
        if self.penetration_floor_m > self.penetration_ceiling_m:
            raise ValueError("invalid penetration interval")
