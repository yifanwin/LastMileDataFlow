"""Named V2 model boundaries; full GPU numerical evidence is an explicit smoke."""
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from lastmile_dataflow.planning.robots.panda_omron import configuration, robot_description, ASSETS, ARM_NAMES, FULL_NAMES
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

    def test_column_spheres_cover_the_cylinders_without_large_overhang(self):
        # MuJoCo columns in the planning link frames (axis x=-0.05, y=0): the fixed column
        # on omron_base above the chassis top, the lift column on panda_link0 above the
        # part that stays inside the fixed column over the torso travel. The former 8-sphere
        # sets overhung by ~14 cm and rejected stations 3-10 cm clear of the table.
        spheres = robot_description()[1]
        columns = {"omron_base": (lambda z: z >= .382, .12, .382, .70),
                   "panda_link0": (lambda z: z < 0, .1125, -.34, 0.)}
        buffer = configuration(panda_adapter())["kinematics"]["collision_sphere_buffer"]
        for link, (member, radius, low, high) in columns.items():
            chosen = [s for s in spheres[link] if member(s["center"][2])]
            centers = np.array([s["center"] for s in chosen])
            radii = np.array([s["radius"] for s in chosen])
            angle = np.linspace(0, 2 * np.pi, 48, endpoint=False)
            ring = np.c_[np.cos(angle), np.sin(angle)]
            xy = np.r_[[[0, 0]], .5 * radius * ring, radius * ring] + [-.05, 0]
            points = np.array([[x, y, z] for x, y in xy for z in np.linspace(low, high, 25)])
            gap = (np.linalg.norm(points[:, None] - centers[None], axis=-1) - radii[None]).min(1)
            self.assertLessEqual(gap.max(), 1e-6, link)
            side = np.linalg.norm(centers[:, :2] - [-.05, 0], axis=1) + radii + buffer - radius
            self.assertLess(side.max(), .03, link)
            self.assertLess((centers[:, 2] + radii + buffer - high).max(), .03, link)
