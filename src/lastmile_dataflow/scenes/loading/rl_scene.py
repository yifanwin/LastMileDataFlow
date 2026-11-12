"""Prepare one fixed task-local XML topology using the existing scene loader."""

from dataclasses import dataclass

import mujoco
import numpy as np

from lastmile_dataflow.utils.io import REPO_ROOT
from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from ...runtime.simulation import Simulation
from .source import SceneSource
from lastmile_dataflow.robots.registry import load_robot_spec
from lastmile_dataflow.runtime.construction.state import migrate_state
from lastmile_dataflow.runtime.simulation import closed_on_error
from lastmile_dataflow.scenes.geometry import geom_points


@dataclass(frozen=True)
class RLSceneConfig:
    data_root: str
    scene_xml: str
    scene_metadata: str
    robot_xml: str
    robot_scale: float
    target: str = "Cup_30"
    local_radius_m: float = 2.0
    physics_dt: float = 0.002
    multiccd: bool = False
    home: tuple = (0.0, -0.00365045915, 0.0, -2.61799387799, 0.0, 2.74159265359, 0.78539816339)
    lift: float = 0.2

    def asset(self, name):
        return str(REPO_ROOT / self.data_root / name)


@dataclass
class PreparedRLScene:
    scene_spec: mujoco.MjSpec
    robot_spec: mujoco.MjSpec
    target_body: str
    target_position: np.ndarray
    initial_qpos: dict
    initial_ctrl: dict
    catalog: list
    local_model: dict
    multiccd: bool

    def copy_robot(self):
        return self.robot_spec.copy()

    def attach_scene(self, spec):
        # Attach keeps the parent's global settings anyway; matching them first avoids
        # MuJoCo's conflict warning, which otherwise writes MUJOCO_LOG.TXT into the cwd.
        scene = self.scene_spec.copy()
        _match_globals(spec.option, scene.option)
        _match_globals(spec.visual, scene.visual)
        for name in ("njmax", "nconmax", "nstack", "memory"):
            setattr(scene, name, getattr(spec, name))
        spec.attach(scene, prefix="", frame=spec.worldbody.add_frame())


def _match_globals(parent, child):
    for name in dir(parent):
        value = getattr(parent, name)
        if name.startswith("_") or callable(value):
            continue
        if isinstance(value, (bool, int, float, str, np.ndarray)):
            setattr(child, name, value)
        else:
            _match_globals(value, getattr(child, name))


def prepare_rl_scene(config):
    source = SceneSource(
        "val103-cup30", config.asset(config.scene_xml), config.asset(config.scene_metadata), "procthor-10k-val", "val"
    )
    robot = PandaOmronConfig(
        model_path=config.asset(config.robot_xml),
        physics_dt=config.physics_dt,
        cameras=("overview", "robot0_eye_in_hand"),
        initial={
            "base_xyyaw": [0.0, 0.0, 0.0],
            "lift": config.lift,
            "arm_q": {"right": list(config.home)},
            "gripper_width": {},
        },
    )
    sim = Simulation.from_source(source, robot, target=config.target, robot_scale=config.robot_scale)
    target = sim.model.body(sim.target_id).name
    anchor = sim.data.xpos[sim.target_id].copy()
    local = task_local_model(sim, {"anchor_world": anchor.tolist(), "target_body": target}, config.local_radius_m)
    scene_spec = local.spec.copy()
    robot_roots = [body for body in scene_spec.worldbody.bodies if body.name.startswith(robot.namespace)]
    for body in robot_roots:
        scene_spec.delete(body)
    used_meshes = {geom.meshname for geom in scene_spec.geoms if geom.meshname}
    for mesh in list(scene_spec.meshes):
        if mesh.name not in used_meshes:
            scene_spec.delete(mesh)

    robot_spec = load_robot_spec(robot, config.robot_scale)
    qpos = {
        local.model.joint(j).name: float(local.data.qpos[local.model.jnt_qposadr[j]])
        for j in range(local.model.njnt)
        if local.model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE
    }
    ctrl = {local.model.actuator(a).name: float(local.data.ctrl[a]) for a in range(local.model.nu)}
    prepared = PreparedRLScene(
        scene_spec, robot_spec, target, anchor, qpos, ctrl, local.catalog, local.local_model, config.multiccd
    )
    local.close()
    sim.close()
    return prepared


def root_body(model, body):
    while model.body_parentid[body] > 0:
        body = int(model.body_parentid[body])
    return body


def local_model_bounds(sim):
    """House geometry prepared once and reused by its task-local crops."""
    model = sim.model
    roots = {b: root_body(model, b) for b in range(1, model.nbody)}
    grouped = {}
    for geom in range(model.ngeom):
        body = int(model.geom_bodyid[geom])
        if body:
            grouped.setdefault(roots[body], []).append(geom)
    articulated = {
        roots[int(model.jnt_bodyid[j])] for j in range(model.njnt) if model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE
    }
    bounds = []
    for body, geoms in grouped.items():
        name = model.body(body).name
        structural = any(
            any(word in (model.geom(g).name or "").lower() for word in ("floor", "wall", "ceiling", "door"))
            for g in geoms
        )
        if (
            body in articulated
            or structural
            or name.startswith(sim.robot.config.namespace)
            or any(model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE for g in geoms)
        ):
            continue
        points = np.concatenate([geom_points(sim, g) for g in geoms])[:, :2]
        bounds.append((body, points.min(axis=0), points.max(axis=0)))
    return bounds


def task_local_model(sim, task, radius, bounds=None):
    """Keep complete roots meeting the station/sweep region; never crop by station.

    World geometry, floors, walls, doors and articulated structures stay whole.
    The target root includes its complete articulated object for open tasks.
    """
    spec = sim.spec.copy()
    model = sim.model
    anchor = np.asarray(task["anchor_world"][:2])
    target_root = root_body(model, model.body(task["target_body"]).id)
    removed = []
    for body, low, high in local_model_bounds(sim) if bounds is None else bounds:
        if body != target_root and (np.any(high < anchor - radius) or np.any(low > anchor + radius)):
            name = model.body(body).name
            spec.delete(spec.body(name))
            removed.append(name)
    meshes = {g.meshname for g in spec.geoms if g.meshname}
    for mesh in list(spec.meshes):
        if mesh.name not in meshes:
            spec.delete(mesh)
    local_model = spec.compile()
    catalog = []
    names = {local_model.body(b).name: b for b in range(local_model.nbody)}
    for entry in sim.catalog:
        if entry["mjcf_body"] in names:
            catalog.append({**entry, "body_id": names[entry["mjcf_body"]]})
    with closed_on_error(
        sim.derive(model=local_model, catalog=catalog, target=task["target_body"], spec=spec)
    ) as local:
        migrate_state(sim, local)
    local.local_model = {
        "radius_m": radius,
        "anchor_world": task["anchor_world"],
        "removed_roots": removed,
        "source_bodies": model.nbody,
        "retained_bodies": local_model.nbody,
    }
    return local
