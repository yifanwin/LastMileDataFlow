"""Strict force-aware pick evidence. Every physics tick, no agent override.

【这个文件决定“抓取算不算成功”】协议 strict-pick-v3 是硬编码常量，**配置无法放宽**。
定义见下方 PROTOCOL：双指真实法向力、抬升高度、无支撑保持时长、相对滑移、底盘/头/闲置臂漂移、
躯干联动误差、采样间隔上限、接触力下限。

【两种层次，别混淆】
  PickMonitor.sample()      每个物理 tick 采一行“事实”（力、位姿、接触日志）。
  constraint_failure()      逐行判违约——任何一项违规立刻给 failure 并记录首个失败阶段。
  evaluate_pick()           汇总全部样本，给出 success/failure/infrastructure_error。

【为什么要有 infrastructure_error 这一档】
  如果证据本身缺失、时间戳断裂、四元数不合法，那不是“机器人抓失败了”，
  而是“我们没有可信证据”。这种情况必须单独归类，绝不能写成 failure——
  否则会把数据采集故障误当成物理结论。
"""

import numpy as np

from ...scenes.geometry import body_pose, descendants, tcp_pose
from ..control.action import torso_joints
from ..execution_config import TORSO_ERROR_POLICY
from .contacts import CONTACT_POLICY, SIMULATION_FAILURES, ContactSampler, simulation_failure
from .pick_state import PICK_THRESHOLDS, update_hold_window

VLA_PROTOCOL = "mobile-vla-physical-v2"

# 任务成功阈值不可通过配置放宽。
PROTOCOL = {**PICK_THRESHOLDS, "contact_policy": CONTACT_POLICY}


def rotation_error(a, b):
    return float(np.arccos(np.clip((np.trace(a.T @ b) - 1) / 2, -1, 1)))


def yaw_gap(delta):
    return float(np.arctan2(np.sin(delta), np.cos(delta)))


class PickMonitor:
    """逐物理 tick 的事实采样器 + 接触记录器。

    初始化时锁定“基线”：底盘/头/闲置臂的初值、目标高度、两个指尖 body 的 id。
    之后每次 sample() 都相对基线判断，所以任何漂移都会被记录。
    """

    def __init__(self, sim, side, *, allowed_target_fingers=()):
        self.sim, self.side = sim, side
        # 机器人自身 body 集合 vs 目标（含其子部件）集合，用来区分“指-目标接触”和“环境接触”。
        self.robot_bodies = {
            b for b in range(sim.model.nbody) if sim.model.body(b).name.startswith(sim.robot.config.namespace)
        }
        self.target_bodies = descendants(sim.model, sim.target_id)
        groups = sim.robot.finger_bodies(side)
        self.fingers = {min(group) for group in groups}
        self.finger_index = {body: min(group) for group in groups for body in group}
        self.allowed_target_fingers = set(self.finger_index) | set(allowed_target_fingers)
        self.initial = {
            "base": sim.robot.base_pose().tolist(),
            **sim.robot.holding_state(side),
            "target": sim.model.body(sim.target_id).name,
            "height_m": float(sim.data.xpos[sim.target_id, 2]),
            "fingers": sorted(self.fingers),
        }
        self.phase = "torso_adjust"
        self.command_h = 0.0
        self.contact_sampler = ContactSampler(sim)

    def sample(self, contacts=None):
        """采集一行物理事实。包含：力、支撑、接触标记列表、相对位姿（TCP←目标）。"""
        sim = self.sim
        m, d = sim.model, sim.data
        forces = {}
        support = False
        contact_flags = []
        health = simulation_failure(sim)
        illegal = [health] if health else []
        if contacts is None:
            contacts = self.contact_sampler.capture()
        for sample in contacts.contacts:
            pair, force = sample.pair, sample.force
            a, b = pair.bodies
            bearing = bool(sample.active and force > PROTOCOL["finger_force_min_n"])  # 是否真的承力
            ta, tb = a in self.target_bodies, b in self.target_bodies
            ra, rb = a in self.robot_bodies, b in self.robot_bodies
            if ta != tb:
                # 目标 vs 其它：可能是指尖抓握、桌面支撑、或手指以外的机器人部位非法压目标
                other = b if ta else a
                if other in self.finger_index and bearing:
                    finger = self.finger_index[other]
                    forces[finger] = forces.get(finger, 0.0) + force
                elif other not in self.robot_bodies and bearing:
                    support = True  # 目标仍被桌面支撑
                elif other in self.robot_bodies and other not in self.allowed_target_fingers:
                    contact_flags.append("nonfinger_target")
            elif ra and rb:
                contact_flags.append("self_collision")
            elif ra != rb and not pair.floor_base:
                # 机器人 vs 环境：豁免“底盘/轮 vs 地板”的正常支撑接触
                contact_flags.append("robot_environment_collision")
        target = body_pose(sim, sim.target_id)
        tcp = tcp_pose(sim, self.side)
        # relative_pose = TCP^-1 · 目标，即“目标相对手爪”的位姿；滑移检测就看它是否稳定。
        extra = {"command_h": float(self.command_h)} if "torso" in self.initial else {}
        return {
            "time_s": float(d.time),
            "phase": self.phase,
            **extra,
            "base": sim.robot.base_pose().tolist(),
            **sim.robot.holding_state(self.side),
            "target": m.body(sim.target_id).name,
            "height_m": float(d.xpos[sim.target_id, 2]),
            "support_contact": support,
            "finger_forces_n": {str(k): v for k, v in forces.items()},
            "relative_pose": np.linalg.solve(tcp, target).tolist(),
            "illegal": sorted(set(illegal)),
            "contact_flags": sorted(set(contact_flags)),
            "contact_policy": CONTACT_POLICY,
            "sampler": "force-aware-v3",
        }


def constraint_failure(row, initial, *, check_base=True, check_torso=True, check_idle=True):
    """逐行判违约，返回首个违约原因字符串，全部合规返回 None。

    检查顺序：目标没换 → 底盘漂移 → 头/闲置臂漂移 → 躯干联动 → 仿真健康。
    """
    health = next((issue for issue in row["illegal"] if issue in SIMULATION_FAILURES), None)
    if health:
        return health
    if row["target"] != initial["target"]:
        return "wrong_target"
    base = np.array(row["base"])
    delta = base - np.array(initial["base"])
    if check_base and (
        np.linalg.norm(delta[:2]) > PROTOCOL["base_translation_m"] or abs(yaw_gap(delta[2])) > PROTOCOL["base_yaw_rad"]
    ):
        return "base_drift"
    for key, tol in (("head", "head_error_rad"), ("idle_arm", "idle_arm_error_rad")):
        if key not in initial or key == "idle_arm" and not check_idle:
            continue
        if np.max(np.abs(np.array(row[key]) - initial[key])) > PROTOCOL[tol]:
            return key + "_drift"
    if check_torso and "torso" in row and torso_tracking_error(row) > PROTOCOL["torso_error_rad"]:
        return "torso_protocol"
    if check_torso and "lift" in initial and abs(row["lift"] - initial["lift"]) > PROTOCOL["lift_hold_error_m"]:
        return "lift_drift"
    return None


def evaluate_pick(samples, initial, *, constraint_checker=constraint_failure):
    """汇总全部物理样本，判定这次抓取尝试的最终结论。

    返回 status 为 success / failure / infrastructure_error 三者之一，并带诊断。
    判定成功需要**同时满足**：两个指尖都承力、目标抬升 ≥ 5cm、目标已不靠桌面支撑，
    且上述状态连续保持 ≥ 2s（允许期间因滑移重开计时窗口）。
    """
    required = {
        "time_s",
        "phase",
        "base",
        "target",
        "height_m",
        "support_contact",
        "finger_forces_n",
        "relative_pose",
        "illegal",
        "sampler",
    }
    required |= {"lift"} if "lift" in initial else {"command_h", "torso", "head", "idle_arm"}
    # 先做“证据完整性”检查：字段缺失或采样器版本不对 → 这是设施问题，不是抓取失败。
    if not samples or any(not required <= set(r) or r["sampler"] != "force-aware-v3" for r in samples):
        return {"status": "infrastructure_error", "reason": "missing_physics_evidence"}
    times = np.array([r["time_s"] for r in samples])
    # 时间必须严格递增且采样间隔不过大，否则“保持 2 秒”无从谈起。
    if (
        len(times) < 2
        or not np.isfinite(times).all()
        or np.any(np.diff(times) <= 0)
        or np.max(np.diff(times)) > PROTOCOL["max_sample_gap_s"]
    ):
        return {"status": "infrastructure_error", "reason": "physics_trace_gap"}
    start = np.asarray(-1.0)
    reference_position = np.zeros(3)
    reference_rotation = np.eye(3)
    best = 0.0
    max_lift = 0.0
    tail = 0.0
    for r in samples:
        numeric = np.concatenate(
            [
                np.atleast_1d(r[key])
                for key in ("base", "torso", "head", "idle_arm", "lift", "height_m", "command_h")
                if key in r
            ]
        )
        pose = np.asarray(r["relative_pose"])
        forces = {int(k): float(v) for k, v in r["finger_forces_n"].items()}
        # 数值合法性：四元数/旋转矩阵/力的非有限值或非法值都算证据无效。
        if (
            not np.isfinite(numeric).all()
            or pose.shape != (4, 4)
            or not np.isfinite(pose).all()
            or not np.allclose(pose[3], [0, 0, 0, 1])
            or not np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-5)
            or not np.isclose(np.linalg.det(pose[:3, :3]), 1)
            or any(not np.isfinite(v) or v <= PROTOCOL["finger_force_min_n"] for v in forces.values())
        ):
            return {"status": "infrastructure_error", "reason": "invalid_physics_evidence"}
        fail = constraint_checker(r, initial)
        # 任何一行违约 → 直接 failure，并记录首个违约来自哪个阶段/时刻（便于归因）。
        if fail:
            return {
                "status": "simulation_failure" if fail in ("nonfinite_state", "mujoco_warning") else "failure",
                "reason": fail,
                "first_failure_phase": r["phase"],
                "first_failure_time_s": r["time_s"],
            }
        lift = r["height_m"] - initial["height_m"]
        max_lift = max(max_lift, lift)
        held = (
            set(forces) == set(initial["fingers"])
            and len(forces) == 2
            and lift >= PROTOCOL["lift_m"]
            and r["support_contact"] is False
        )
        start, reference_position, reference_rotation, tail = update_hold_window(
            r["time_s"],
            np.asarray(held),
            pose[:3, 3],
            pose[:3, :3],
            start,
            reference_position,
            reference_rotation,
            xp=np,
        )
        best = max(best, float(tail))
    ok = tail >= PROTOCOL["hold_s"] - 1e-9
    return {
        "status": "success" if ok else "failure",
        "reason": "strict_pick" if ok else "lift_contact_hold_or_slip",
        "first_failure_phase": None if ok else samples[-1]["phase"],
        "tail_held_s": float(tail),
        "max_held_s": float(best),
        "max_lift_m": float(max_lift),
        "physics_samples": len(samples),
    }


def torso_tracking_error(row):
    """Measured six-joint error, telemetry only during V2 execution."""
    torso = np.asarray(row["torso"], float)
    h = torso[1] if row["phase"] == "torso_adjust" else row["command_h"]
    return float(np.max(np.abs(torso - torso_joints(h))))


def workspace_constraint(row, initial, *, check_idle=True):
    """v2: base motion allowed inside the workspace, torso error recorded rather than a stop condition."""
    distance = np.linalg.norm(np.asarray(row["base"][:2]) - initial["workspace_center_xy"])
    if distance > initial["workspace_radius_m"] + 1e-6:
        return "base_outside_workspace"
    return constraint_failure(row, initial, check_base=False, check_torso=False, check_idle=check_idle)


def mobile_constraint_failure(row, initial):
    return workspace_constraint(row, initial)


def constraint(row, initial):
    """VLA hands may both move, so the idle arm is not held."""
    return workspace_constraint(row, initial, check_idle=False)


def evaluate_mobile_pick(samples, initial):
    result = evaluate_pick(samples, initial, constraint_checker=mobile_constraint_failure)
    result["protocol"] = "mobile-pick-v2"
    result["torso_error_policy"] = TORSO_ERROR_POLICY
    if result["status"] == "success":
        result["reason"] = "mobile_pick"
    return result


def attribute(reason, *, samples=(), collisions=(), diagnostic=None):
    result = {"category": "Unknown", "reason": reason, "collisions": list(collisions)}
    if reason in SIMULATION_FAILURES:
        result["category"] = "SimulationFailure"
    elif "collision" in reason or "nonfinger" in reason or "penetration" in reason:
        result["category"] = "Collision"
    elif reason == "outside_conservative_reach_bound":
        result["category"] = "Reachability"
        result["scope"] = "conservative shoulder-distance bound"
    elif diagnostic and diagnostic.get("free_ik_success") is False:
        result["category"] = "Reachability"
        result["scope"] = "tested grasp/torso/arm and finite IK seeds only"
    elif "planning" in reason or "no_solution" in reason or reason == "fov_constraint_failed":
        result["category"] = "PlanningFailure"
    elif (
        reason.startswith("control_limit:")
        or reason == "torso_feedback_limit"
        or "tracking" in reason
        or "drift" in reason
        or "protocol" in reason
        or "workspace" in reason
    ):
        result["category"] = "TrackingFailure"
    elif reason in ("lift_contact_hold_or_slip", "open_no_grip", "open_contact_lost"):
        gripping = [r for r in samples if len(r.get("finger_forces_n", {})) == 2]
        if not gripping:
            result["category"] = "MissedGrasp"
        elif samples and len(samples[-1].get("finger_forces_n", {})) != 2:
            result["category"] = "DroppedObject" if reason == "lift_contact_hold_or_slip" else "LostHandleContact"
        else:
            slipped = False
            reference = np.asarray(gripping[0].get("relative_pose", np.eye(4)))
            for row in gripping[1:]:
                pose = np.asarray(row.get("relative_pose", np.eye(4)))
                if (
                    np.linalg.norm(pose[:3, 3] - reference[:3, 3]) > PROTOCOL["relative_translation_m"]
                    or rotation_error(reference[:3, :3], pose[:3, :3]) > PROTOCOL["relative_rotation_rad"]
                ):
                    slipped = True
                    break
            result["category"] = "GraspSlip" if slipped else "InsufficientLiftOrHold"
    elif reason.startswith("open_"):
        result["category"] = "ArticulationFailure"
    if diagnostic:
        result["planning_diagnostic"] = diagnostic
    return result


def open_progress(task, joint_value):
    direction = np.sign(task["joint_goal"] - task["joint_initial"])
    return float(direction * (joint_value - task["joint_initial"]))


def evaluate_open(task, samples, hold_s):
    for row in samples:
        health = next((issue for issue in row.get("illegal", []) if issue in SIMULATION_FAILURES), None)
        if health:
            return {"status": "simulation_failure", "reason": health}
    if len(samples) < 2:
        return {"status": "failure", "reason": "open_missing_hold"}
    required = abs(task["joint_goal"] - task["joint_initial"])
    start = None
    best = 0.0
    for row in samples:
        if "joint_value" not in row or not np.isfinite(row["joint_value"]):
            return {"status": "infrastructure_error", "reason": "invalid_open_evidence"}
        valid = open_progress(task, row["joint_value"]) >= required - 1e-3 and len(row["finger_forces_n"]) == 2
        if valid:
            if start is None:
                start = row["time_s"]
            best = max(best, row["time_s"] - start)
        else:
            start = None
    tail = samples[-1]["time_s"] - start if start is not None else 0.0
    return {
        "status": "success" if tail >= hold_s - 1e-6 else "failure",
        "reason": "physical_open" if tail >= hold_s - 1e-6 else "open_joint_or_contact_hold",
        "required_progress": required,
        "final_progress": open_progress(task, samples[-1]["joint_value"]),
        "tail_hold_s": tail,
        "max_hold_s": best,
    }


def evaluate(task, samples, initials, hold_s):
    verdicts = {}
    for side in samples:
        rows = samples[side]
        if task["operation"] == "pick":
            verdicts[side] = evaluate_pick(rows, initials[side], constraint_checker=constraint)
        else:
            verdicts[side] = evaluate_open(task, rows, hold_s)
    success = next((side for side, verdict in verdicts.items() if verdict["status"] == "success"), None)
    simulation_error = next((v["reason"] for v in verdicts.values() if v["status"] == "simulation_failure"), None)
    if simulation_error:
        status, reason = "simulation_failure", simulation_error
    elif success:
        status, reason = "success", "physical_" + task["operation"]
    elif any(v["status"] == "infrastructure_error" for v in verdicts.values()):
        status, reason = "infrastructure_error", "invalid_physics_evidence"
    else:
        status, reason = "failure", "task_not_completed"
    return {
        "status": status,
        "reason": reason,
        "winning_hand": success if status == "success" else None,
        "hands": verdicts,
        "protocol": VLA_PROTOCOL,
        "torso_error_policy": TORSO_ERROR_POLICY,
        "both_arms_commanded": len(samples) > 1,
    }
