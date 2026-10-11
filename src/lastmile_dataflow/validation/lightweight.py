"""Simulation health and contact telemetry; strict overlap checks are initialization-only."""
import numpy as np
from .contacts import contact_observations


def inspect_state(sim, config, *, initialization=False):
    """检查当前物理状态，返回 {valid, issues, contacts, protocol}。

    valid=False 一定会让调用方（runner/execution）停止执行并给出失败结论。
    运行时接触只记录；initialization=True 才检查初态严重重叠。
    """
    m, d = sim.model, sim.data
    issues = []
    # 廉价的第一道闸：任何非有限值立即返回，避免后面算出 NaN 比较。
    if not all(np.isfinite(a).all() for a in (d.qpos, d.qvel, d.ctrl, d.qacc)):
        issues.append({"severity": "error", "code": "nonfinite_physics_state"})
        return {"valid": False, "issues": issues, "contacts": [], "protocol": config.protocol_version}
    # MuJoCo 自身的警告（求解器不稳、约束冲突等）一律当 error。
    for i, warning in enumerate(d.warning):
        if warning.number:
            issues.append({"severity": "error", "code": "mujoco_warning",
                           "warning_id": i, "count": int(warning.number)})
    contacts = contact_observations(sim)
    if initialization:
        for entry in contacts:
            scales = [float(m.geom_rbound[g]) for g in entry['geom_ids'] if m.geom_rbound[g] > 0]
            scale = min(scales) if scales else config.penetration_ceiling_m
            threshold = float(np.clip(scale * config.penetration_ratio,
                                      config.penetration_floor_m, config.penetration_ceiling_m))
            if entry['distance_m'] < -threshold and not (entry['allowed_floor_base'] and entry['distance_m'] >= -.005):
                issues.append({'severity': 'error', 'code': 'severe_penetration', **entry,
                               'initialization_threshold_m': threshold})
    if sim.robot is not None:
        # 躯干 6 个关节必须满足 [0,h,-2h,h,0,0] 的联动关系；偏离说明伺服跟不上或影子状态不一致。
        robot = sim.robot
        torso = robot.group("torso")
        expected = np.array([0., torso[1], -2 * torso[1], torso[1], 0., 0.])
        if np.max(np.abs(torso - expected)) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "torso_coupling_feedback",
                           "actual": torso.tolist()})
        # 头部锁定目标也应保持不变（头部没有动作维度）。
        if np.max(np.abs(robot.group("head") - robot.fixed_head)) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "head_feedback_drift"})
    return {"valid": not any(i["severity"] == "error" for i in issues),
            "issues": issues, "contacts": contacts, "protocol": config.protocol_version}


def independent_results(scene=None):
    """三个**互相独立**的结论，绝不合并成一个 success。

      scene_validity  : 场景/初态是否合规（阶段一是轻量范围）
      case_condition  : 这个 case 是否成立（阶段一/二恒为 unknown，等阶段三真实执行）
      task_completion : 机器人任务是否完成（阶段一恒为 unknown，无验收器）

    默认全部 unknown —— “没测就是未知”，不是失败也不是成功。
    """
    return {"scene_validity": scene or {"status": "unknown", "reason": "not_loaded"},
            "case_condition": {"status": "unknown", "reason": "phase_2_3_not_evaluated"},
            "task_completion": {"status": "unknown", "reason": "no_task_success_evaluator"}}
