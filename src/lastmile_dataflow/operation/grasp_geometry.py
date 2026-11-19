"""Native Panda hand and target visual geometry shared by preprocessing and plots."""

from dataclasses import dataclass
from types import SimpleNamespace

import mujoco
import numpy as np

from lastmile_dataflow.robots.registry import load_robot_config, load_robot_spec
from lastmile_dataflow.sim.model.geometry import descendants, geom_points


@dataclass
class PandaHand:
    model: mujoco.MjModel
    data: mujoco.MjData
    site: int
    triangles: np.ndarray
    root_to_tcp: np.ndarray


def visual_triangles(model, data, target):
    bodies = descendants(model, target)
    rotation = data.xmat[target].reshape(3, 3)
    sim = SimpleNamespace(model=model, data=data)
    triangles = []
    for geom in range(model.ngeom):
        if (int(model.geom_bodyid[geom]) not in bodies
                or model.geom_type[geom] != mujoco.mjtGeom.mjGEOM_MESH
                or model.geom_contype[geom] or model.geom_conaffinity[geom]):
            continue
        mesh = int(model.geom_dataid[geom])
        start, count = int(model.mesh_faceadr[mesh]), int(model.mesh_facenum[mesh])
        vertices = (geom_points(sim, geom) - data.xpos[target]) @ rotation
        triangles.append(vertices[model.mesh_face[start:start + count]])
    if not triangles:
        raise ValueError("Target has no visual mesh triangles")
    return np.concatenate(triangles)


def panda_hand(config, scale):
    """Native open Panda Hand mesh in its measured grip-site coordinate frame."""
    model = load_robot_spec(load_robot_config(config), scale=scale).compile()
    data = mujoco.MjData(model)
    for suffix, endpoint in ((1, 1), (2, 0)):
        joint = model.joint(f"robot_0/gripper0_right_finger_joint{suffix}").id
        data.qpos[model.jnt_qposadr[joint]] = model.jnt_range[joint, endpoint]
    mujoco.mj_forward(model, data)
    root = model.body("robot_0/gripper0_right_right_gripper").id
    site = model.site("robot_0/gripper0_right_grip_site").id
    root_world, tcp_world = np.eye(4), np.eye(4)
    root_world[:3, :3], root_world[:3, 3] = data.xmat[root].reshape(3, 3), data.xpos[root]
    tcp_world[:3, :3], tcp_world[:3, 3] = data.site_xmat[site].reshape(3, 3), data.site_xpos[site]
    root_to_tcp = np.linalg.inv(root_world) @ tcp_world
    triangles = visual_triangles(model, data, root)
    triangles = (triangles - root_to_tcp[:3, 3]) @ root_to_tcp[:3, :3]
    return PandaHand(model, data, site, triangles, root_to_tcp)
