"""π0.5 ↔ RoboCasa native observation/action mapping (no RoboCasa or model needed)."""

import unittest

import numpy as np

from lastmile_dataflow.policies.pi05.actions import robocasa_env_action
from lastmile_dataflow.policies.pi05.observation import ROBOCASA_STATE_KEYS, robocasa_observation


class RoboCasaPi05MappingTests(unittest.TestCase):
    def test_state_order_and_camera_slots(self):
        native = {key: np.full(size, index, np.float64) for index, (key, size) in enumerate(zip(ROBOCASA_STATE_KEYS, (3, 4, 3, 4, 2)))}
        frames = {name: np.full((4, 4, 3), i, np.uint8) for i, name in enumerate(("robot0_agentview_left", "robot0_eye_in_hand", "robot0_agentview_right"))}
        observation = robocasa_observation(native, frames, "Pick up the cup.")
        np.testing.assert_array_equal(observation["state"], [0] * 3 + [1] * 4 + [2] * 3 + [3] * 4 + [4] * 2)
        self.assertEqual(observation["state"].dtype, np.float32)
        self.assertEqual({k: int(v[0, 0, 0]) for k, v in observation["images"].items()}, {"base_0_rgb": 0, "left_wrist_0_rgb": 1, "right_wrist_0_rgb": 2})

    def test_gripper_and_mode_follow_gym_threshold(self):
        prediction = np.linspace(-0.5, 0.6, 12)
        prediction[6], prediction[11] = 0.49, 0.5
        action = robocasa_env_action(prediction)
        self.assertEqual((action[6], action[11]), (-1.0, 1.0))
        np.testing.assert_array_equal(np.delete(action, [6, 11]), np.delete(prediction, [6, 11]))
        self.assertEqual(robocasa_env_action(np.r_[np.zeros(6), 1.0, np.zeros(4), -1.0])[[6, 11]].tolist(), [1.0, -1.0])


if __name__ == "__main__":
    unittest.main()
