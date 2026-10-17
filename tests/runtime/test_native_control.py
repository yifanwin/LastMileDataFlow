"""MolmoBot motor 控制映射、节奏与停止。"""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.policies.molmobot_config import load_policy_config
from lastmile_dataflow.runtime.control.action import InvalidAction
from lastmile_dataflow.runtime.execution_config import load_execution_config
from lastmile_dataflow.runtime.outcomes.vla import constraint
from lastmile_dataflow.runtime.simulation import Simulation
from tests.helpers import ROOT, fixtures

ROOT = ROOT


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        source, robot = fixtures(root)
        tree = ET.parse(robot.model_path)
        for motor in tree.findall("./actuator/motor"):
            motor.set("ctrllimited", "true")
            motor.set("ctrlrange", "-100 100")
        for body in tree.findall(".//body"):
            if "gripper_finger_" in body.get("name", ""):
                body.set("name", body.get("name").replace("gripper_finger_", "ee_finger_"))
        for side in ("l", "r"):
            ET.SubElement(tree.find("./worldbody/body"), "site", name="robot_0/ee_site_" + side, pos="0 0 .1")
        tree.write(robot.model_path)
        self.robot = replace(robot, control_profile="molmobot-native-v1", control_hz=10)
        self.sim = Simulation.from_source(source, self.robot, target="target")

    def tearDown(self):
        self.sim.close()
        self.tmp.cleanup()

    def test_native_motor_not_position_and_atomic_limits(self):
        s = self.sim
        index = s.robot.act_ids["left_finger_act"]
        self.assertEqual(s.model.actuator_biastype[index], mujoco.mjtBias.mjBIAS_NONE)
        np.testing.assert_array_equal(s.model.actuator_ctrlrange[index], [-100, 100])
        a = s.robot.neutral_action()
        a[10] = 100
        a[18] = -100
        a[19] = 0.4
        result = s.robot.apply(a)
        self.assertEqual(s.data.ctrl[index], 100)
        np.testing.assert_allclose(result["joint_targets"]["torso"], [0, 0.4, -0.8, 0.4, 0, 0])
        before = s.data.ctrl.copy()
        s.robot.hold()
        np.testing.assert_array_equal(s.data.ctrl, before)
        a[2] = float("nan")
        with self.assertRaises(InvalidAction):
            s.robot.apply(a)
        np.testing.assert_array_equal(before, s.data.ctrl)

    def test_upstream_projection_explicit_and_not_position_mapping(self):
        s = self.sim
        a = s.robot.neutral_action()
        a[10] = 150
        a[18] = -7
        a[19] = 0.9
        result = s.robot.apply(a)
        self.assertEqual(result["motor_targets"]["left_gripper"], [100])
        self.assertEqual(result["motor_targets"]["right_gripper"], [-7])
        self.assertEqual(result["submitted_action"][19], 0.738)
        self.assertTrue(result["adjustments"])

    def test_100ms_no_repeated_relative_accumulation(self):
        s = self.sim
        s.begin()
        a = s.robot.neutral_action()
        a[0] = 0.02
        checks = []
        before = s.robot.group("base")[0]
        command, stop = s.step_policy(a, substep_check=lambda: checks.append(s.data.time) or {"valid": True})
        self.assertIsNone(stop)
        self.assertEqual(len(checks), 25)
        self.assertAlmostEqual(s.data.time, 0.1)
        self.assertAlmostEqual(command["joint_targets"]["base"][0], before + 0.02)
        with self.assertRaises(RuntimeError):
            s.robot.initialize()

    def test_physics_stop_not_silently_completed(self):
        s = self.sim
        s.begin()
        command, stop = s.step_policy(s.robot.neutral_action(), substep_check=lambda: {"valid": False})
        self.assertIsNotNone(stop)
        self.assertAlmostEqual(s.data.time, 0.004)

    def test_stop_transition_latches_actual_then_holds_without_drift(self):
        s = self.sim
        a = np.zeros(20)
        a[0] = 0.05
        s.robot.apply(a)
        actual = s.robot.group("base").copy()
        a[0] = 0
        stopped = s.robot.apply(a)
        np.testing.assert_array_equal(stopped["joint_targets"]["base"], actual)
        # Synthetic measured drift is pre-begin only, testing controller state.
        s.data.qpos[s.robot.addresses["base_x"]] += 0.01
        again = s.robot.apply(a)
        np.testing.assert_array_equal(again["joint_targets"]["base"], actual)
        before = s.data.ctrl.copy()
        cache = {k: v.copy() for k, v in s.robot._native_stationary_targets.items()}
        a[3] = 0.3
        with self.assertRaises(InvalidAction):
            s.robot.apply(a)
        np.testing.assert_array_equal(s.data.ctrl, before)
        for k, v in cache.items():
            np.testing.assert_array_equal(s.robot._native_stationary_targets[k], v)


class ConfigTests(unittest.TestCase):
    def test_policy_config_uses_local_backend(self):
        config = load_policy_config(ROOT / "configs/policies/molmobot.json")
        self.assertEqual(config.gripper_mode, "model_default")
        self.assertTrue(config.tokenizer_path.endswith("models/Qwen3-tokenizer"))
        self.assertFalse(hasattr(config, "source_dir"))
        sampling = load_execution_config(ROOT / "configs/execution/molmobot.json")
        self.assertAlmostEqual(sampling.radius_m, 0.7928056080178096)
        self.assertAlmostEqual(sampling.spacing_m, 0.1108796040001314)
        self.assertEqual(sampling.trials_per_station, 5)
        self.assertEqual(sampling.operation_base_mode, "policy_relative")

    def test_native_resolution_and_backend_mode_fail_closed(self):
        config = load_execution_config(ROOT / "configs/execution/molmobot.json")
        for values in ({"width": 320}, {"operation_base_mode": "holonomic_joint_planning"}):
            with self.assertRaises(ValueError):
                replace(config, **values)

    def test_two_commanded_arms_not_idle_drift_but_head_still_checked(self):
        initial = {
            "base": [0, 0, 0],
            "head": [0, 0],
            "idle_arm": [0] * 7,
            "target": "obj",
            "workspace_center_xy": [0, 0],
            "workspace_radius_m": 2,
        }
        row = {
            "base": [1, 0, 0],
            "head": [0, 0],
            "idle_arm": [0.5] * 7,
            "target": "obj",
            "torso": [0] * 6,
            "command_h": 0.7,
            "phase": "vla_operation",
            "illegal": [],
        }
        self.assertIsNone(constraint(row, initial))
        row["head"] = [0.1, 0]
        self.assertEqual(constraint(row, initial), "head_drift")
        row["base"] = [3, 0, 0]
        self.assertEqual(constraint(row, initial), "base_outside_workspace")
