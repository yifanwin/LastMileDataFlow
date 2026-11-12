from dataclasses import dataclass, field
import math
from lastmile_dataflow.runtime.control.interface import ROBOT_NAMESPACE


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
