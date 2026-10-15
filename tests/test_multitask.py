"""Small tests of the real multitask input and action-buffer semantics."""

import ast
import importlib.util
from pathlib import Path
import unittest

import numpy as np

HAS_POLICY = all(
    importlib.util.find_spec(name) is not None
    for name in ("torch", "torchvision", "transformers", "pydantic", "einops")
)


class InferenceClosureTests(unittest.TestCase):
    def test_no_training_or_external_source_imports(self):
        root = (
            Path(__file__).resolve().parents[1]
            / "src/lastmile_dataflow/policies/multitask"
        )
        banned = (
            "olmo",
            "torchmetrics",
            "datasets",
            "boto3",
            "google",
            "decord",
            "torch.distributed",
        )
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertFalse(
                        any(
                            name == prefix or name.startswith(prefix + ".")
                            for prefix in banned
                        ),
                        (path.name, name),
                    )


@unittest.skipUnless(HAS_POLICY, "install the policy extra to test model preprocessing")
class MultitaskPolicyTests(unittest.TestCase):
    def make_policy(self, operation):
        from lastmile_dataflow.policies.multitask.policy import MolmoBotRBY1MultitaskPolicy, MolmoBotRBY1PickPnPPolicyConfig, MolmoBotRBY1DoorPlusOpenPolicyConfig

        config_cls = (
            MolmoBotRBY1PickPnPPolicyConfig
            if operation == "pick"
            else MolmoBotRBY1DoorPlusOpenPolicyConfig
        )
        policy = MolmoBotRBY1MultitaskPolicy(
            config_cls(checkpoint_path="unused", img_resolution=(64, 48))
        )

        class Agent:
            def __init__(self):
                self.calls = []

            def get_action_chunk(self, **kwargs):
                self.calls.append(kwargs)
                chunk = np.full((16, 20), 0.25, np.float32)
                chunk[:, 10] = 5.0
                chunk[:, 18] = 4.0
                return chunk

        policy.agent = Agent()
        obs = {
            camera: np.zeros((48, 64, 3), np.uint8) for camera in policy.camera_names
        }
        obs["qpos"] = {
            group: np.arange(size, dtype=np.float32)
            for group, size in [
                ("base", 3),
                ("left_arm", 7),
                ("left_gripper", 2),
                ("right_arm", 7),
                ("right_gripper", 2),
                ("torso", 6),
            ]
        }
        obs["task"] = "pick up the cup" if operation == "pick" else "open the door"
        return policy, obs

    def test_pick_preserves_continuous_grippers_and_refreshes_after_eight(self):
        policy, obs = self.make_policy("pick")
        for _ in range(8):
            action = policy.inference_model(obs)
        self.assertEqual(len(policy.agent.calls), 1)
        self.assertEqual(action["left_gripper"][0], 5)
        self.assertEqual(action["right_gripper"][0], 4)
        state = policy.agent.calls[0]["state"]
        self.assertEqual(state.shape, (22,))
        np.testing.assert_array_equal(state[-3:], [1, 2, 3])
        self.assertEqual(len(policy.agent.calls[0]["images"]), 3)
        policy.inference_model(obs)
        self.assertEqual(len(policy.agent.calls), 2)
        policy.reset()
        policy.inference_model(obs)
        self.assertEqual(len(policy.agent.calls), 3)

    def test_open_retains_first_conditioning_image_point_and_binary_grippers(self):
        policy, obs = self.make_policy("open")
        obs["object_image_points"] = {
            "door": {
                "head_camera": {
                    "points": np.array([[0.5, 0.5]], np.float32),
                    "num_points": np.array([1]),
                }
            }
        }
        action = policy.inference_model(obs)
        self.assertEqual(action["left_gripper"][0], 100)
        self.assertEqual(action["right_gripper"][0], -100)
        first = policy.agent.calls[0]
        self.assertEqual(len(first["images"]), 4)
        self.assertIn("<points coords=", first["task_description"])
        condition = first["images"][-1].copy()
        obs["head_camera"].fill(255)
        for _ in range(8):
            policy.inference_model(obs)
        np.testing.assert_array_equal(policy.agent.calls[-1]["images"][-1], condition)
        policy.reset()
        self.assertIsNone(policy._conditioning_image)
        self.assertIsNone(policy._conditioning_points)


if __name__ == "__main__":
    unittest.main()
