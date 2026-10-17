"""Factory assets and position-servo adapter for robosuite PandaOmron."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from .action import InvalidAction, vector
from .interface import RobotCommand


PIVOT = np.array([-.21, 0., 0.])


def factory_spec(robosuite_root, namespace="robot_0/"):
    """Use the real local factory, without depending on its task environments."""
    path = str(Path(robosuite_root).resolve())
    sys.path.insert(0, path)
    try:
        from robosuite.models.bases import robot_base_factory
        from robosuite.models.grippers import gripper_factory
        from robosuite.models.robots import create_robot
        robot = create_robot("PandaOmron", idn=0)
        robot.add_base(robot_base_factory(robot.default_base, idn=0))
        robot.update_joints()
        robot.update_actuators()
        robot.add_gripper(gripper_factory(robot.default_gripper["right"], idn="0_right"),
                          robot.eef_name["right"])
        spec = mujoco.MjSpec.from_string(robot.get_xml())
    finally:
        sys.path.remove(path)
    base = spec.body("mobilebase0_base")
    base.add_site(name="base_site", pos=PIVOT, size=[.005, 0, 0], rgba=[0, 0, 0, 0])
    base.add_camera(name="overview", pos=[-1.3, 0., 2.2],
                    quat=[.653288, .2705823, -.2705823, -.653288], fovy=60.)
    scene = mujoco.MjSpec()
    scene.worldbody.add_frame(pos=-PIVOT).attach_body(spec.body("robot0_base"), namespace, "")
    return scene


def export_factory(robosuite_root, output, namespace="robot_0/"):
    """Export referenced meshes plus XML. Repeated exports require a new directory."""
    spec = factory_spec(robosuite_root, namespace)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    root = ET.fromstring(spec.to_xml())
    for asset in root.find("asset"):
        source = asset.get("file")
        if source:
            source = Path(source)
            target = Path("meshes") / (asset.get("name").replace("/", "_") + source.suffix)
            (output / target).parent.mkdir(exist_ok=True)
            shutil.copyfile(source, output / target)
            asset.set("file", str(target))
    path = output / "model.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    shutil.copyfile(Path(robosuite_root) / "LICENSE", output / "LICENSE")
    return path


def _servo(act, kp, kv, limits, *, force_limits=None):
    act.gaintype = mujoco.mjtGain.mjGAIN_FIXED
    act.biastype = mujoco.mjtBias.mjBIAS_AFFINE
    act.dyntype = mujoco.mjtDyn.mjDYN_NONE
    act.gainprm[:] = 0
    act.biasprm[:] = 0
    act.gainprm[0] = kp
    act.biasprm[1:3] = [-kp, -kv]
    act.ctrllimited = True
    act.ctrlrange = limits
    if force_limits is not None:
        act.forcelimited = True
        act.forcerange = force_limits


def prepare_spec(spec, config):
    ns = config.namespace
    for i, suffix in enumerate(("forward", "side", "yaw")):
        _servo(spec.actuator(ns + "mobilebase0_actuator_mobile_" + suffix),
               config.base_kp[i], config.base_kv[i], [-1.e6, 1.e6])
        spec.joint(ns + "mobilebase0_joint_mobile_" + suffix).frictionloss = 0.
    _servo(spec.actuator(ns + "mobilebase0_actuator_torso_height"),
           config.lift_kp, config.lift_kv, [0., .34])
    spec.joint(ns + "mobilebase0_joint_torso_height").frictionloss = 0.
    for i, (kp, kv) in enumerate(zip(config.arm_kp, config.arm_kv), 1):
        act = spec.actuator(ns + f"robot0_torq_j{i}")
        force = act.ctrlrange.copy()
        _servo(act, kp, kv, spec.joint(ns + f"robot0_joint{i}").range, force_limits=force)
    support = spec.body(ns + "mobilebase0_support")
    for body in [support, *support.find_all("body")]:
        body.gravcomp = float(config.gravity_compensation)
    for site in spec.sites:
        if site.name.startswith(ns):
            site.rgba[3] = 0.
    for body in spec.bodies:
        for geom in body.geoms:
            if geom.group == 0:
                geom.material = ""
                geom.rgba = [0, 0, 0, 0]


class PandaOmronAdapter:
    arms = ("right",)

    def __init__(self, model, data, config):
        self.model, self.data, self.config = model, data, config
        self.execution_started = False
        self.groups = {
            "base": ["mobilebase0_joint_mobile_" + s for s in ("forward", "side", "yaw")],
            "lift": ["mobilebase0_joint_torso_height"],
            "arm": [f"robot0_joint{i}" for i in range(1, 8)],
            "gripper": [f"gripper0_right_finger_joint{i}" for i in (1, 2)]}
        self.actuators = {
            "base": ["mobilebase0_actuator_mobile_" + s for s in ("forward", "side", "yaw")],
            "lift": ["mobilebase0_actuator_torso_height"],
            "arm": [f"robot0_torq_j{i}" for i in range(1, 8)],
            "gripper": [f"gripper0_right_gripper_finger_joint{i}" for i in (1, 2)]}
        self.addresses = {n: int(model.jnt_qposadr[model.joint(config.namespace+n).id])
                          for names in self.groups.values() for n in names}
        self.act_ids = {n: model.actuator(config.namespace+n).id
                        for names in self.actuators.values() for n in names}
        self.camera_names = {n: config.namespace+n for n in config.cameras}
        for name in self.camera_names.values():
            model.camera(name)
        self.targets = None
        self.lift_target = None

    def planning_model(self, *args):
        from ...planning.panda_omron import PlanningModel
        return PlanningModel(*args)

    def group(self, name):
        return np.array([self.data.qpos[self.addresses[n]] for n in self.groups[name]])

    def base_pose(self):
        return self.group("base")

    def arm_positions(self, arm):
        if arm not in self.arms:
            raise ValueError(f"unknown arm: {arm}")
        return self.group("arm")

    def gripper_width(self, arm):
        self.arm_positions(arm)
        a, b = self.group("gripper")
        return float(a-b)

    def tcp_site(self, arm):
        self.arm_positions(arm)
        return self.model.site(self.config.namespace+"gripper0_right_grip_site").id

    def finger_bodies(self, arm):
        self.arm_positions(arm)
        from ...scenes.geometry import descendants
        return tuple(descendants(self.model, self.model.body(self.config.namespace +
                     f"gripper0_right_{side}finger").id) for side in ("left", "right"))

    def holding_state(self, arm):
        return {"lift": float(self.group("lift")[0])}

    def state(self):
        return {"base_xyyaw": self.base_pose().tolist(), "lift": float(self.group("lift")[0]),
                "arm_q": {a: self.arm_positions(a).tolist() for a in self.arms},
                "gripper_width": {a: self.gripper_width(a) for a in self.arms}}

    def initialize(self, initial=None):
        if self.execution_started:
            raise RuntimeError("initialization prohibited after begin")
        value = self.config.initial if initial is None else initial
        self.lift_target = float(value["lift"])
        self.targets = RobotCommand(**deepcopy(value))
        groups = self._targets(self.targets)
        self._controls(groups)
        for group, values in groups.items():
            for name, value in zip(self.groups[group], values):
                self.data.qpos[self.addresses[name]] = value
        self.data.qvel[:] = 0
        self.hold()
        mujoco.mj_forward(self.model, self.data)

    def _targets(self, command):
        if not isinstance(command, RobotCommand):
            raise InvalidAction("PandaOmron requires a semantic RobotCommand")
        if command.lift is not None and command.lift != self.lift_target:
            raise InvalidAction("lift is held at its initialization target")
        if set(command.arm_q) - set(self.arms) or set(command.gripper_width) - set(self.arms):
            raise InvalidAction("unknown arm")
        held = self.targets
        base = command.base_xyyaw if command.base_xyyaw is not None else held.base_xyyaw
        arm = command.arm_q.get("right", held.arm_q["right"])
        width = command.gripper_width.get("right", held.gripper_width["right"])
        width = vector([width], 1)[0]
        return {"base": vector(base, 3), "lift": vector([self.lift_target], 1),
                "arm": vector(arm, 7), "gripper": np.array([width/2, -width/2])}

    def _controls(self, targets):
        ctrl = self.data.ctrl.copy()
        for group, values in targets.items():
            for name, actuator, value in zip(self.groups[group], self.actuators[group], values):
                j = self.model.joint(self.config.namespace + name).id
                if self.model.jnt_limited[j]:
                    lo, hi = self.model.jnt_range[j]
                    if not lo <= value <= hi:
                        raise InvalidAction(f"joint limit: {name}")
                ctrl[self.act_ids[actuator]] = value
        return ctrl

    def hold(self):
        if self.targets is None:
            raise RuntimeError("initialize before hold")
        self.data.ctrl[:] = self._controls(self._targets(self.targets))

    def apply(self, command, *, fixed_base=False):
        targets = self._targets(command)
        if fixed_base and not np.array_equal(targets["base"], self.targets.base_xyyaw):
            raise InvalidAction("fixed-base trial prohibits base motion")
        ctrl = self._controls(targets)
        self.targets = RobotCommand(targets["base"].copy(), self.lift_target,
                                    {"right": targets["arm"].copy()},
                                    {"right": float(targets["gripper"][0]*2)})
        self.data.ctrl[:] = ctrl
        return {"submitted_action": self.state_targets(), "ctrl": ctrl.tolist()}

    def state_targets(self):
        return {"base_xyyaw": np.asarray(self.targets.base_xyyaw).tolist(), "lift": self.lift_target,
                "arm_q": {"right": np.asarray(self.targets.arm_q["right"]).tolist()},
                "gripper_width": dict(self.targets.gripper_width)}
