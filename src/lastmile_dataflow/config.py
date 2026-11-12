"""Robot/config parameters and explicit repository/data path loading."""

from dataclasses import dataclass, field, replace
import math
import os
from pathlib import Path

import yaml

# Fixed by the v1 protocol for both robots; not configurable.
ROBOT_NAMESPACE = "robot_0/"
RBY1_PLANNING_ADAPTER = "joint_waypoints_v1"
_PROTOCOL_KEYS = {"namespace": ROBOT_NAMESPACE, "planning_adapter": RBY1_PLANNING_ADAPTER}


@dataclass(frozen=True)
class RobotConfig:
    model_path: str
    arm_working_reach_m: float | None = None
    base_radius_m: float | None = None
    name: str = "rby1m"
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
    control_profile: str = "position-v1"
    initial: dict = field(
        default_factory=lambda: {
            "base": [0.0, 0.0, 0.0],
            "torso": [0.0],
            "head": [0.0, 0.6],
            "left_arm": [0.5, 0.0, 0.0, -2.3, 0.0, -0.5, 0.0],
            "right_arm": [0.5, 0.0, 0.0, -2.3, 0.0, -0.5, 0.0],
            "left_gripper": [-0.05],
            "right_gripper": [-0.05],
        }
    )

    @property
    def namespace(self):
        return ROBOT_NAMESPACE

    def __post_init__(self):
        if self.control_profile not in ("position-v1", "molmobot-native-v1"):
            raise ValueError("unknown robot control profile")
        if self.name != "rby1m":
            raise ValueError("v1 only supports rby1m")
        for key in (
            "control_hz",
            "physics_dt",
            "max_base_delta",
            "max_yaw_delta",
            "max_arm_delta",
            "gripper_kp",
            "gripper_kv",
        ):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"invalid {key}")
        if self.control_hz * self.physics_dt > 1:
            raise ValueError("control rate exceeds physics rate")
        for name, limits, legal in (
            ("torso", self.torso_limits, (0.0, 0.738)),
            ("gripper", self.gripper_limits, (-0.05, 0.0)),
        ):
            if len(limits) != 2 or not all(math.isfinite(x) for x in limits):
                raise ValueError(f"invalid {name} limits")
            if not legal[0] <= limits[0] <= limits[1] <= legal[1]:
                raise ValueError(f"limits exceed RBY1 {name} protocol")
        if not self.cameras or len(set(self.cameras)) != len(self.cameras):
            raise ValueError("cameras must be nonempty and unique")


@dataclass(frozen=True)
class PandaOmronConfig:
    model_path: str
    arm_working_reach_m: float | None = None
    base_radius_m: float | None = None
    max_base_delta: float = 0.10
    max_yaw_delta: float = 0.20
    max_arm_delta: float = 0.20
    observation_camera: str = "overview"
    observation_position: tuple = (0.1, 0.0, 1.65)
    observation_xyaxes: tuple = (0.0, -1.0, 0.0, 0.2425356, 0.0, 0.9701425)
    observation_fovy: float = 75.0
    name: str = "panda_omron"
    control_profile: str = "position-v1"
    control_hz: float = 20.0
    physics_dt: float = 0.004
    cameras: tuple = ("overview", "robot0_eye_in_hand")
    gravity_compensation: bool = True
    base_kp: tuple = (25000.0, 25000.0, 5000.0)
    base_kv: tuple = (1000.0, 1000.0, 1500.0)
    lift_kp: float = 50000.0
    lift_kv: float = 1000.0
    arm_kp: tuple = (4500.0, 4500.0, 3500.0, 3500.0, 2000.0, 2000.0, 2000.0)
    arm_kv: tuple = (450.0, 450.0, 350.0, 350.0, 200.0, 200.0, 200.0)
    initial: dict = field(
        default_factory=lambda: {
            "base_xyyaw": [0.0, 0.0, 0.0],
            "lift": 0.2,
            "arm_q": {"right": [0.0, math.pi / 16 - 0.2, 0.0, -5 * math.pi / 6, 0.0, math.pi - 0.4, math.pi / 4]},
            "gripper_width": {},
        }
    )

    @property
    def namespace(self):
        return ROBOT_NAMESPACE

    def __post_init__(self):
        if self.name != "panda_omron" or self.control_profile not in ("position-v1", "robocasa-v1"):
            raise ValueError("unsupported PandaOmron control profile")
        if not 0 < self.control_hz * self.physics_dt <= 1:
            raise ValueError("invalid PandaOmron control/physics rate")


def robot_config(values):
    """The sole config selection boundary; existing RobotConfig remains RBY-1.

    Snapshots written before the protocol constants left the config still carry them; they must match.
    """
    values = dict(values)
    for key, fixed in _PROTOCOL_KEYS.items():
        if values.pop(key, fixed) != fixed:
            raise ValueError(f"unsupported {key}: v1 uses {fixed}")
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


REPO_ROOT = Path(__file__).absolute().parents[2]


def repo_path(value):
    # Do not dereference the virtual-environment Python symlink.
    return os.path.abspath(REPO_ROOT / Path(value).expanduser())


@dataclass(frozen=True)
class StationRun:
    """Recording switches and process resources of one calibration or collection run."""

    execution: str
    assets_dir: str | None = None
    max_trials: int | None = None
    record_video: bool = False
    recording_mode: str | None = None
    planning_workers_per_gpu: list = field(default_factory=lambda: [1])
    cpu_workers: int = 60
    preparation_workers: int = 12

    def __post_init__(self):
        if self.recording_mode not in (None, "numerical", "visual"):
            raise ValueError("recording_mode must be numerical or visual")
        cpus = max(1, os.cpu_count() or 1)
        if not 1 <= self.cpu_workers <= cpus:
            raise ValueError(f"cpu_workers must be between 1 and {cpus}")
        if not 1 <= self.preparation_workers <= min(64, cpus):
            raise ValueError(f"preparation_workers must be between 1 and {min(64, cpus)}")

    @property
    def mode(self):
        """Calibration recording: numerical keeps no training RGB; visual records RGB (and video if asked)."""
        return self.recording_mode or ("visual" if self.record_video else "numerical")

    def calibration_recording(self, collection):
        visual = self.mode == "visual"
        return replace(collection, record_images=visual, record_video=visual and collection.record_video)


def add_station_run_arguments(cli):
    """Command-line options of the calibration and collection scripts (defaults are StationRun's)."""
    cli.add_argument("--config", type=Path, required=True, help="执行配置 YAML，见 configs/execution")
    cli.add_argument("--assets-dir", type=Path, help="资产目录；省略时取 configs/local.yaml 的 assets_dir")
    cli.add_argument("--max-trials", type=int, help="每场景试验上限，只做少量检查；不完整标定不能用于正式配对")
    cli.add_argument("--record-video", action="store_true", default=None, help="visual 模式下额外录像")
    cli.add_argument(
        "--recording-mode",
        choices=("numerical", "visual"),
        help="标定记录：numerical 不存训练 RGB（默认，--record-video 时为 visual）",
    )
    cli.add_argument("--planning-workers-per-gpu", type=int, nargs="+", help="每卡规划进程数 1–4，默认 1")
    cli.add_argument("--cpu-workers", type=int, help="CPU 执行进程数，默认 60")
    cli.add_argument("--preparation-workers", type=int, help="站位准备进程数，默认 12")


def station_run(args):
    """StationRun from ``add_station_run_arguments`` options; CLI paths are relative to the working directory."""
    values = {
        "execution": str(args.config.resolve()),
        "assets_dir": str(args.assets_dir.resolve()) if args.assets_dir else None,
        "max_trials": args.max_trials,
        "record_video": args.record_video,
        "recording_mode": args.recording_mode,
        "planning_workers_per_gpu": args.planning_workers_per_gpu,
        "cpu_workers": args.cpu_workers,
        "preparation_workers": args.preparation_workers,
    }
    return StationRun(**{key: value for key, value in values.items() if value is not None})


@dataclass(frozen=True)
class LocalConfig:
    assets_dir: Path | None = None


def load_local_config():
    """Optional machine defaults (configs/local.yaml); paths are relative to the repository."""
    path = REPO_ROOT / "configs/local.yaml"
    if not path.exists():
        return LocalConfig()
    return LocalConfig(**{key: Path(value) for key, value in read_config(path, repo_fields=("assets_dir",)).items()})


def read_config(path, *, data_fields=(), repo_fields=()):
    """Read one YAML input config; resolve only the fields explicitly named by the owning loader.

    Run outputs (config.json, summary.json …) are JSON and are read with ``utils.io.read_json``.
    """
    path = Path(repo_path(path))
    if path.suffix not in (".yaml", ".yml"):
        raise ValueError(f"input configs are YAML: {path}")
    value = yaml.safe_load(path.read_text()) or {}
    root = value.pop("data_root", None)
    if data_fields:
        if not root:
            raise ValueError("data_root must be configured explicitly")
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
