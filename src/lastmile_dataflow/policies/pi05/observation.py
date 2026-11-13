"""Actual robot observations; no target geometry or planner inputs."""

import numpy as np

from .config import PI05_CAMERAS

PROFILES = {"panda_omron": "robocasa-v1", "rby1m": "position-v1"}


def observation(sim, frames, prompt):
    robot = sim.robot.config
    if robot.control_profile != PROFILES[robot.name]:
        raise ValueError(f"π0.5 {robot.name} requires {PROFILES[robot.name]}")
    cameras = PI05_CAMERAS[robot.name]
    return {
        "images": {key: frames[name] for key, name in cameras.items()},
        "state": np.asarray(sim.robot.record_state(), np.float32),
        "prompt": prompt,
    }


# RoboCasa365 native observation keys of the 16D Panda state, in model order
# (relative end effector position/quaternion, base position/quaternion, gripper qpos; quaternions xyzw).
ROBOCASA_STATE_KEYS = (
    "robot0_base_to_eef_pos",
    "robot0_base_to_eef_quat",
    "robot0_base_pos",
    "robot0_base_quat",
    "robot0_gripper_qpos",
)


def robocasa_observation(native, frames, prompt):
    """π0.5 input from a native RoboCasa observation dict and upright camera frames."""
    return {
        "images": {key: frames[name] for key, name in PI05_CAMERAS["panda_omron"].items()},
        "state": np.concatenate([np.asarray(native[key], np.float32) for key in ROBOCASA_STATE_KEYS]),
        "prompt": prompt,
    }
