"""Robot joint/actuator mapping, initialization and actual 20D control.

apply() validates the complete command before writing ctrl. Native motor and
position control profiles keep their own units and explicit limit projection.
"""
import mujoco
import numpy as np

from .action import InvalidAction, torso_joints, validate_action, vector


def _prepare_rby1_spec(spec, config):
    """Adapt the in-memory robot spec; native grippers retain motor units."""
    native = config.control_profile == 'molmobot-native-v1'
    for side in ("left", "right"):
        act = spec.actuator(config.namespace + side + "_finger_act")
        if act is None:
            raise ValueError("robot gripper actuator missing")
        if native:
            if (act.biastype != mujoco.mjtBias.mjBIAS_NONE or
                    not np.allclose(act.ctrlrange, [-100., 100.])):
                raise ValueError('MolmoBot requires original motor grippers [-100,100]')
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
        for alias, fovy in [('head_camera', 139.), ('wrist_camera_l', 58.), ('wrist_camera_r', 58.)]:
            spec.camera(config.namespace + alias).fovy = fovy


class RBY1Adapter:
    """把“MuJoCo 模型 + 20 维协议”粘合起来的适配器。

    典型使用顺序（由 Simulation 驱动）：
        adapter = RBY1Adapter(model, data, config)
        adapter.initialize(...)        # 仅在准备阶段，写 qpos，同时间接设定 ctrl
        adapter.apply(action)          # 每个控制步：校验 + 原子写 ctrl
    """

    def __init__(self, model, data, config):
        self.model, self.data, self.config = model, data, config
        # execution_started 一旦为 True，initialize() 就永久拒绝（连续执行纪律的守卫）
        self.execution_started = False
        # 下面三个映射把“人类可读的组名”翻译成模型里的关节/执行器名字
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
        # qpos 地址：把关节值写进 data.qpos 需要这个偏移量
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
            model.camera(name)  # 必需相机缺失直接报错：不静默降级成两相机
        # 头部没有动作维度，用初始化时的固定目标锁住
        self.fixed_head = self.group("head")
        self._fixed_base_target = None
        self._native_stationary_targets = {}
        self._native_stationary = {}

    def group(self, name):
        """读取某个组当前的实测关节值（从 data.qpos 里取）。"""
        return np.array([self.data.qpos[self.addresses[n]] for n in self.groups[name]])

    def state(self):
        """把全部组的实测状态打包成 dict，供记录和观察使用。"""
        return {group: self.group(group).tolist() for group in self.groups}

    def _check_joint(self, name, value):
        """单关节检查：有限值 + 若模型声明了限位则不越界。"""
        j = self.joints[name]
        if not np.isfinite(value):
            raise InvalidAction("nonfinite joint target")
        if self.model.jnt_limited[j]:
            lo, hi = self.model.jnt_range[j]
            if not lo - 1e-8 <= value <= hi + 1e-8:
                raise InvalidAction(f"joint limit: {name}={value}, [{lo}, {hi}]")

    def initialize(self, initial=None):
        """Set all robot groups before execution; validate first, then write qpos and hold."""
        if self.execution_started:
            raise RuntimeError("robot initialization prohibited during/after continuous execution")
        initial = self.config.initial if initial is None else initial
        if set(initial) != set(self.groups):
            raise ValueError("initialization must specify all robot groups")
        values = {}
        for group, names in self.groups.items():
            # 躯干/夹爪在配置里是“1 个数”，基座/双臂/头是“多关节”
            size = 1 if group in ("torso", "left_gripper", "right_gripper") else len(names)
            q = vector(initial[group], size)
            if group == "torso":
                if not self.config.torso_limits[0] <= q[0] <= self.config.torso_limits[1]:
                    raise ValueError("illegal initial torso")
                q = torso_joints(q[0])          # 一维 h → 6 个联动关节
            elif group.endswith("gripper"):
                if not self.config.gripper_limits[0] - 1e-8 <= q[0] <= self.config.gripper_limits[1] + 1e-8:
                    raise ValueError("illegal initial gripper")
                q = np.array([q[0], -q[0]])      # 两指对称张开
            for name, value in zip(names, q):
                self._check_joint(name, value)
                values[name] = value
        for name, value in values.items():
            self.data.qpos[self.addresses[name]] = value
        self.data.qvel[:] = 0
        self.fixed_head = self.group("head")
        self._hold_targets = None
        self.hold()
        mujoco.mj_forward(self.model, self.data)

    def _controls(self, targets):
        """Build a new ctrl array without mutating live controls; reject actuator violations."""
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
        """把当前实测关节值设为控制目标（原地保持，不产生新运动）。"""
        if getattr(self, "_hold_targets", None) is None:
            self._hold_targets = {g: self.group(g) for g in self.groups}
            for side in ("left", "right"):
                self._hold_targets[side + "_gripper"] = self._hold_targets[side + "_gripper"][:1]
        self.data.ctrl[:] = self._controls(self._hold_targets)

    def apply(self, value, *, fixed_base=False):
        """Map one validated action to joint targets, then atomically write ctrl.
        
        Fixed-base mode latches the first measured base target."""
        from .interface import RobotCommand
        if isinstance(value, RobotCommand):
            value = self._semantic_action(value)
        if self.config.control_profile == 'molmobot-native-v1':
            from .native_actions import apply_native
            command = apply_native(self, value, fixed_base=fixed_base)
            self._hold_targets = {g: np.asarray(q).copy() for g, q in
                                  (command["joint_targets"] | command["motor_targets"]).items()}
            return command
        a = validate_action(value, self.config, fixed_base=fixed_base)
        if fixed_base:
            if self._fixed_base_target is None:
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
        self._hold_targets = {g: qs.copy() for g, qs in targets.items()}
        return {"submitted_action": a.tolist(),
                "joint_targets": {k: v.tolist() for k, v in targets.items()},
                "actuator_names": [self.model.actuator(i).name for i in range(self.model.nu)],
                "ctrl": ctrl.tolist()}

    def neutral_action(self):
        """构造一条“什么都不改变”的 20 维动作。

        绝对量（夹爪、躯干）必须填当前实际控制目标，而不是 0——
        否则会被当成“把夹爪/躯干猛地拉到 0”的非法新命令。
        """
        a = np.zeros(20)
        # 绝对量保持已声明的控制目标，不能把微小反馈漂移当成非法新命令。
        a[10] = self.data.ctrl[self.act_ids["left_finger_act"]]
        a[18] = self.data.ctrl[self.act_ids["right_finger_act"]]
        a[19] = self.data.ctrl[self.act_ids["link2_act"]]
        return a


    def aim_station(self, initial, anchor):
        yaw_adjustments=[]
        def legal_initial_yaw(value):
            from .action_limits import project, yaw_interval, ANGULAR_TOLERANCE_RAD
            return project(value,yaw_interval(self),ANGULAR_TOLERANCE_RAD,'base_theta',yaw_adjustments)
        initial[self.initial_base_field][2]=legal_initial_yaw(initial[self.initial_base_field][2])
        initial['head'] = [0., .6]
        self.initialize(initial)
        cid = self.model.camera(self.camera_names['head_camera']).id
        # Camera -Z is forward; mounting offset is not assumed equal to base +X.
        for _ in range(3):
            forward = -self.data.cam_xmat[cid].reshape(3,3)[:,2]
            delta = np.asarray(anchor[:2])-self.data.cam_xpos[cid,:2]
            error = np.arctan2(delta[1],delta[0])-np.arctan2(forward[1],forward[0])
            initial[self.initial_base_field][2] = legal_initial_yaw(float((initial[self.initial_base_field][2]+error+np.pi)%(2*np.pi)-np.pi))
            self.initialize(initial)
        joint = self.joints['head_1']
        low,high = self.model.jnt_range[joint]
        # Solve pitch from the ACTUAL optical axis, avoiding an expensive 25-pose scan
        # per lattice point. The short numerical derivative also handles mount sign.
        for _ in range(2):
            forward=-self.data.cam_xmat[cid].reshape(3,3)[:,2]
            inclination=float(np.arctan2(forward[2],np.linalg.norm(forward[:2])))
            delta=np.asarray(anchor)-self.data.cam_xpos[cid]
            desired=float(np.arctan2(delta[2],np.linalg.norm(delta[:2])))
            pitch=initial['head'][1]; probe=min(high,pitch+.001)
            if probe == pitch: probe=max(low,pitch-.001)
            initial['head'][1]=float(probe); self.initialize(initial)
            axis=-self.data.cam_xmat[cid].reshape(3,3)[:,2]
            slope=(float(np.arctan2(axis[2],np.linalg.norm(axis[:2])))-inclination)/(probe-pitch)
            corrected=pitch+(desired-inclination)/slope if abs(slope)>.1 else pitch
            initial['head'][1]=float(np.clip(corrected,low,high)); self.initialize(initial)
        return yaw_adjustments

    observation_camera = "head_camera"
    base_body = "base"
    initial_base_field = "base"
    open_grip = -.05
    semantic_control = False
    plan_lift_before_close = True
    lock_base_after_close = False

    def copy_control_from(self, other):
        from copy import deepcopy
        self.fixed_head = other.fixed_head.copy()
        self._hold_targets = deepcopy(getattr(other, "_hold_targets", None))

    def snapshot_control(self):
        return {"fixed_head": self.fixed_head.tolist()}

    def restore_control(self, saved):
        self.fixed_head = np.asarray(saved["fixed_head"])

    def control_height(self):
        from .torso_command import bounded_torso_command
        return bounded_torso_command(float(self.neutral_action()[19]), self.config.torso_limits)

    def execution_action(self, command):
        return self._semantic_action(command)

    def manipulation_command(self, side, q, grip, height, base=None):
        a = self.neutral_action()
        offset = {"left": 3, "right": 11}[side]
        a[offset:offset+7] = np.asarray(q)-self.arm_positions(side)
        a[{"left": 10, "right": 18}[side]] = grip
        a[19] = height
        for idle in self.arms:
            if idle != side:
                start = {'left': 3, 'right': 11}[idle]
                a[start:start+7] = self._hold_targets[idle+'_arm']-self.arm_positions(idle)
        if base is not None:
            a[:3] = np.asarray(base)-self.base_pose()
        return a

    def record_layout(self):
        return {"robot": self.config.name, "state_key": "state22", "state_layout": "state22",
                "action_layout": "rby1-20d", "action_size": 20, "state_size": 22}

    def record_state(self):
        from ...data.export.molmobot_dataset import state22
        return state22({g: {"position": self.group(g)} for g in self.groups})

    def record_action(self, command):
        return np.asarray(command, float)

    def planning_model(self, *args):
        from ...planning.rby1m_mobile_torso import PlanningModel
        return PlanningModel(*args)

    arms = ("left", "right")

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
        from ...scenes.geometry import descendants
        return tuple(descendants(self.model, self.model.body(self.config.namespace +
                     f"ee_finger_{arm[0]}{i}").id) for i in (1, 2))

    def holding_state(self, arm):
        idle = "right" if arm == "left" else "left"
        return {"head": self.group("head").tolist(),
                "idle_arm": self.group(idle+"_arm").tolist(),
                "torso": self.group("torso").tolist()}

    def _semantic_action(self, command):
        if command.lift is not None:
            raise InvalidAction("RBY-1 torso coupling is not a lift position")
        action = self.neutral_action()
        if command.base_xyyaw is not None:
            action[:3] = np.asarray(command.base_xyyaw) - self.base_pose()
        for arm, q in command.arm_q.items():
            start = {"left": 3, "right": 11}[arm]
            action[start:start+7] = np.asarray(q) - self.arm_positions(arm)
        for arm, width in command.gripper_width.items():
            if self.config.control_profile == "molmobot-native-v1":
                raise InvalidAction("native motor command requires its original units")
            action[{"left": 10, "right": 18}[arm]] = -float(width)/2
        return action


def adapter_class(config):
    if config.name == 'panda_omron' and config.control_profile == 'robocasa-v1':
        from .panda_robocasa import PandaRoboCasaAdapter
        return PandaRoboCasaAdapter
    from .panda_omron import PandaOmronAdapter
    return {"rby1m": RBY1Adapter, "panda_omron": PandaOmronAdapter}[config.name]


def make_robot_adapter(model, data, config):
    return adapter_class(config)(model, data, config)


def prepare_robot_spec(spec, config):
    if config.name == 'panda_omron' and config.control_profile == 'robocasa-v1':
        from .panda_robocasa import prepare_spec
        return prepare_spec(spec, config)
    from .panda_omron import prepare_spec
    {"rby1m": _prepare_rby1_spec, "panda_omron": prepare_spec}[config.name](spec, config)
