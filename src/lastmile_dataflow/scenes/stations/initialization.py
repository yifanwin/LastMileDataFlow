"""有限预算的机器人初态选择；只在连续执行前设置 qpos。不是导航。"""

import copy
import mujoco
import numpy as np

from ...runtime.control.action import torso_joints
from ...runtime.outcomes.contacts import FLOOR_PENETRATION_M, ContactSampler, simulation_failure


def room_triangles(sim):
    """使用已编译的 room floor mesh，坐标自动随 MJCF 变换；不假设 THOR 轴。"""
    m, d = sim.model, sim.data
    result = []
    for g in range(m.ngeom):
        name = m.geom(g).name.lower()
        if not name.startswith("room_") or m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        mesh = int(m.geom_dataid[g])
        va, vn = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
        fa, fn = int(m.mesh_faceadr[mesh]), int(m.mesh_facenum[mesh])
        verts = m.mesh_vert[va : va + vn] @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        faces = m.mesh_face[fa : fa + fn]
        triangles = verts[faces][:, :, :2]
        a, b = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        area = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        result.extend(triangles[np.abs(area) > 1e-8])
    return np.asarray(result)


def inside_triangles(xy, triangles):
    if len(triangles) == 0:
        return True
    u, v = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    p = xy - triangles[:, 0]
    det = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    a = (p[:, 0] * v[:, 1] - p[:, 1] * v[:, 0]) / det
    b = (u[:, 0] * p[:, 1] - u[:, 1] * p[:, 0]) / det
    return bool(np.any((a >= 0) & (b >= 0) & (a + b <= 1)))


def floor_height(sim, xy, *, triangles=None):
    """Ray against named collision floor, not robot or room visual occluders.

    The room-triangle mask supplies bounded coverage for infinite floor planes.
    """
    start = np.r_[xy, 0.20]
    direction = np.array([0.0, 0.0, -1.0])
    distances = []
    for g in range(sim.model.ngeom):
        if "floor" not in sim.model.geom(g).name.lower():
            continue
        if not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
            continue
        if sim.model.body(int(sim.model.geom_bodyid[g])).name.startswith("robot_0/"):
            continue
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            distance = mujoco.mj_rayMesh(sim.model, sim.data, g, start, direction)
        else:
            distance = mujoco.mju_rayGeom(
                sim.data.geom_xpos[g],
                sim.data.geom_xmat[g],
                sim.model.geom_size[g],
                start,
                direction,
                sim.model.geom_type[g],
            )
        if 0 <= distance <= 0.30:
            distances.append(float(distance))
    if distances and inside_triangles(np.asarray(xy), room_triangles(sim) if triangles is None else triangles):
        return 0.20 - min(distances)
    return None


def floor_support(sim, xy, *, triangles=None):
    return floor_height(sim, xy, triangles=triangles) is not None


def initialize_robot(sim, collection, *, base=None):
    if sim.started or sim.closed:
        raise RuntimeError("initialization requires a preparation session")
    rng = np.random.default_rng(collection.seed)
    initial = copy.deepcopy(sim.robot.config.initial)
    triangles = room_triangles(sim)
    if base is not None:
        candidates = [np.asarray(base, dtype=float)]
    else:
        # 从实际地板 geom 的世界范围采样，而非猜 THOR/MuJoCo 坐标变换。
        floors = [
            g
            for g in range(sim.model.ngeom)
            if "floor" in sim.model.geom(g).name.lower()
            and not sim.model.body(sim.model.geom_bodyid[g]).name.startswith("robot_0/")
            and sim.model.geom_rbound[g] > 0
        ]
        if len(triangles):
            xy_min = triangles.reshape(-1, 2).min(axis=0)
            xy_max = triangles.reshape(-1, 2).max(axis=0)
        elif not floors:
            raise ValueError("no bounded floor or room mesh; explicit --base required")
        candidates = []
        for _ in range(collection.max_initialization_trials):
            if len(triangles):
                xy = rng.uniform(xy_min, xy_max)
            else:
                g = int(rng.choice(floors))
                pos = sim.data.geom_xpos[g]
                radius = float(sim.model.geom_rbound[g])
                xy = pos[:2] + rng.uniform(-radius, radius, 2)
            candidates.append(np.r_[xy, rng.uniform(-3.0, 3.0)])
    trials = []
    for candidate in candidates:
        initial[sim.robot.initial_base_field] = candidate.tolist()
        try:
            sim.robot.initialize(initial)
        except ValueError as exc:
            trials.append({"base": candidate.tolist(), "valid": False, "reason": str(exc)})
            continue
        # 基座必须位于实际地面上方；向下 ray 排除房外/空洞。
        ground = floor_support(sim, candidate[:2])
        check = inspect_state(sim, collection, initialization=True)
        valid = bool(ground and inside_triangles(candidate[:2], triangles) and check["valid"])
        trials.append(
            {
                "base": candidate.tolist(),
                "valid": valid,
                "ground": bool(ground),
                "errors": [i for i in check["issues"] if i["severity"] == "error"],
            }
        )
        if valid:
            return {
                "status": "valid",
                "selected_base": candidate.tolist(),
                "trials": trials,
                "check": check,
                "method": "explicit" if base is not None else "bounded_floor_sampling",
            }
    return {"status": "invalid", "reason": "initialization_budget_exhausted", "trials": trials}


def inspect_state(sim, config, *, initialization=False):
    """检查当前物理状态，返回 {valid, issues, contacts, protocol}。

    valid=False 一定会让调用方（runner/execution）停止执行并给出失败结论。
    运行时接触只记录；initialization=True 才检查初态严重重叠。
    """
    m, d = sim.model, sim.data
    issues = []
    # 廉价的第一道闸：任何非有限值立即返回，避免后面算出 NaN 比较。
    if simulation_failure(sim) == "nonfinite_physics":
        issues.append({"severity": "error", "code": "nonfinite_physics"})
        return {"valid": False, "issues": issues, "contacts": []}
    # MuJoCo 自身的警告（求解器不稳、约束冲突等）一律当 error。
    for i, warning in enumerate(d.warning):
        if warning.number:
            issues.append(
                {"severity": "error", "code": "mujoco_warning", "warning_id": i, "count": int(warning.number)}
            )
    contacts = ContactSampler(sim).capture().observations()
    if initialization:
        for entry in contacts:
            scales = [float(m.geom_rbound[g]) for g in entry["geom_ids"] if m.geom_rbound[g] > 0]
            scale = min(scales) if scales else config.penetration_ceiling_m
            threshold = float(
                np.clip(scale * config.penetration_ratio, config.penetration_floor_m, config.penetration_ceiling_m)
            )
            if entry["distance_m"] < -threshold and not (
                entry["allowed_floor_base"] and entry["distance_m"] >= -FLOOR_PENETRATION_M
            ):
                issues.append(
                    {
                        "severity": "error",
                        "code": "severe_penetration",
                        **entry,
                        "initialization_threshold_m": threshold,
                    }
                )
    robot = sim.robot
    if "torso" in robot.joints.groups:
        # 躯干 6 个关节必须满足 [0,h,-2h,h,0,0] 的联动关系；偏离说明伺服跟不上或影子状态不一致。
        torso = robot.group("torso")
        if np.max(np.abs(torso - torso_joints(torso[1]))) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "torso_coupling_feedback", "actual": torso.tolist()})
        # 头部锁定目标也应保持不变（头部没有动作维度）。
        if np.max(np.abs(robot.group("head") - robot.fixed_head)) > config.feedback_warning_rad:
            issues.append({"severity": "warning", "code": "head_feedback_drift"})
    return {"valid": not any(i["severity"] == "error" for i in issues), "issues": issues, "contacts": contacts}
