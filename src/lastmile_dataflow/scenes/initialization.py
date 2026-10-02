"""有限预算的机器人初态选择；只在连续执行前设置 qpos。不是导航。"""
import copy
import mujoco
import numpy as np

from ..validation.lightweight import inspect_state


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
        verts = m.mesh_vert[va:va + vn] @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        faces = m.mesh_face[fa:fa + fn]
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
        floors = [g for g in range(sim.model.ngeom)
                  if "floor" in sim.model.geom(g).name.lower()
                  and not sim.model.body(sim.model.geom_bodyid[g]).name.startswith("robot_0/")
                  and sim.model.geom_rbound[g] > 0]
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
        initial["base"] = candidate.tolist()
        try:
            sim.robot.initialize(initial)
        except ValueError as exc:
            trials.append({"base": candidate.tolist(), "valid": False, "reason": str(exc)})
            continue
        # 基座必须位于实际地面上方；向下 ray 排除房外/空洞。
        ray_start = np.r_[candidate[:2], 0.20]
        geom_id = np.zeros(1, dtype=np.int32)
        distance = mujoco.mj_ray(sim.model, sim.data, ray_start, np.array([0., 0., -1.]),
                                None, True, sim.model.body("robot_0/base").id, geom_id)
        ground = distance >= 0 and distance <= 0.30 and "floor" in sim.model.geom(int(geom_id[0])).name.lower()
        check = inspect_state(sim, collection)
        valid = bool(ground and inside_triangles(candidate[:2], triangles) and check["valid"])
        trials.append({"base": candidate.tolist(), "valid": valid, "ground": bool(ground),
                       "errors": [i for i in check["issues"] if i["severity"] == "error"]})
        if valid:
            return {"status": "valid", "selected_base": candidate.tolist(), "trials": trials,
                    "check": check, "method": "explicit" if base is not None else "bounded_floor_sampling"}
    return {"status": "invalid", "reason": "initialization_budget_exhausted", "trials": trials}
