from dataclasses import dataclass, field
import math
from ..interface import ROBOT_NAMESPACE


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
