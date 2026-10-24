"""PandaOmron V2 model: named kinematics, movable fingers and fixed/mobile planning."""
from pathlib import Path
from copy import deepcopy
from functools import lru_cache
import xml.etree.ElementTree as ET

import numpy as np
import yaml

from ..scenes.geometry import body_pose


ASSETS = Path(__file__).parent / "assets/panda_omron"
ARM_NAMES = tuple(f"panda_joint{i}" for i in range(1, 8))
FINGER_NAMES = ("panda_finger_joint1", "panda_finger_joint2")
BASE_NAMES = ("base_x", "base_y", "base_theta")
FULL_NAMES = (*BASE_NAMES, "torso_height", *ARM_NAMES, *FINGER_NAMES)
TCP = "panda_grip_site"


@lru_cache(maxsize=1)
def robot_description():
    return ET.parse(ASSETS / 'model.urdf'), yaml.safe_load((ASSETS / 'spheres.yml').read_text())['collision_spheres']


def configuration(robot, *, fixed=True, mobile=False):
    spheres = deepcopy(robot_description()[1])
    spheres["attached_object_right"] = [{"center": [0., 0., 0.], "radius": -.01}] * 40
    links = list(spheres)
    ignore = {
        "omron_base": ["panda_link0", "panda_link1"],
        **{f"panda_link{i}": [f"panda_link{i+1}", f"panda_link{i+2}"] for i in range(6)},
        "panda_link6": ["panda_link7", "panda_gripper"],
        "panda_link7": ["panda_gripper", "panda_finger1", "panda_finger2"],
        "panda_gripper": ["panda_finger1", "panda_finger2", "attached_object_right"],
        "panda_finger1": ["panda_finger2", "attached_object_right"],
        "panda_finger2": ["attached_object_right"]}
    q = robot.arm_positions("right").tolist()
    fingers = robot.group("gripper").tolist()
    if mobile:
        names, values = (*BASE_NAMES, *ARM_NAMES), [0., 0., 0., *q]
    elif fixed:
        names, values = ARM_NAMES, q
    else:
        names = FULL_NAMES
        values = [*robot.base_pose(), float(robot.group("lift")[0]), *q, *fingers]
    locks = dict(zip(("base_x", "base_y", "base_theta", "torso_height", *FINGER_NAMES),
                     [0., 0., 0., robot.lift_target, *fingers])) if fixed else {}
    if mobile:
        locks = {"torso_height": robot.lift_target, **dict(zip(FINGER_NAMES, fingers))}
    return {"kinematics": {
        "urdf_path": str((ASSETS / "model.urdf").resolve()), "base_link": "world",
        "tool_frames": [TCP], "load_tool_frames_with_mesh": False,
        "collision_link_names": links, "collision_spheres": spheres,
        "collision_sphere_buffer": .002, "self_collision_ignore": ignore,
        "self_collision_buffer": {}, "lock_joints": locks,
        "extra_links": {"attached_object_right": {
            "parent_link_name": TCP, "link_name": "attached_object_right",
            "joint_name": "attached_object_joint", "joint_type": "FIXED",
            "fixed_transform": [0., 0., 0., 1., 0., 0., 0.]}},
        "cspace": {"joint_names": list(names), "default_joint_position": list(values),
                   "null_space_weight": [1.] * len(names),
                   "cspace_distance_weight": [1.] * len(names),
                   "max_acceleration": 5., "max_jerk": 100.}}}


class PlanningModel:
    movable_base = False
    fk_limits = (.005, .01)
    fk_check_required = True
    tcp_link = TCP
    attached_link = "attached_object_right"
    contact_links = ["panda_finger1", "panda_finger2"]

    def __init__(self, sim, config, side, path, workspace):
        self.sim = sim
        if side not in sim.robot.arms:
            raise ValueError("unknown arm")
        self.movable_base = getattr(config, "operation_base_mode", "fixed") != "fixed"
        self.names = (*BASE_NAMES, *ARM_NAMES) if self.movable_base else ARM_NAMES
        self.base = body_pose(sim, sim.model.body(sim.robot.config.namespace+"mobilebase0_base").id)
        self.base[:3, 3] = sim.data.site_xpos[sim.model.site(sim.robot.config.namespace+"base_site").id]
        self.base_target = sim.robot.base_pose().copy()
        self.reference_yaw = float(self.base_target[2])
        self.cfg = configuration(sim.robot, mobile=self.movable_base)
        self.bounds = {}
        self.coupling = {"base_locked": True, "lift_locked_m": sim.robot.lift_target}
        self.h_step_limit = 0.
        if self.movable_base:
            center, radius = workspace
            local = np.linalg.solve(self.base, np.r_[center[:2], self.base[2,3], 1.])[:2]
            travel = config.local_base_radius_m
            self.bounds = {"base_x": (max(-travel, local[0]-radius), min(travel, local[0]+radius)),
                           "base_y": (max(-travel, local[1]-radius), min(travel, local[1]+radius)),
                           "base_theta": (-np.pi, np.pi)}
            urdf = deepcopy(robot_description()[0])
            for name, (low, high) in self.bounds.items():
                limit = urdf.getroot().find(f"joint[@name='{name}']/limit")
                limit.set("lower", str(low)); limit.set("upper", str(high))
            output = Path(path)/"mobile.urdf"
            urdf.write(output)
            self.cfg["kinematics"]["urdf_path"] = str(output.resolve())
            self.cfg["kinematics"]["cspace"]["cspace_distance_weight"] = [3.,3.,2., *([1.]*7)]
            self.coupling["base_locked"] = False

    def arm_target(self, point):
        return np.asarray(point)[3:] if self.movable_base else np.asarray(point)

    def height_target(self, point):
        return self.sim.robot.lift_target

    def current_joints(self):
        q = self.sim.robot.arm_positions("right")
        if not self.movable_base:
            return q
        pose = self.sim.robot.base_pose()
        local = np.linalg.solve(self.base, np.r_[pose[:2], self.base[2,3], 1.])
        return np.r_[local[:2], pose[2]-self.reference_yaw, q]

    def base_target_world(self, point):
        if not self.movable_base:
            return self.base_target.copy()
        world = self.base @ np.r_[point[:2], 0., 1.]
        return np.r_[world[:2], self.reference_yaw+point[2]]
