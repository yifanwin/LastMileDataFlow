"""Actual robot observations; no target geometry or planner inputs."""
import numpy as np

from .config import PI05_CAMERAS

PROFILES = {'panda_omron': 'robocasa-v1', 'rby1m': 'position-v1'}


def observation(sim, frames, prompt):
    robot = sim.robot.config
    if robot.control_profile != PROFILES[robot.name]:
        raise ValueError(f'π0.5 {robot.name} requires {PROFILES[robot.name]}')
    cameras = PI05_CAMERAS[robot.name]
    return {'images': {key: frames[name] for key, name in cameras.items()},
            'state': np.asarray(sim.robot.record_state(), np.float32), 'prompt': prompt}
