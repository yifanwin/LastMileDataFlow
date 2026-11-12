"""Robot joint/actuator mapping, initialization and actual 20D control.

apply() validates the complete command before writing ctrl. Native motor and
position control profiles keep their own units and explicit limit projection.
"""

from copy import deepcopy

import mujoco
import numpy as np

from ...scenes.geometry import descendants
from .action import (
    ANGULAR_TOLERANCE_RAD,
    ARM_SLICE,
    BASE_SLICE,
    GRIPPER_INDEX,
    TORSO_INDEX,
    InvalidAction,
    bounded_torso_command,
    project,
    torso_joints,
    validate_action,
    vector,
    yaw_interval,
)
from .interface import RobotCommand
from .joints import JointMap, check_cameras
from .native_actions import NativeControl

NATIVE_PROFILE = "molmobot-native-v1"

# MolmoBot native camera field of view (degrees) on RBY-1.
NATIVE_CAMERA_FOVY = {"head_camera": 139.0, "wrist_camera_l": 58.0, "wrist_camera_r": 58.0}


def prepare_rby1_spec(spec, config):
    """Adapt the in-memory robot spec; native grippers retain motor units."""
    native = config.control_profile == NATIVE_PROFILE
    for side in ("left", "right"):
        act = spec.actuator(config.namespace + side + "_finger_act")
        if act is None:
            raise ValueError("robot gripper actuator missing")
        if native:
            if act.biastype != mujoco.mjtBias.mjBIAS_NONE or not np.allclose(act.ctrlrange, [-100.0, 100.0]):
                raise ValueError("MolmoBot requires original motor grippers [-100,100]")
            continue
        act.gaintype = mujoco.mjtGain.mjGAIN_FIXED
        act.biastype = mujoco.mjtBias.mjBIAS_AFFINE
        act.gainprm[:] = 0
        act.gainprm[0] = config.gripper_kp
        act.biasprm[:] = 0
        act.biasprm[1:3] = [-config.gripper_kp, -config.gripper_kv]
        act.ctrllimited = True
        act.ctrlrange[:] = config.gripper_limits
    for body in spec.bodies:
        if body.name.startswith(config.namespace):
            body.gravcomp = float(config.gravity_compensation)
    if native:
        # Nominal RBY1GoProD455CameraSystem; source XML is never changed.
        for alias, fovy in NATIVE_CAMERA_FOVY.items():
            spec.camera(config.namespace + alias).fovy = fovy


RBY1_GROUPS = {
    "base": ["base_x", "base_y", "base_theta"],
    "torso": [f"torso_{i}" for i in range(6)],
    "head": [f"head_{i}" for i in range(2)],
    "left_arm": [f"left_arm_{i}" for i in range(7)],
    "right_arm": [f"right_arm_{i}" for i in range(7)],
    "left_gripper": ["gripper_finger_l1", "gripper_finger_l2"],
    "right_gripper": ["gripper_finger_r1", "gripper_finger_r2"],
}
RBY1_ACTUATORS = {
    "base": ["base_x_act", "base_y_act", "base_theta_act"],
    "torso": [f"link{i + 1}_act" for i in range(6)],
    "head": [f"head_{i}_act" for i in range(2)],
    "left_arm": [f"left_arm_{i + 1}_act" for i in range(7)],
    "right_arm": [f"right_arm_{i + 1}_act" for i in range(7)],
    "left_gripper": ["left_finger_act"],
    "right_gripper": ["right_finger_act"],
}
STATE22_GROUPS = ("base", "left_arm", "left_gripper", "right_arm", "right_gripper", "torso")


def state22(groups):
    """MolmoBot RBY-1 state layout: base, arms, one finger per gripper, torso joints 1-3."""
    parts = []
    for name in STATE22_GROUPS:
        values = np.asarray(groups[name], float)
        parts.append(values[[1, 2, 3]] if name == "torso" else values[:1] if name.endswith("gripper") else values)
    return np.concatenate(parts).astype(np.float32)


class RBY1Adapter:
    """把“MuJoCo 模型 + 20 维协议”粘合起来的适配器。

    典型使用顺序（由 Simulation 驱动）：
        adapter = RBY1Adapter(model, data, config)
        adapter.initialize(...)        # 仅在准备阶段，写 qpos，同时间接设定 ctrl
        adapter.apply(action)          # 每个控制步：校验 + 原子写 ctrl
    """

    arms = ("left", "right")
    observation_camera = "head_camera"
    base_body = "base"
    initial_base_field = "base"
    open_grip = -0.05
    semantic_control = False
    plan_lift_before_close = True
    lock_base_after_close = False

    def __init__(self, model, data, config):
        self.model, self.data, self.config = model, data, config
        # execution_started 一旦为 True，initialize() 就永久拒绝（连续执行纪律的守卫）
        self.execution_started = False
        self.joints = JointMap.build(model, config.namespace, RBY1_GROUPS, RBY1_ACTUATORS)
        self.camera_names = check_cameras(model, config.namespace, config.cameras)
        # 头部没有动作维度，用初始化时的固定目标锁住
        self.fixed_head = self.group("head")
        self._fixed_base_target = None
        self._hold_targets = None
        self.native = NativeControl() if config.control_profile == NATIVE_PROFILE else None

    def group(self, name):
        """读取某个组当前的实测关节值（从 data.qpos 里取）。"""
        return self.joints.read(self.data, name)

    def state(self):
        """把全部组的实测状态打包成 dict，供记录和观察使用。"""
        return {group: self.group(group).tolist() for group in self.joints.groups}

    def initialize(self, initial=None):
        """Set all robot groups before execution; validate first, then write qpos and hold."""
        if self.execution_started:
            raise RuntimeError("robot initialization prohibited during/after continuous execution")
        initial = self.config.initial if initial is None else initial
        if set(initial) != set(self.joints.groups):
            raise ValueError("initialization must specify all robot groups")
        values = {}
        for group, names in self.joints.groups.items():
            # 躯干/夹爪在配置里是“1 个数”，基座/双臂/头是“多关节”
            size = 1 if group in ("torso", "left_gripper", "right_gripper") else len(names)
            q = vector(initial[group], size)
            if group == "torso":
                if not self.config.torso_limits[0] <= q[0] <= self.config.torso_limits[1]:
                    raise ValueError("illegal initial torso")
                q = torso_joints(q[0])  # 一维 h → 6 个联动关节
            elif group.endswith("gripper"):
                if not self.config.gripper_limits[0] - 1e-8 <= q[0] <= self.config.gripper_limits[1] + 1e-8:
                    raise ValueError("illegal initial gripper")
                q = np.array([q[0], -q[0]])  # 两指对称张开
            for name, value in zip(names, q):
                self.joints.check_joint(self.model, name, value)
                values[name] = value
        for name, value in values.items():
            self.data.qpos[self.joints.addresses[name]] = value
        self.data.qvel[:] = 0
        self.fixed_head = self.group("head")
        self._hold_targets = None
        self.hold()
        mujoco.mj_forward(self.model, self.data)

    def controls(self, targets):
        """Build a new ctrl array without mutating live controls; reject actuator violations."""
        ctrl = self.data.ctrl.copy()
        for group, qs in targets.items():
            for actuator, q in zip(self.joints.actuators[group], qs):
                idx = self.joints.act_ids[actuator]
                if self.model.actuator_ctrllimited[idx]:
                    lo, hi = self.model.actuator_ctrlrange[idx]
                    if not lo - 1e-8 <= q <= hi + 1e-8:
                        raise InvalidAction(f"actuator limit: {actuator}")
                ctrl[idx] = q
        return ctrl

    def hold(self):
        """把当前实测关节值设为控制目标（原地保持，不产生新运动）。"""
        if self._hold_targets is None:
            self._hold_targets = {g: self.group(g) for g in self.joints.groups}
            for side in self.arms:
                self._hold_targets[side + "_gripper"] = self._hold_targets[side + "_gripper"][:1]
        self.data.ctrl[:] = self.controls(self._hold_targets)

    def update_control(self):
        """Position servos need no per-substep feedback."""

    def apply(self, value, *, fixed_base=False):
        """Map one validated action to joint targets, then atomically write ctrl.

        Fixed-base mode latches the first measured base target."""
        if isinstance(value, RobotCommand):
            value = self.execution_action(value)
        if self.native is not None:
            command = self.native.apply(self, value, fixed_base=fixed_base)
            self._hold_targets = {
                g: np.asarray(q).copy() for g, q in (command["joint_targets"] | command["motor_targets"]).items()
            }
            return command
        a = validate_action(value, self.config, fixed_base=fixed_base)
        if fixed_base:
            if self._fixed_base_target is None:
                self._fixed_base_target = self.group("base").copy()
            base_target = self._fixed_base_target.copy()
        else:
            base_target = self.group("base") + a[BASE_SLICE]
        targets = {
            "base": base_target,
            "left_arm": self.group("left_arm") + a[ARM_SLICE["left"]],
            "left_gripper": a[GRIPPER_INDEX["left"] : GRIPPER_INDEX["left"] + 1],
            "right_arm": self.group("right_arm") + a[ARM_SLICE["right"]],
            "right_gripper": a[GRIPPER_INDEX["right"] : GRIPPER_INDEX["right"] + 1],
            "torso": torso_joints(a[TORSO_INDEX]),
            "head": self.fixed_head.copy(),
        }
        for group, qs in targets.items():
            for name, q in zip(self.joints.groups[group], qs):
                self.joints.check_joint(self.model, name, q)
        ctrl = self.controls(targets)  # 全部验证完才原子下发，绝不部分修改或静默裁剪
        self.data.ctrl[:] = ctrl
        self._hold_targets = {g: qs.copy() for g, qs in targets.items()}
        return {
            "submitted_action": a.tolist(),
            "joint_targets": {k: v.tolist() for k, v in targets.items()},
            "actuator_names": [self.model.actuator(i).name for i in range(self.model.nu)],
            "ctrl": ctrl.tolist(),
        }

    def neutral_action(self):
        """构造一条“什么都不改变”的 20 维动作。

        绝对量（夹爪、躯干）必须填当前实际控制目标，而不是 0——
        否则会被当成“把夹爪/躯干猛地拉到 0”的非法新命令。
        """
        a = np.zeros(20)
        for side, index in GRIPPER_INDEX.items():
            a[index] = self.data.ctrl[self.joints.act_ids[side + "_finger_act"]]
        a[TORSO_INDEX] = self.data.ctrl[self.joints.act_ids["link2_act"]]
        return a

    def _legal_initial_yaw(self, value, adjustments):
        return project(value, yaw_interval(self), ANGULAR_TOLERANCE_RAD, "base_theta", adjustments)

    def _head_axis(self, camera):
        return -self.data.cam_xmat[camera].reshape(3, 3)[:, 2]

    def _aim_yaw(self, initial, anchor, camera, adjustments):
        base = initial[self.initial_base_field]
        base[2] = self._legal_initial_yaw(base[2], adjustments)
        self.initialize(initial)
        # Camera -Z is forward; mounting offset is not assumed equal to base +X.
        for _ in range(3):
            forward = self._head_axis(camera)
            delta = np.asarray(anchor[:2]) - self.data.cam_xpos[camera, :2]
            error = np.arctan2(delta[1], delta[0]) - np.arctan2(forward[1], forward[0])
            base[2] = self._legal_initial_yaw(float((base[2] + error + np.pi) % (2 * np.pi) - np.pi), adjustments)
            self.initialize(initial)

    def _aim_pitch(self, initial, anchor, camera):
        """Solve pitch from the ACTUAL optical axis; the short numerical derivative handles mount sign."""
        low, high = self.model.jnt_range[self.joints.ids["head_1"]]
        for _ in range(2):
            forward = self._head_axis(camera)
            inclination = float(np.arctan2(forward[2], np.linalg.norm(forward[:2])))
            delta = np.asarray(anchor) - self.data.cam_xpos[camera]
            desired = float(np.arctan2(delta[2], np.linalg.norm(delta[:2])))
            pitch = initial["head"][1]
            probe = min(high, pitch + 0.001)
            if probe == pitch:
                probe = max(low, pitch - 0.001)
            initial["head"][1] = float(probe)
            self.initialize(initial)
            axis = self._head_axis(camera)
            slope = (float(np.arctan2(axis[2], np.linalg.norm(axis[:2]))) - inclination) / (probe - pitch)
            corrected = pitch + (desired - inclination) / slope if abs(slope) > 0.1 else pitch
            initial["head"][1] = float(np.clip(corrected, low, high))
            self.initialize(initial)

    def aim_station(self, initial, anchor):
        adjustments = []
        initial["head"] = [0.0, 0.6]
        camera = self.model.camera(self.camera_names["head_camera"]).id
        self._aim_yaw(initial, anchor, camera, adjustments)
        self._aim_pitch(initial, anchor, camera)
        return adjustments

    def copy_control_from(self, other):
        self.fixed_head = other.fixed_head.copy()
        self._hold_targets = deepcopy(other._hold_targets)

    def snapshot_control(self):
        held = self._hold_targets
        return {
            "fixed_head": self.fixed_head.tolist(),
            "hold_targets": None if held is None else {k: v.tolist() for k, v in held.items()},
        }

    def restore_control(self, saved):
        self.fixed_head = np.asarray(saved["fixed_head"])
        held = saved.get("hold_targets")
        self._hold_targets = None if held is None else {k: np.asarray(v) for k, v in held.items()}

    def control_height(self):
        return bounded_torso_command(float(self.neutral_action()[TORSO_INDEX]), self.config.torso_limits)

    def manipulation_command(self, side, q, grip, height, base=None):
        a = self.neutral_action()
        a[ARM_SLICE[side]] = np.asarray(q) - self.arm_positions(side)
        a[GRIPPER_INDEX[side]] = grip
        a[TORSO_INDEX] = height
        for idle in self.arms:
            if idle != side:
                a[ARM_SLICE[idle]] = self._hold_targets[idle + "_arm"] - self.arm_positions(idle)
        if base is not None:
            a[BASE_SLICE] = np.asarray(base) - self.base_pose()
        return a

    def record_layout(self):
        return {
            "robot": self.config.name,
            "state_key": "state22",
            "state_layout": "state22",
            "action_layout": "rby1-20d",
            "action_size": 20,
            "state_size": 22,
        }

    def record_state(self):
        return state22({g: self.group(g) for g in STATE22_GROUPS})

    def record_action(self, command):
        return np.asarray(command, float)

    def base_pose(self):
        return self.group("base")

    def arm_positions(self, arm):
        return self.group(arm + "_arm")

    def gripper_width(self, arm):
        a, b = self.group(arm + "_gripper")
        return float(b - a)

    def tcp_site(self, arm):
        return self.model.site(self.config.namespace + "ee_site_" + arm[0]).id

    def finger_bodies(self, arm):
        return tuple(
            descendants(self.model, self.model.body(self.config.namespace + f"ee_finger_{arm[0]}{i}").id)
            for i in (1, 2)
        )

    def holding_state(self, arm):
        idle = "right" if arm == "left" else "left"
        return {
            "head": self.group("head").tolist(),
            "idle_arm": self.group(idle + "_arm").tolist(),
            "torso": self.group("torso").tolist(),
        }

    def execution_action(self, command):
        """Semantic command -> 20D delta action relative to the measured state."""
        if command.lift is not None:
            raise InvalidAction("RBY-1 torso coupling is not a lift position")
        action = self.neutral_action()
        if command.base_xyyaw is not None:
            action[BASE_SLICE] = np.asarray(command.base_xyyaw) - self.base_pose()
        for arm, q in command.arm_q.items():
            action[ARM_SLICE[arm]] = np.asarray(q) - self.arm_positions(arm)
        for arm, width in command.gripper_width.items():
            if self.native is not None:
                raise InvalidAction("native motor command requires its original units")
            action[GRIPPER_INDEX[arm]] = -float(width) / 2
        return action
