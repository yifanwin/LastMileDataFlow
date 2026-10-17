"""Semantic controls and real hold groups, without external robot assets."""
import tempfile
import unittest
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from lastmile_dataflow.config import PandaOmronConfig, robot_config
from lastmile_dataflow.runtime.control.interface import RobotCommand
from lastmile_dataflow.runtime.control.panda_omron import PandaOmronAdapter
from lastmile_dataflow.runtime.control.action import InvalidAction
from lastmile_dataflow.runtime.outcomes.pick import evaluate_pick
from tests.helpers import make_sim, evidence


def panda_adapter():
    """Small position-actuated chain for protocol checks, not factory evidence."""
    root = ET.Element("mujoco")
    ET.SubElement(root, "compiler", angle="radian")
    world = ET.SubElement(root, "worldbody")
    acts = ET.SubElement(root, "actuator")
    body = ET.SubElement(world, "body")
    ns = "robot_0/"
    def add(joint, actuator, kind, limits):
        nonlocal body
        body = ET.SubElement(body, "body", pos="0 0 .02")
        ET.SubElement(body, "geom", type="sphere", size=".01", mass="1", contype="0", conaffinity="0")
        ET.SubElement(body, "joint", name=ns+joint, type=kind, axis="0 0 1", range=limits)
        ET.SubElement(acts, "position", name=ns+actuator, joint=ns+joint, kp="100", ctrlrange=limits)
    for suffix in ("forward", "side", "yaw"):
        add("mobilebase0_joint_mobile_"+suffix, "mobilebase0_actuator_mobile_"+suffix,
            "hinge" if suffix == "yaw" else "slide", "-25 25")
    add("mobilebase0_joint_torso_height", "mobilebase0_actuator_torso_height", "slide", "0 .34")
    for i in range(1, 8):
        add(f"robot0_joint{i}", f"robot0_torq_j{i}", "hinge", "-3.2 3.2")
    for i, limits in ((1, "0 .04"), (2, "-.04 0")):
        add(f"gripper0_right_finger_joint{i}", f"gripper0_right_gripper_finger_joint{i}", "slide", limits)
    for name in ("overview", "robot0_eye_in_hand"):
        ET.SubElement(body, "camera", name=ns+name)
    model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
    robot = PandaOmronAdapter(model, mujoco.MjData(model), PandaOmronConfig("unused"))
    robot.initialize()
    return robot


class RobotInterfaceTests(unittest.TestCase):
    def test_rby1_semantic_targets_preserve_existing_delta_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            sim, _ = make_sim(directory)
            try:
                q = sim.robot.arm_positions("left") + .05
                result = sim.robot.apply(RobotCommand(arm_q={"left": q}, gripper_width={"left": .06}))
                np.testing.assert_allclose(result["submitted_action"][3:10], .05)
                self.assertAlmostEqual(result["submitted_action"][10], -.03)
                before = sim.data.ctrl.copy()
                sim.data.qpos[sim.robot.addresses["left_arm_0"]] += .01
                sim.robot.hold()
                np.testing.assert_equal(sim.data.ctrl, before)
            finally:
                sim.close()

    def test_panda_width_and_lift_hold_use_semantic_units(self):
        robot = panda_adapter()
        self.assertIsInstance(robot_config({"name": "panda_omron", "model_path": "unused"}), PandaOmronConfig)
        robot.apply(RobotCommand(gripper_width={"right": .06}))
        self.assertAlmostEqual(robot.data.ctrl[robot.act_ids["gripper0_right_gripper_finger_joint1"]], .03)
        self.assertAlmostEqual(robot.data.ctrl[robot.act_ids["gripper0_right_gripper_finger_joint2"]], -.03)
        before = robot.data.ctrl.copy()
        robot.data.qpos[robot.addresses["mobilebase0_joint_torso_height"]] = .19
        robot.hold()
        np.testing.assert_equal(robot.data.ctrl, before)
        with self.assertRaises(InvalidAction):
            robot.apply(RobotCommand(lift=.21))
        np.testing.assert_equal(robot.data.ctrl, before)
        self.assertNotIn("head", robot.state())

    def test_pick_judges_actual_lift_without_fabricated_head_or_idle_arm(self):
        initial, rows = evidence()
        initial.pop("head")
        initial.pop("idle_arm")
        initial["lift"] = .2
        for row in rows:
            for name in ("head", "idle_arm", "torso", "command_h"):
                row.pop(name)
            row["lift"] = .2
        self.assertEqual(evaluate_pick(rows, initial)["status"], "success")
        rows[-1]["lift"] = .204
        self.assertEqual(evaluate_pick(rows, initial)["reason"], "lift_drift")
