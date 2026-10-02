"""集中维护关节/执行器映射、初始化、限位和完整实际控制记录。"""
import mujoco
import numpy as np

from .action import InvalidAction, torso_joints, validate_action, vector


def prepare_robot_spec(spec, config):
    """只修改内存中的机器人，不改源 MJCF；恢复 motor 夹爪为位置伺服。"""
    for side in ("left", "right"):
        act = spec.actuator(config.namespace + side + "_finger_act")
        if act is None:
            raise ValueError("robot gripper actuator missing")
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


class RBY1Adapter:
    def __init__(self, model, data, config):
        self.model, self.data, self.config = model, data, config
        self.execution_started = False
        self.groups = {
            "base": ["base_x", "base_y", "base_theta"],
            "torso": [f"torso_{i}" for i in range(6)],
            "head": [f"head_{i}" for i in range(2)],
            "left_arm": [f"left_arm_{i}" for i in range(7)],
            "right_arm": [f"right_arm_{i}" for i in range(7)],
            "left_gripper": ["gripper_finger_l1", "gripper_finger_l2"],
            "right_gripper": ["gripper_finger_r1", "gripper_finger_r2"]}
        self.joints = {name: model.joint(config.namespace + name).id
                       for names in self.groups.values() for name in names}
        self.addresses = {name: int(model.jnt_qposadr[j]) for name, j in self.joints.items()}
        self.actuators = {
            "base": ["base_x_act", "base_y_act", "base_theta_act"],
            "torso": [f"link{i+1}_act" for i in range(6)],
            "head": [f"head_{i}_act" for i in range(2)],
            "left_arm": [f"left_arm_{i+1}_act" for i in range(7)],
            "right_arm": [f"right_arm_{i+1}_act" for i in range(7)],
            "left_gripper": ["left_finger_act"], "right_gripper": ["right_finger_act"]}
        self.act_ids = {name: model.actuator(config.namespace + name).id
                        for names in self.actuators.values() for name in names}
        self.camera_names = {name: config.namespace + name for name in config.cameras}
        for name in self.camera_names.values():
            model.camera(name)  # 必需相机缺失直接报错
        self.fixed_head = self.group("head")

    def group(self, name):
        return np.array([self.data.qpos[self.addresses[n]] for n in self.groups[name]])

    def state(self):
        return {group: self.group(group).tolist() for group in self.groups}

    def _check_joint(self, name, value):
        j = self.joints[name]
        if not np.isfinite(value):
            raise InvalidAction("nonfinite joint target")
        if self.model.jnt_limited[j]:
            lo, hi = self.model.jnt_range[j]
            if not lo - 1e-8 <= value <= hi + 1e-8:
                raise InvalidAction(f"joint limit: {name}={value}, [{lo}, {hi}]")

    def initialize(self, initial=None):
        if self.execution_started:
            raise RuntimeError("robot initialization prohibited during/after continuous execution")
        initial = self.config.initial if initial is None else initial
        if set(initial) != set(self.groups):
            raise ValueError("initialization must specify all robot groups")
        values = {}
        for group, names in self.groups.items():
            size = 1 if group in ("torso", "left_gripper", "right_gripper") else len(names)
            q = vector(initial[group], size)
            if group == "torso":
                if not self.config.torso_limits[0] <= q[0] <= self.config.torso_limits[1]:
                    raise ValueError("illegal initial torso")
                q = torso_joints(q[0])
            elif group.endswith("gripper"):
                if not self.config.gripper_limits[0] - 1e-8 <= q[0] <= self.config.gripper_limits[1] + 1e-8:
                    raise ValueError("illegal initial gripper")
                q = np.array([q[0], -q[0]])
            for name, value in zip(names, q):
                self._check_joint(name, value)
                values[name] = value
        for name, value in values.items():
            self.data.qpos[self.addresses[name]] = value
        self.data.qvel[:] = 0
        self.fixed_head = self.group("head")
        self.hold()
        mujoco.mj_forward(self.model, self.data)

    def _controls(self, targets):
        ctrl = self.data.ctrl.copy()
        for group, qs in targets.items():
            for actuator, q in zip(self.actuators[group], qs):
                idx = self.act_ids[actuator]
                if self.model.actuator_ctrllimited[idx]:
                    lo, hi = self.model.actuator_ctrlrange[idx]
                    if not lo - 1e-8 <= q <= hi + 1e-8:
                        raise InvalidAction(f"actuator limit: {actuator}")
                ctrl[idx] = q
        return ctrl

    def hold(self):
        targets = {g: self.group(g) for g in self.groups}
        for side in ("left", "right"):
            targets[side + "_gripper"] = targets[side + "_gripper"][:1]
        self.data.ctrl[:] = self._controls(targets)

    def apply(self, value, *, fixed_base=False):
        a = validate_action(value, self.config, fixed_base=fixed_base)
        if fixed_base:
            if not hasattr(self, "_fixed_base_target"):
                self._fixed_base_target = self.group("base").copy()
            base_target = self._fixed_base_target.copy()
        else:
            base_target = self.group("base") + a[:3]
        targets = {"base": base_target,
                   "left_arm": self.group("left_arm") + a[3:10],
                   "left_gripper": a[10:11],
                   "right_arm": self.group("right_arm") + a[11:18],
                   "right_gripper": a[18:19],
                   "torso": torso_joints(a[19]), "head": self.fixed_head.copy()}
        for group, qs in targets.items():
            for name, q in zip(self.groups[group], qs):
                self._check_joint(name, q)
        ctrl = self._controls(targets)  # 全部验证完才原子下发，绝不部分修改或静默裁剪
        self.data.ctrl[:] = ctrl
        return {"submitted_action": a.tolist(),
                "joint_targets": {k: v.tolist() for k, v in targets.items()},
                "actuator_names": [self.model.actuator(i).name for i in range(self.model.nu)],
                "ctrl": ctrl.tolist()}

    def neutral_action(self):
        a = np.zeros(20)
        # 绝对量保持已声明的控制目标，不能把微小反馈漂移当成非法新命令。
        a[10] = self.data.ctrl[self.act_ids["left_finger_act"]]
        a[18] = self.data.ctrl[self.act_ids["right_finger_act"]]
        a[19] = self.data.ctrl[self.act_ids["link2_act"]]
        return a
