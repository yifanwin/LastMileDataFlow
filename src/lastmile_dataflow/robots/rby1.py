"""集中维护关节/执行器映射、初始化、限位和完整实际控制记录。

【职责】这个文件是“机器人知识”的唯一出处：关节名、执行器名、相机名、初始化写 qpos、
把 20 维动作翻译成 MuJoCo 的 ctrl 数组。任何 case 脚本都不应该自己写这些名字。

【本工程最重要的纪律之一】`apply()` 会先完成**全部**校验，再一次性写入 `data.ctrl`。
绝不出现“改了一半发现非法”的状态，也绝不静默裁剪越界值。见文件末尾 apply()。
"""
import mujoco
import numpy as np

from .action import InvalidAction, torso_joints, validate_action, vector


def prepare_robot_spec(spec, config):
    """只修改内存中的机器人模型，不改源 MJCF 文件；恢复 motor 夹爪为位置伺服。

    背景：RBY1 原始模型的夹爪是“力控 motor”，本工程需要它表现为“位置伺服”
    （给它一个目标位置，它会自己伺服过去），所以在内存 spec 里重写增益参数。
    这里做的改动只存在于编译后的模型，磁盘上的资产文件保持只读。
    """
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
        """设置机器人初态（唯一允许直接写 qpos 的入口，且必须在执行开始前）。

        参数 initial 是“人类可读的组结构”，例如 {"base": [x,y,yaw], "torso": [h], ...}。
        这里会把 torso 的一维 h 展开成 6 个联动关节，把夹爪一维值展开成 [q, -q]。
        全部组校验通过后才真正写 qpos，并把 qvel 清零，最后 hold() 让 ctrl 对齐初态。
        """
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
        self.hold()
        mujoco.mj_forward(self.model, self.data)

    def _controls(self, targets):
        """把目标字典翻译成完整 ctrl 数组；任一执行器越界则整体拒绝。

        返回的是 ctrl 的副本，不直接写 data.ctrl —— 调用方负责原子写入。
        """
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
        targets = {g: self.group(g) for g in self.groups}
        for side in ("left", "right"):
            targets[side + "_gripper"] = targets[side + "_gripper"][:1]
        self.data.ctrl[:] = self._controls(targets)

    def apply(self, value, *, fixed_base=False):
        """把一条 20 维动作翻译成真实控制目标并写入 ctrl。这是执行期的核心函数。

        流程：
          1. validate_action 做协议级检查。
          2. 计算每个组的目标：底盘/双臂是“当前实测 + 增量”，夹爪/躯干是“绝对值”。
             fixed_base=True 时底盘目标锁定为首次进入时的位置（绝不漂移）。
          3. 对所有目标做关节限位检查。
          4. 通过 _controls 生成新 ctrl，全部成功后才一次性写入 data.ctrl。
        返回值是“这次到底下发了什么”的完整记录，会被 recoder 原样存进 trajectory。
        """
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
