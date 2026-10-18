"""Actual robot observations; no target geometry or planner inputs."""
import numpy as np


def observation(sim, frames, prompt):
    if sim.robot.config.name == 'panda_omron':
        if sim.robot.config.control_profile != 'robocasa-v1':
            raise ValueError('π0.5 Panda requires robocasa-v1')
        names = ('robot0_agentview_left', 'robot0_eye_in_hand', 'robot0_agentview_right')
    else:
        if sim.robot.config.control_profile != 'position-v1':
            raise ValueError('π0.5 RBY-1 requires position-v1')
        names = ('head_camera', 'wrist_camera_l', 'wrist_camera_r')
    keys = ('base_0_rgb', 'left_wrist_0_rgb', 'right_wrist_0_rgb')
    return {'images': {key: frames[name] for key,name in zip(keys,names)},
            'state': np.asarray(sim.robot.record_state(), np.float32), 'prompt': prompt}
