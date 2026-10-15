"""控制提交、快照与执行后的生命周期限制。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lastmile_dataflow.runtime.control.action import (
    InvalidAction,
)
from lastmile_dataflow.runtime.execution.context import clone_sim
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.loading.source import resolve_target
from tests.helpers import make_sim


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim, self.robot = make_sim(self.root)

    def tearDown(self):
        self.sim.close()
        self.tmp.cleanup()

    def test_atomic_command_and_actual_controls(self):
        s = self.sim
        before = s.data.ctrl.copy()
        a = s.robot.neutral_action()
        a[19] = 0.2
        a[3] = 0.1
        command = s.robot.apply(a)
        self.assertEqual(len(command["submitted_action"]), 20)
        np.testing.assert_allclose(command["joint_targets"]["torso"], [0, 0.2, -0.4, 0.2, 0, 0])
        np.testing.assert_allclose(command["joint_targets"]["left_arm"], [0.6, 0, 0, -2.3, 0, -0.5, 0])
        self.assertEqual(command["joint_targets"]["head"], [0, 0.6])
        expected = s.data.ctrl.copy()
        a[18] = float("nan")
        with self.assertRaises(InvalidAction):
            s.robot.apply(a)
        np.testing.assert_array_equal(s.data.ctrl, expected)
        self.assertFalse(np.array_equal(before, expected))

    def test_joint_limit_rejects_before_mutation(self):
        s = self.sim
        name = "left_arm_0"
        j = s.robot.joints[name]
        s.data.qpos[s.robot.addresses[name]] = s.model.jnt_range[j, 1] - 0.01
        before = s.data.ctrl.copy()
        a = s.robot.neutral_action()
        a[3] = 0.1
        with self.assertRaises(InvalidAction):
            s.robot.apply(a)
        np.testing.assert_array_equal(s.data.ctrl, before)

    def test_snapshot_exact_and_no_reset_after_begin(self):
        s = self.sim
        frozen = self.root / "frozen"
        s.freeze(frozen)
        initial = s.state_vector().copy()
        s.data.qpos[0] += 0.1
        s.restore_snapshot(frozen / "initial.npz")
        np.testing.assert_array_equal(s.state_vector(), initial)
        restored = Simulation.from_snapshot(frozen, self.robot, target="target")
        np.testing.assert_array_equal(restored.state_vector(), initial)
        restored.close()
        s.begin()
        s.step(s.robot.neutral_action())
        self.assertAlmostEqual(s.data.time, 0.048)
        with self.assertRaises(RuntimeError):
            s.restore_snapshot(frozen / "initial.npz")
        with self.assertRaises(RuntimeError):
            s.freeze(self.root / "other")
        with self.assertRaises(RuntimeError):
            s.robot.initialize()

    def test_target_ambiguity_rejected(self):
        catalog = [
            {"instance_id": "a", "asset_id": "Cup", "mjcf_body": "target"},
            {"instance_id": "b", "asset_id": "Cup", "mjcf_body": "robot_0/base"},
        ]
        with self.assertRaises(ValueError):
            resolve_target(self.sim.model, catalog, "Cup")


class CloneTests(unittest.TestCase):
    def test_diagnostic_clone_does_not_reset_running_simulation(self):
        with tempfile.TemporaryDirectory() as d:
            sim, robot = make_sim(d)
            sim.freeze(Path(d) / "scene")
            sim.begin()
            clone = clone_sim(sim, target="target")
            self.assertFalse(clone.started)
            clone.robot.initialize()
            self.assertTrue(sim.started)
            clone.close()
            sim.close()
