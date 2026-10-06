"""轻量协议：严重异常阻断、轻微接触与反馈偏差告警。

穿透容差随碰撞几何尺度变化，范围在冻结配置中声明。并非抓取/落位验收器。

【严重（error，会中断执行） vs 告警（warning，只记录）】
  error : 非有限物理状态、MuJoCo 警告、严重非允许穿透
  warning: 轻微接触、躯干联动偏差、头部反馈漂移
  正常“底盘/轮 vs 地板”的支撑接触被显式豁免，但仍记录在 contacts 里。

【穿透阈值为什么是“尺度相关”的】
  大物体被压进 5mm 也许无所谓，小物体压进 5mm 就穿模了。所以阈值 =
  较小碰撞几何包围球半径 × penetration_ratio，并夹在 [floor, ceiling] 区间内。
  这是一版显式启发式，尚未逐类别校准——文档里也强调过。
"""
import mujoco
import numpy as np


def inspect_state(sim, config):
    """检查当前物理状态，返回 {valid, issues, contacts, protocol}。

    valid=False 一定会让调用方（runner/execution）停止执行并给出失败结论。
    这个函数在每个物理子步被调用，所以必须便宜：只看接触、警告和少量关节。
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
    contacts = []
    for ci, c in enumerate(d.contact):
        ga, gb = int(c.geom1), int(c.geom2)
        ba, bb = int(m.geom_bodyid[ga]), int(m.geom_bodyid[gb])
        if ba == bb:
            continue   # 同一 body 内部的接触忽略
        names = [m.geom(ga).name, m.geom(gb).name]
        body_names = [m.body(ba).name, m.body(bb).name]
        force = np.zeros(6)
        mujoco.mj_contactForce(m, d, ci, force)
        # 底盘/轮压在地板上的接触是正常支撑，不算“非法环境碰撞”。
        floor_base = c.dist >= -.005 and any("floor" in n.lower() for n in names) and any(
            n.startswith("robot_0/") and ("base" in n or "wheel" in n) for n in body_names)
        # rbound 为碰撞几何包围球半径；零尺寸平面不参与最小尺度。
        scales = [float(m.geom_rbound[g]) for g in (ga, gb) if m.geom_rbound[g] > 0]
        scale = min(scales) if scales else config.penetration_ceiling_m
        threshold = float(np.clip(scale * config.penetration_ratio,
                                  config.penetration_floor_m, config.penetration_ceiling_m))
        entry = {"geom_names": names, "body_names": body_names, "distance_m": float(c.dist),
                 "position_world": c.pos.tolist(), "normal_force_n": float(force[0]),
                 "severe_threshold_m": threshold, "allowed_floor_base": floor_base}
        contacts.append(entry)
        if c.dist < -threshold and not floor_base:
            issues.append({"severity": "error", "code": "severe_penetration", **entry})
        elif c.dist < -0.0005 and not floor_base:
            issues.append({"severity": "warning", "code": "minor_contact", **entry})
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
