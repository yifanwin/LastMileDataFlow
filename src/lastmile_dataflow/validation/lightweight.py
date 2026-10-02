"""轻量协议：严重异常阻断、轻微接触与反馈偏差告警。

穿透容差随碰撞几何尺度变化，范围在冻结配置中声明。并非抓取/落位验收器。
"""
import mujoco
import numpy as np


def inspect_state(sim, config):
    m, d = sim.model, sim.data
    issues = []
    if not all(np.isfinite(a).all() for a in (d.qpos, d.qvel, d.ctrl, d.qacc)):
        issues.append({"severity": "error", "code": "nonfinite_physics_state"})
        return {"valid": False, "issues": issues, "contacts": [], "protocol": config.protocol_version}
    for i, warning in enumerate(d.warning):
        if warning.number:
            issues.append({"severity": "error", "code": "mujoco_warning",
                           "warning_id": i, "count": int(warning.number)})
    contacts = []
    for ci, c in enumerate(d.contact):
        ga, gb = int(c.geom1), int(c.geom2)
        ba, bb = int(m.geom_bodyid[ga]), int(m.geom_bodyid[gb])
        if ba == bb:
            continue
        names = [m.geom(ga).name, m.geom(gb).name]
        body_names = [m.body(ba).name, m.body(bb).name]
        force = np.zeros(6)
        mujoco.mj_contactForce(m, d, ci, force)
        # 正常底盘/轮与地板接触仍保留记录，但不当作非法环境碰撞。
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
        robot = sim.robot
        torso = robot.group("torso")
        expected = np.array([0., torso[1], -2 * torso[1], torso[1], 0., 0.])
        if np.max(np.abs(torso - expected)) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "torso_coupling_feedback",
                           "actual": torso.tolist()})
        if np.max(np.abs(robot.group("head") - robot.fixed_head)) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "head_feedback_drift"})
    return {"valid": not any(i["severity"] == "error" for i in issues),
            "issues": issues, "contacts": contacts, "protocol": config.protocol_version}


def independent_results(scene=None):
    return {"scene_validity": scene or {"status": "unknown", "reason": "not_loaded"},
            "case_condition": {"status": "unknown", "reason": "phase_2_3_not_evaluated"},
            "task_completion": {"status": "unknown", "reason": "no_task_success_evaluator"}}
