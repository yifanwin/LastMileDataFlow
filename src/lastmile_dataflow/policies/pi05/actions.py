"""Map physical RBY-1 predictions to the position command envelope."""
import numpy as np

from ...runtime.control.action import vector
from ...runtime.control.action_limits import sanitize_action


def execution_action(robot, prediction):
    if robot.config.name == 'panda_omron':
        return prediction  # RoboCasa clips the canonical action in its adapter.
    action = vector(prediction, 20)
    config = robot.config
    norm = np.linalg.norm(action[:2])
    if norm > config.max_base_delta:
        action[:2] *= config.max_base_delta/norm
    action[2] = np.clip(action[2], -config.max_yaw_delta, config.max_yaw_delta)
    for segment in (slice(3,10), slice(11,18)):
        action[segment] = np.clip(action[segment], -config.max_arm_delta, config.max_arm_delta)
    action[[10,18]] = np.clip(action[[10,18]], *config.gripper_limits)
    action[19] = np.clip(action[19], *config.torso_limits)
    # Keep the existing live joint/actuator checks after protocol clipping.
    return sanitize_action(robot, action)[0]
