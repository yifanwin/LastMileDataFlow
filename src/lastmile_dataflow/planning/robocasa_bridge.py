"""File bridge between native RoboCasa (mujoco 3.3) and cuRobo (main .venv).

export_problem runs in the RoboCasa env; the npz holds what cuRobo needs, in the planning
base frame of the unscaled assets (URDF world = native robot0_link0 shifted back by
LINK0_OFFSET_M along link0 x, z at the base floor).
"""
import numpy as np
import mujoco

LINK0_OFFSET_M = 0.21  # URDF world -> panda_link0 along x
FLOOR_CLEARANCE_M = 0.01
ROBOT_PREFIXES = ("robot0_", "mobilebase0_", "gripper0_")
BOX_FACES = np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],[2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]])


def _pose(pos, mat):
    t = np.eye(4); t[:3, :3] = np.asarray(mat).reshape(3, 3); t[:3, 3] = pos
    return t


def planning_base(runtime):
    """World pose of the URDF world frame at the current station."""
    m, d = runtime.env.sim.model._model, runtime.env.sim.data._data
    b, l0 = m.body("mobilebase0_base").id, m.body("robot0_link0").id
    frame = _pose(d.xpos[b], d.xmat[b])
    link0 = _pose(d.xpos[l0], d.xmat[l0])
    frame[:2, 3] = link0[:2, 3] - LINK0_OFFSET_M * link0[:2, 0]
    return frame


def geom_triangles(m, d, g):
    kind = m.geom_type[g]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        mid = m.geom_dataid[g]
        v = m.mesh_vert[m.mesh_vertadr[mid]: m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
        f = m.mesh_face[m.mesh_faceadr[mid]: m.mesh_faceadr[mid] + m.mesh_facenum[mid]]
    else:
        s = m.geom_size[g]
        if kind == mujoco.mjtGeom.mjGEOM_BOX:
            h = s
        elif kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
            h = np.r_[s[0], s[0], s[1]]
        elif kind == mujoco.mjtGeom.mjGEOM_CAPSULE:
            h = np.r_[s[0], s[0], s[1] + s[0]]
        elif kind == mujoco.mjtGeom.mjGEOM_SPHERE:
            h = np.repeat(s[0], 3)
        else:
            return None
        v = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * h
        f = BOX_FACES
    R = np.asarray(d.geom_xmat[g]).reshape(3, 3)
    return v @ R.T + d.geom_xpos[g], f


def export_problem(runtime, grasps_object_to_tcp, path, range_m=1.5):
    m, d = runtime.env.sim.model._model, runtime.env.sim.data._data
    base = planning_base(runtime)
    inv = np.linalg.inv(base)
    target = set(runtime.target_geoms)
    verts, faces, names, counts, n = [], [], [], [], 0
    for g in range(m.ngeom):
        if m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0:
            continue
        if g in target or m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE:
            continue
        if m.body(m.geom_bodyid[g]).name.startswith(ROBOT_PREFIXES):
            continue
        if np.linalg.norm(d.geom_xpos[g][:2] - base[:2, 3]) - m.geom_rbound[g] > range_m:
            continue
        tri = geom_triangles(m, d, g)
        if tri is None:
            continue
        v, f = tri
        if v[:, 2].max() <= base[2, 3] + FLOOR_CLEARANCE_M:
            continue  # floor slabs: planner only, physics unchanged
        verts.append(v @ inv[:3, :3].T + inv[:3, 3]); faces.append(f + n); n += len(v)
        counts.append(len(v))
        names.append(m.geom(g).name or f"geom{g}")
    obj = _pose(d.xpos[runtime.target_body], d.xmat[runtime.target_body])
    goals = np.array([inv @ obj @ T for T in grasps_object_to_tcp])
    joints = runtime.joint_positions()
    np.savez(path, vertices=np.concatenate(verts), faces=np.concatenate(faces), geom_names=np.array(names),
             base_world=base, goals_base=goals, geom_vertex_counts=np.array(counts), arm=np.array(joints["right"]), torso=np.array(joints["torso"]),
             fingers=np.array(joints["right_gripper"]), object_world=obj)
    return {"geoms": len(names), "vertices": n, "goals": len(goals)}
