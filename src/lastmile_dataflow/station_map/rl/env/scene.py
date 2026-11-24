"""Prepare one fixed task-local XML topology using the existing scene loader."""

from dataclasses import dataclass
import json
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.utils.io import REPO_ROOT
from lastmile_dataflow.robots.interface import ROBOT_NAMESPACE
from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from lastmile_dataflow.sim.simulation import Simulation
from lastmile_dataflow.sim.source import SceneSource
from lastmile_dataflow.robots.registry import load_robot_spec
from lastmile_dataflow.sim.state import migrate_state
from lastmile_dataflow.sim.simulation import closed_on_error
from lastmile_dataflow.sim.model.geometry import extract_regions, geom_points, support_rays


# mjlab names every element of an entity "<entity>/<name>"; the robot is the entity "robot",
# so its elements are "robot/" + the protocol namespace.
ROBOT_ENTITY = "robot"
ENTITY_PREFIX = ROBOT_ENTITY + "/"
ROBOT_PREFIX = ENTITY_PREFIX + ROBOT_NAMESPACE


@dataclass(frozen=True)
class EntityPandaConfig(PandaOmronConfig):
    """PandaOmron as compiled into the mjlab scene: element names carry the entity prefix."""

    @property
    def namespace(self):
        return ROBOT_PREFIX


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
    cube: dict | None = None
    fixed_base: bool = False
    # Remove the torso joint too (fixed at 0.00 m), so the arm is the 7-DoF chain of every evaluator.
    lock_torso: bool = False
    # Scene-builder run directory whose accepted edit adds obstacles (procedural_obstacle_*) to this scene.
    edit_scene: str | None = None

    def asset(self, name):
        return str(REPO_ROOT / self.data_root / name)

    def robot_config(self, *, entity=False):
        """PandaOmron for this scene; ``entity=True`` names its elements as in the compiled mjlab scene."""
        cls = EntityPandaConfig if entity else PandaOmronConfig
        return cls(
            model_path=self.asset(self.robot_xml),
            physics_dt=self.physics_dt,
            initial={
                "base_xyyaw": [0.0, 0.0, 0.0],
                "lift": self.lift,
                "arm_q": {"right": list(self.home)},
                "gripper_width": {},
            },
        )


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
    reference_model: mujoco.MjModel | None = None
    support_height_m: float | None = None
    support_body: str | None = None

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
    robot = config.robot_config()
    sim = Simulation.from_source(source, robot, target=config.target, robot_scale=config.robot_scale)
    if config.edit_scene is not None:
        sim = add_edit_obstacles(sim, config.edit_scene)
    support_height = target_support_height(sim, sim.data.xpos[sim.target_id, None, :2])
    support = target_support_body(sim)
    if config.cube is not None:
        cube = config.cube
        if set(cube) != {"size_m", "mass_kg"} or cube["size_m"] <= 0 or cube["mass_kg"] <= 0:
            raise ValueError("cube requires positive size_m and mass_kg")
        replacement = replace_with_cube(sim, cube["size_m"], cube["mass_kg"])
        sim.close()
        sim = replacement
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
    if config.fixed_base:
        fix_robot_base(robot_spec, torso=config.lock_torso)
    robot_joints = {joint.name for joint in robot_spec.joints}
    qpos = {
        local.model.joint(j).name: float(local.data.qpos[local.model.jnt_qposadr[j]])
        for j in range(local.model.njnt)
        if local.model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE
        and local.model.joint(j).name in robot_joints
    }
    ctrl = {local.model.actuator(a).name: float(local.data.ctrl[a]) for a in range(local.model.nu)}
    prepared = PreparedRLScene(
        scene_spec, robot_spec, target, anchor, qpos, ctrl, local.catalog, local.local_model, config.multiccd,
        local.model if config.fixed_base else None, support_height, support,
    )
    local.close()
    sim.close()
    return prepared


def add_edit_obstacles(sim, run_dir):
    """Copy the obstacles of an accepted scene edit into this scene at their placement poses."""
    run_dir = Path(run_dir)
    record = [json.loads(line) for line in (run_dir / "scenes.jsonl").open()]
    record = [r for r in record if r["status"] == "accepted"][-1]
    placements = record["edits"]["placements"]
    names = [f"procedural_obstacle_{i}" for i in range(len(placements))]
    edit = mujoco.MjSpec.from_file(str(run_dir / record["scene"] / "model.xml"))
    for body in list(edit.worldbody.bodies):
        if body.name not in names:
            edit.delete(body)
    meshes = {g.meshname for g in edit.geoms if g.meshname}
    materials = {g.material for g in edit.geoms if g.material}
    for elements, keep in ((edit.meshes, meshes), (edit.materials, materials)):
        for item in list(elements):
            if item.name not in keep:
                edit.delete(item)
    textures = {t for m in edit.materials for t in m.textures if t}
    for texture in list(edit.textures):
        if texture.name not in textures:
            edit.delete(texture)
    spec = sim.spec.copy()
    for name, placement in zip(names, placements):
        body = edit.body(name)
        body.pos, body.quat = placement["pose"]["position"], placement["pose"]["quaternion_wxyz"]
        spec.worldbody.add_frame().attach_body(body, "", "")
    with closed_on_error(sim.derive(model=spec.compile(), spec=spec)) as edited:
        migrate_state(sim, edited)
    sim.close()
    return edited


def fix_robot_base(spec, *, torso=False):
    """Remove planar dynamics (and optionally the torso, leaving it at 0.00 m); mjlab wraps this robot in a mocap root."""
    names = [f"mobile_{suffix}" for suffix in ("forward", "side", "yaw")] + (["torso_height"] if torso else [])
    for name in names:
        spec.delete(spec.actuator(ROBOT_NAMESPACE + "mobilebase0_actuator_" + name))
        spec.delete(spec.joint(ROBOT_NAMESPACE + "mobilebase0_joint_" + name))


def target_support_body(sim):
    """MJCF body of the scene instance the target originally stands on."""
    target = sim.model.body(sim.target_id).name
    entry = next(item for item in sim.catalog if item["mjcf_body"] == target)
    return next(item["mjcf_body"] for item in sim.catalog if item["instance_id"] == entry["parent_instance_id"])


def target_support_height(sim, xy):
    """Read the target's original physical tabletop from its scene parent."""
    target = sim.model.body(sim.target_id).name
    support = target_support_body(sim)
    regions = []
    for region in extract_regions(sim, support):
        points = np.column_stack((xy, np.full(len(xy), region.height)))
        if region.contains(points, tolerance=-0.002) and support_rays(sim, sim.target_id, region, points):
            regions.append(region)
    if not regions:
        raise ValueError(f"No physical support at original {target} XY")
    return max(region.height for region in regions)


def replace_with_cube(sim, size_m, mass_kg):
    """Replace the source cup in memory on its original dining-table support."""
    target = sim.model.body(sim.target_id).name
    xy = sim.data.xpos[sim.target_id, :2].copy()
    half = size_m / 2
    corners = xy + np.array([[-half, -half], [-half, half], [half, -half], [half, half]])
    height = target_support_height(sim, corners)
    spec = sim.spec.copy()
    spec.delete(spec.body(target))
    body = spec.worldbody.add_body(name="RL_Cube", pos=[*xy, height + half])
    body.add_freejoint(name="RL_Cube_free")
    body.add_geom(
        name="RL_Cube_geom", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[half, half, half], mass=mass_kg, rgba=[0.95, 0.32, 0.15, 1.0],
    )
    model = spec.compile()
    catalog = [item for item in sim.catalog if item["mjcf_body"] != target]
    catalog.append({
        "instance_id": "RL_Cube", "mjcf_body": "RL_Cube",
        "body_id": model.body("RL_Cube").id, "asset_id": None,
        "category": "Cube", "name_map": {},
        "pose_frame": "world", "pose_order": "xyz_wxyz",
    })
    with closed_on_error(sim.derive(model=model, catalog=catalog, target="RL_Cube", spec=spec)) as replaced:
        migrate_state(sim, replaced)
    return replaced


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
