"""Robot configuration of the RoboCasa native runtime (robosuite robot class, no LMDF assembly)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoboCasaRobotConfig:
    """Official robosuite embodiment at factory scale with its default composite controller.

    ``controller: default`` resolves to robosuite ``default_<robot>.json`` (PandaOmron: arm OSC_POSE delta in
    the base frame, torso JOINT_POSITION, base JOINT_VELOCITY) when a scene is imported; the resolved
    dictionary is stored with the scene so reconstruction never re-resolves it.
    """

    runtime: str = "robocasa"
    name: str = "panda_omron"
    embodiment: str = "PandaOmron"
    scale: float = 1.0
    controller: str = "default"
    control_freq: float = 20.0
    cameras: tuple = ("robot0_agentview_left", "robot0_eye_in_hand", "robot0_agentview_right")
    camera_width: int = 256
    camera_height: int = 256

    def __post_init__(self):
        if self.runtime != "robocasa" or self.name != "panda_omron" or self.embodiment != "PandaOmron":
            raise ValueError("RoboCasa runtime supports the official PandaOmron only")
        if self.scale != 1.0:
            raise ValueError("RoboCasa loads the official robot unscaled (scale 1.0)")
        if self.controller != "default":
            raise ValueError("RoboCasa runtime uses the default native controller")
        if self.control_freq <= 0 or min(self.camera_width, self.camera_height) <= 0 or not self.cameras:
            raise ValueError("invalid RoboCasa control rate or cameras")
