"""RBY1_multitask 20 维协议。相对量参考动作下发时的实测状态。

【全工程共用的“语言”】任何模块想让机器人动，都必须先构造一个长度 20 的浮点向量，
再由 `validate_action` 校验。这个文件是 20 维语义的唯一定义处，不要在各 case 里重复定义。

布局（与 docs/data-format.md 一致）：
    索引 0:3   底盘 [dx, dy, dyaw]        世界系相对增量
    索引 3:10  左臂 7 关节相对增量
    索引 10    左夹爪绝对位置，必须落在 [-0.05, 0]
    索引 11:18 右臂 7 关节相对增量
    索引 18    右夹爪绝对位置，必须落在 [-0.05, 0]
    索引 19    躯干绝对参数 h，落在 [0, 0.738]，下发时展开为 [0,h,-2h,h,0,0]

“相对量”的含义很关键：它不是绝对目标，而是**相对下发那一刻的实测关节状态**的增量。
所以同样的动作向量在不同时刻下发会产生不同结果。
"""
import numpy as np

# 供人阅读/文档生成用的布局表：(组名, 起始索引, 结束索引, 语义)
ACTION_LAYOUT = (("base", 0, 3, "delta_world_xy_yaw"),
                 ("left_arm", 3, 10, "delta_joint_rad"),
                 ("left_gripper", 10, 11, "absolute_m"),
                 ("right_arm", 11, 18, "delta_joint_rad"),
                 ("right_gripper", 18, 19, "absolute_m"),
                 ("torso", 19, 20, "absolute_height_parameter"))


class InvalidAction(ValueError):
    pass


def vector(value, size):
    """把任意输入转成“长度正确 + 全部有限”的 float 向量，否则抛 InvalidAction。

    这是所有动作/位姿校验的第一道闸门：形状错、含 NaN/Inf、不可转 float 都直接拒绝。
    注意是 `.copy()`，避免调用方后续误改原数组。
    """
    try:
        a = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise InvalidAction("action must be numeric") from exc
    if a.shape != (size,) or not np.isfinite(a).all():
        raise InvalidAction(f"expected finite vector of size {size}; got {a.shape}")
    return a.copy()


def torso_joints(h):
    """把一维“躯干参数 h”展开成模型真实的 6 个联动关节角。

    这就是文档里说的“躯干 h 是联动参数，不是世界高度 m”的来源。
    反向检查见 validation/lightweight.py 的 torso_coupling_feedback。
    """
    return np.array([0., h, -2 * h, h, 0., 0.])


def validate_action(value, config, *, fixed_base=False):
    """20 维动作的完整校验；任何一条不满足就抛 InvalidAction，绝不放行部分动作。

    校验顺序（越靠前越基础）：
      1. 维度 20 且全有限
      2. 底盘增量不超过 max_base_delta / max_yaw_delta
      3. fixed_base 试验禁止任何底盘命令（非零即拒）
      4. 双臂每个关节增量不超过 max_arm_delta
      5. 夹爪绝对目标在 gripper_limits 内
      6. 躯干绝对参数在 torso_limits 内

    注意：这里只做“单个动作”的边界检查；关节真实限位、执行器限位由
    RBY1Adapter.apply() -> _check_joint()/_controls() 在写入 ctrl 之前再查一遍。
    """
    a = vector(value, 20)
    if np.linalg.norm(a[:2]) > config.max_base_delta or abs(a[2]) > config.max_yaw_delta:
        raise InvalidAction("base delta exceeds configured bound")
    if fixed_base and np.any(a[:3] != 0):
        raise InvalidAction("fixed-base trial prohibits base commands")
    if np.max(np.abs(np.r_[a[3:10], a[11:18]])) > config.max_arm_delta:
        raise InvalidAction("arm delta exceeds configured bound")
    for i in (10, 18):
        if not config.gripper_limits[0] <= a[i] <= config.gripper_limits[1]:
            raise InvalidAction("gripper absolute target out of range")
    if not config.torso_limits[0] <= a[19] <= config.torso_limits[1]:
        raise InvalidAction("torso height out of range")
    return a
