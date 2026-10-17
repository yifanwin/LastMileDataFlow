"""Named V2 model boundaries; full GPU numerical evidence is an explicit smoke."""
import unittest
import xml.etree.ElementTree as ET

from lastmile_dataflow.planning.panda_omron import configuration, ASSETS, ARM_NAMES, FULL_NAMES
from tests.runtime.test_robot_interface import panda_adapter


class PandaPlanningTests(unittest.TestCase):
    def test_fixed_pick_locks_base_lift_and_measured_fingers(self):
        robot = panda_adapter()
        cfg = configuration(robot)["kinematics"]
        self.assertEqual(tuple(cfg["cspace"]["joint_names"]), ARM_NAMES)
        locks = cfg["lock_joints"]
        self.assertEqual([locks[n] for n in ("base_x", "base_y", "base_theta")], [0, 0, 0])
        self.assertEqual(locks["torso_height"], .2)
        self.assertEqual(locks["panda_finger_joint1"], .04)
        self.assertEqual(locks["panda_finger_joint2"], -.04)
        full = configuration(robot, fixed=False)["kinematics"]
        self.assertEqual(tuple(full["cspace"]["joint_names"]), FULL_NAMES)
        self.assertEqual(full["lock_joints"], {})

    def test_moving_fingers_have_kinematics_and_collision_geometry(self):
        robot = panda_adapter()
        root = ET.parse(ASSETS / "model.urdf")
        cfg = configuration(robot)["kinematics"]
        for i in (1, 2):
            joint = root.find(f"joint[@name='panda_finger_joint{i}']")
            self.assertEqual(joint.get("type"), "prismatic")
            self.assertEqual(joint.find("axis").get("xyz"), "0 1 0")
            self.assertIn(f"panda_finger{i}", cfg["collision_link_names"])
            self.assertTrue(cfg["collision_spheres"][f"panda_finger{i}"])
