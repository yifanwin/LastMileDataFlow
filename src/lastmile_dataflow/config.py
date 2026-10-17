"""Robot/config parameters and explicit repository/data path loading."""
from dataclasses import dataclass, field
import math
import json
import os
from pathlib import Path


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
    control_profile: str = "position-v1"
    initial: dict = field(default_factory=lambda: {
        "base": [0.0, 0.0, 0.0], "torso": [0.0], "head": [0.0, 0.6],
        "left_arm": [0.5, 0.0, 0.0, -2.3, 0.0, -0.5, 0.0],
        "right_arm": [0.5, 0.0, 0.0, -2.3, 0.0, -0.5, 0.0],
        "left_gripper": [-0.05], "right_gripper": [-0.05]})

    def __post_init__(self):
        if self.control_profile not in ('position-v1', 'molmobot-native-v1'):
            raise ValueError('unknown robot control profile')
        if self.name != "rby1m" or self.namespace != "robot_0/":
            raise ValueError("v1 only supports rby1m / robot_0/")
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


@dataclass(frozen=True)
class PandaOmronConfig:
    model_path: str
    max_base_delta: float = .10
    max_yaw_delta: float = .20
    max_arm_delta: float = .20
    observation_camera: str = "overview"
    observation_position: tuple = (.1, 0., 1.65)
    observation_xyaxes: tuple = (0., -1., 0., .2425356, 0., .9701425)
    observation_fovy: float = 75.
    name: str = "panda_omron"
    namespace: str = "robot_0/"
    control_profile: str = "position-v1"
    control_hz: float = 20.0
    physics_dt: float = 0.004
    cameras: tuple = ("overview", "robot0_eye_in_hand")
    gravity_compensation: bool = True
    base_kp: tuple = (25000., 25000., 5000.)
    base_kv: tuple = (1000., 1000., 1500.)
    lift_kp: float = 50000.
    lift_kv: float = 1000.
    arm_kp: tuple = (4500., 4500., 3500., 3500., 2000., 2000., 2000.)
    arm_kv: tuple = (450., 450., 350., 350., 200., 200., 200.)
    initial: dict = field(default_factory=lambda: {
        "base_xyyaw": [0., 0., 0.], "lift": 0.2,
        "arm_q": {"right": [0., math.pi/16-.2, 0., -5*math.pi/6, 0., math.pi-.4, math.pi/4]},
        "gripper_width": {"right": .08}})

    def __post_init__(self):
        if self.name != "panda_omron" or self.control_profile != "position-v1":
            raise ValueError("PandaOmron requires position-v1")
        if not 0 < self.control_hz * self.physics_dt <= 1:
            raise ValueError("invalid PandaOmron control/physics rate")


def robot_config(values):
    """The sole config selection boundary; existing RobotConfig remains RBY-1."""
    cls = {"rby1m": RobotConfig, "panda_omron": PandaOmronConfig}[values.get("name", "rby1m")]
    return cls(**values)


def load_robot_config(path):
    return robot_config(read_config(path, data_fields=("model_path",)))


@dataclass(frozen=True)
class CollectionConfig:
    output_dir: str = "outputs"
    seed: int = 42
    max_steps: int = 10
    max_initialization_trials: int = 80
    width: int = 320
    height: int = 240
    record_video: bool = True
    record_images: bool = True
    record_physics: bool = False
    record_depth: bool = False
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
        for key in ("penetration_ratio", "penetration_floor_m", "penetration_ceiling_m",
                    "feedback_warning_rad"):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"invalid {key}")
        if self.penetration_floor_m > self.penetration_ceiling_m:
            raise ValueError("invalid penetration interval")


REPO_ROOT = Path(__file__).absolute().parents[2]


def repo_path(value):
    # Do not dereference the virtual-environment Python symlink.
    return os.path.abspath(REPO_ROOT / Path(value).expanduser())


@dataclass(frozen=True)
class LocalConfig:
    assets_dir: Path | None = None
    output_dir: Path = Path('outputs')


def load_local_config():
    """Optional machine defaults; configured paths are relative to the repository."""
    path = REPO_ROOT / 'configs/local.json'
    if not path.exists():
        return LocalConfig()
    values = json.loads(path.read_text())
    return LocalConfig(**{key: Path(repo_path(value)) for key, value in values.items()})


def read_config(path, *, data_fields=(), repo_fields=()):
    """Resolve only the fields explicitly named by the owning config loader."""
    value = json.loads(Path(repo_path(path)).read_text())
    root = value.pop('data_root', None)
    if data_fields:
        if not root:
            raise ValueError('data_root must be configured explicitly')
        root = Path(repo_path(root))
        for key in data_fields:
            item = value.get(key)
            if isinstance(item, dict):
                value[key] = {name: os.path.abspath(root / Path(p).expanduser()) for name, p in item.items()}
            elif item:
                value[key] = os.path.abspath(root / Path(item).expanduser())
    for key in repo_fields:
        if value.get(key):
            value[key] = repo_path(value[key])
    return value


def load_config(cls, path):
    data_fields = ('model_path',) if cls is RobotConfig else ('output_dir',)
    return cls(**read_config(path, data_fields=data_fields))
