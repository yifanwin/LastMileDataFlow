"""三类严格配置。路径相对于配置文件，不相对于启动目录。"""
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
import math
import re

from .io import read_json


def construct(cls, value):
    unknown = set(value) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"{cls.__name__} unknown keys: {sorted(unknown)}")
    return cls(**value)


@dataclass(frozen=True)
class RobotConfig:
    model_path: str
    name: str = "rby1m"
    namespace: str = "robot_0/"
    control_hz: float = 20.0
    physics_dt: float = 0.004
    cameras: tuple = ("head_camera", "wrist_camera_l", "wrist_camera_r")
    torso_limits: tuple = (0.0, 0.738)
    gripper_limits: tuple = (-0.05, 0.0)
    max_base_delta: float = 0.10
    max_yaw_delta: float = 0.20
    max_arm_delta: float = 0.20
    gripper_kp: float = 4000.0
    gripper_kv: float = 400.0
    gravity_compensation: bool = True
    planning_adapter: str = "joint_waypoints_v1"
    initial: dict = field(default_factory=lambda: {
        "base": [0.0, 0.0, 0.0], "torso": [0.0], "head": [0.0, 0.6],
        "left_arm": [0.5, 0.0, 0.0, -2.3, 0.0, -0.5, 0.0],
        "right_arm": [0.5, 0.0, 0.0, -2.3, 0.0, -0.5, 0.0],
        "left_gripper": [-0.05], "right_gripper": [-0.05]})

    def __post_init__(self):
        if self.name != "rby1m" or self.namespace != "robot_0/":
            raise ValueError("v1 only supports rby1m / robot_0/")
        if not isinstance(self.gravity_compensation, bool) or not isinstance(self.initial, dict):
            raise ValueError("invalid gravity compensation or initial configuration type")
        for key in ("control_hz", "physics_dt", "max_base_delta", "max_yaw_delta",
                    "max_arm_delta", "gripper_kp", "gripper_kv"):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"invalid {key}")
        if self.control_hz * self.physics_dt > 1:
            raise ValueError("control rate exceeds physics rate")
        if self.planning_adapter != "joint_waypoints_v1":
            raise ValueError("unsupported planning adapter")
        for name, limits, legal in (("torso", self.torso_limits, (0., .738)),
                                    ("gripper", self.gripper_limits, (-.05, 0.))):
            if len(limits) != 2 or not all(math.isfinite(x) for x in limits):
                raise ValueError(f"invalid {name} limits")
            if not legal[0] <= limits[0] <= limits[1] <= legal[1]:
                raise ValueError(f"limits exceed RBY1 {name} protocol")
        if not self.cameras or len(set(self.cameras)) != len(self.cameras):
            raise ValueError("cameras must be nonempty and unique")
        if not all(isinstance(c, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", c) for c in self.cameras):
            raise ValueError("invalid camera alias")


@dataclass(frozen=True)
class TaskConfig:
    task_id: str = "foundation-smoke"
    case_type: str = "foundation"
    target: str | None = None
    operation: str = "short_action"
    allowed_edits: tuple = ()
    success_conditions: dict = field(default_factory=dict)
    fixed_base: bool = False

    def __post_init__(self):
        if not isinstance(self.fixed_base, bool) or not isinstance(self.success_conditions, dict):
            raise ValueError("invalid task flag or success conditions type")
        if self.target is not None and not isinstance(self.target, str):
            raise ValueError("target must be an explicit string identifier")
        if self.case_type not in ("foundation", "case1", "case1.5", "case2", "case3"):
            raise ValueError("unsupported case type in v1")
        if self.operation != "short_action" or self.success_conditions or self.allowed_edits:
            raise ValueError("phase 1 does not implement scene edits or task success evaluators")


@dataclass(frozen=True)
class CollectionConfig:
    output_dir: str = "outputs"
    seed: int = 0
    max_steps: int = 10
    max_initialization_trials: int = 80
    agent_call_budget: int = 0
    width: int = 320
    height: int = 240
    record_video: bool = True
    protocol_version: str = "lightweight-v1"
    penetration_ratio: float = 0.10
    penetration_floor_m: float = 0.010
    penetration_ceiling_m: float = 0.030
    feedback_warning_rad: float = 0.05

    def __post_init__(self):
        if not isinstance(self.record_video, bool):
            raise ValueError("record_video must be boolean")
        for key in ("max_steps", "max_initialization_trials", "width", "height"):
            v = getattr(self, key)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise ValueError(f"{key} must be positive integer")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if not isinstance(self.agent_call_budget, int) or isinstance(self.agent_call_budget, bool) or self.agent_call_budget < 0:
            raise ValueError("invalid agent budget")
        if self.width % 2 or self.height % 2:
            raise ValueError("video dimensions must be even")
        if self.protocol_version != "lightweight-v1":
            raise ValueError("unknown protocol version")
        for key in ("penetration_ratio", "penetration_floor_m", "penetration_ceiling_m",
                    "feedback_warning_rad"):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"invalid {key}")
        if self.penetration_floor_m > self.penetration_ceiling_m:
            raise ValueError("invalid penetration interval")


def load_config(cls, path):
    path = Path(path).resolve()
    value = read_json(path)
    for key in ("model_path", "output_dir"):
        if key in value:
            value[key] = str((path.parent / value[key]).resolve())
    return construct(cls, value)


def freeze_config(robot, task, collection):
    return {"schema_version": "1.0", "robot": asdict(robot), "task": asdict(task),
            "collection": asdict(collection)}
