"""Spec preparation and position-servo adapter for robosuite PandaOmron."""

from copy import deepcopy

import mujoco
import numpy as np

from ...scenes.geometry import descendants
from .action import InvalidAction, vector
from .interface import RobotCommand
from .joints import JointMap, check_cameras

# Factory PandaOmron geometry is scaled once when the robot spec is loaded.
SCALE = 0.9


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
    if "front_camera" in config.cameras:
        spec.body(ns + "mobilebase0_base").add_camera(
            name=ns + "front_camera",
            pos=config.observation_position,
            xyaxes=config.observation_xyaxes,
            fovy=config.observation_fovy,
        )
    for i, suffix in enumerate(("forward", "side", "yaw")):
        _servo(
            spec.actuator(ns + "mobilebase0_actuator_mobile_" + suffix),
            config.base_kp[i],
            config.base_kv[i],
            [-1.0e6, 1.0e6],
        )
        spec.joint(ns + "mobilebase0_joint_mobile_" + suffix).frictionloss = 0.0
    _servo(spec.actuator(ns + "mobilebase0_actuator_torso_height"), config.lift_kp, config.lift_kv, [0.0, 0.34])
    spec.joint(ns + "mobilebase0_joint_torso_height").frictionloss = 0.0
    for i, (kp, kv) in enumerate(zip(config.arm_kp, config.arm_kv), 1):
        act = spec.actuator(ns + f"robot0_torq_j{i}")
        force = act.ctrlrange.copy()
        _servo(act, kp, kv, spec.joint(ns + f"robot0_joint{i}").range, force_limits=force)
    support = spec.body(ns + "mobilebase0_support")
    for body in [support, *support.find_all("body")]:
        body.gravcomp = float(config.gravity_compensation)
    for site in spec.sites:
        if site.name.startswith(ns):
            site.rgba[3] = 0.0
    for body in spec.bodies:
        for geom in body.geoms:
            if geom.group == 0:
                geom.material = ""
                geom.rgba = [0, 0, 0, 0]


PANDA_GROUPS = {
    "base": ["mobilebase0_joint_mobile_" + s for s in ("forward", "side", "yaw")],
    "lift": ["mobilebase0_joint_torso_height"],
    "arm": [f"robot0_joint{i}" for i in range(1, 8)],
    "gripper": [f"gripper0_right_finger_joint{i}" for i in (1, 2)],
}
PANDA_ACTUATORS = {
    "base": ["mobilebase0_actuator_mobile_" + s for s in ("forward", "side", "yaw")],
    "lift": ["mobilebase0_actuator_torso_height"],
    "arm": [f"robot0_torq_j{i}" for i in range(1, 8)],
    "gripper": [f"gripper0_right_gripper_finger_joint{i}" for i in (1, 2)],
}


class PandaBody:
    """Joint map, measured state and station aiming shared by both Panda control profiles.

    Holds no control state; each adapter defines its own initialize/apply/hold.
    """

    arms = ("right",)
    base_body = "mobilebase0_base"
    initial_base_field = "base_xyyaw"
    semantic_control = True
    plan_lift_before_close = False
    lock_base_after_close = True

    def __init__(self, model, data, config):
        self.model, self.data, self.config = model, data, config
        self.execution_started = False
        self.joints = JointMap.build(model, config.namespace, PANDA_GROUPS, PANDA_ACTUATORS)
        self.camera_names = check_cameras(model, config.namespace, config.cameras)
        self.lift_target = None

    @property
    def observation_camera(self):
        return self.config.observation_camera

    @property
    def open_grip(self):
        first, second = (self.joints.ids[name] for name in self.joints.groups["gripper"])
        return float(self.model.jnt_range[first, 1] - self.model.jnt_range[second, 0])

    def aim_station(self, initial, anchor):
        self.initialize(initial)
        cid = self.model.camera(self.camera_names[self.observation_camera]).id
        for _ in range(3):
            forward = -self.data.cam_xmat[cid].reshape(3, 3)[:, 2]
            delta = np.asarray(anchor[:2]) - self.data.cam_xpos[cid, :2]
            error = np.arctan2(delta[1], delta[0]) - np.arctan2(forward[1], forward[0])
            initial[self.initial_base_field][2] += float(np.arctan2(np.sin(error), np.cos(error)))
            self.initialize(initial)
        return []

    def control_height(self):
        return self.lift_target

    def manipulation_command(self, side, q, grip, height, base=None):
        return RobotCommand(base_xyyaw=base, arm_q={side: q}, gripper_width={side: grip})

    def group(self, name):
        return self.joints.read(self.data, name)

    def base_pose(self):
        return self.group("base")

    def arm_positions(self, arm):
        return self.group("arm")

    def gripper_width(self, arm):
        a, b = self.group("gripper")
        return float(a - b)

    def tcp_site(self, arm):
        return self.model.site(self.config.namespace + "gripper0_right_grip_site").id

    def finger_bodies(self, arm):
        return tuple(
            descendants(self.model, self.model.body(self.config.namespace + f"gripper0_right_{side}finger").id)
            for side in ("left", "right")
        )

    def holding_state(self, arm):
        return {"lift": float(self.group("lift")[0])}

    def state(self):
        return {
            "base_xyyaw": self.base_pose().tolist(),
            "lift": float(self.group("lift")[0]),
            "arm_q": {a: self.arm_positions(a).tolist() for a in self.arms},
            "gripper_width": {a: self.gripper_width(a) for a in self.arms},
        }

    def initial_state(self, initial=None):
        value = deepcopy(self.config.initial if initial is None else initial)
        value["gripper_width"].setdefault("right", self.open_grip)
        return value


class PandaOmronAdapter(PandaBody):
    """Position servos on base, lift, arm and fingers; commands are semantic absolute targets."""

    def __init__(self, model, data, config):
        super().__init__(model, data, config)
        self.targets = None

    def copy_control_from(self, other):
        self.targets = deepcopy(other.targets)
        self.lift_target = other.lift_target

    def snapshot_control(self):
        return {"targets": self.state_targets()}

    def restore_control(self, saved):
        self.targets = RobotCommand(**saved["targets"])
        self.lift_target = self.targets.lift

    def execution_action(self, command):
        return command

    def update_control(self):
        """Position servos need no per-substep feedback."""

    def record_layout(self):
        fields = [
            "base_x_m",
            "base_y_m",
            "base_yaw_rad",
            "lift_m",
            *[f"panda_joint{i}_rad" for i in range(1, 8)],
            "gripper_width_m",
        ]
        return {
            "robot": self.config.name,
            "state_key": "state",
            "state_fields": fields,
            "action_fields": fields,
            "state_layout": "panda-semantic-v1",
            "action_layout": "panda-absolute-targets-v1",
            "action_convention": "absolute world base / joint targets; m,rad",
            "time_alignment": "state[t], action[t], state[t+1]",
        }

    @staticmethod
    def flatten_command(command):
        return np.r_[
            command["base_xyyaw"], command["lift"], command["arm_q"]["right"], command["gripper_width"]["right"]
        ]

    def record_state(self):
        return self.flatten_command(self.state())

    def record_action(self, command):
        return self.flatten_command(command)

    def initialize(self, initial=None):
        if self.execution_started:
            raise RuntimeError("initialization prohibited after begin")
        value = self.initial_state(initial)
        self.lift_target = float(value["lift"])
        self.targets = RobotCommand(**deepcopy(value))
        groups = self._targets(self.targets)
        self._controls(groups)
        for group, values in groups.items():
            self.joints.write(self.data, group, values)
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
        return {
            "base": vector(base, 3),
            "lift": vector([self.lift_target], 1),
            "arm": vector(arm, 7),
            "gripper": np.array([width / 2, -width / 2]),
        }

    def _controls(self, targets):
        ctrl = self.data.ctrl.copy()
        for group, values in targets.items():
            for name, actuator, value in zip(self.joints.groups[group], self.joints.actuators[group], values):
                self.joints.check_joint(self.model, name, value, tolerance=0.0)
                ctrl[self.joints.act_ids[actuator]] = value
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
        self.targets = RobotCommand(
            targets["base"].copy(),
            self.lift_target,
            {"right": targets["arm"].copy()},
            {"right": float(targets["gripper"][0] * 2)},
        )
        self.data.ctrl[:] = ctrl
        return {"submitted_action": self.state_targets(), "ctrl": ctrl.tolist()}

    def state_targets(self):
        return {
            "base_xyyaw": np.asarray(self.targets.base_xyyaw).tolist(),
            "lift": self.lift_target,
            "arm_q": {"right": np.asarray(self.targets.arm_q["right"]).tolist()},
            "gripper_width": dict(self.targets.gripper_width),
        }
