"""Fixed task-local models, retaining complete object and articulated subtrees."""
from copy import deepcopy

import mujoco
import numpy as np

from ...runtime.construction.state import migrate_state
from ...runtime.simulation import Simulation
from ..geometry import geom_points


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
    articulated = {roots[int(model.jnt_bodyid[j])] for j in range(model.njnt)
                   if model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE}
    bounds = []
    for body, geoms in grouped.items():
        name = model.body(body).name
        structural = any(any(word in (model.geom(g).name or '').lower()
                             for word in ('floor', 'wall', 'ceiling', 'door')) for g in geoms)
        if (body in articulated or structural or name.startswith(sim.robot.config.namespace)
                or any(model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE for g in geoms)):
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
    anchor = np.asarray(task['anchor_world'][:2])
    target_root = root_body(model, model.body(task['target_body']).id)
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
        if entry['mjcf_body'] in names:
            catalog.append({**deepcopy(entry), 'body_id': names[entry['mjcf_body']]})
    local = Simulation(local_model, sim.robot.config, catalog, task['target_body'])
    local.source, local.restoration, local.spec = sim.source, None, spec
    migrate_state(sim, local)
    local.local_model = {'radius_m': radius, 'anchor_world': task['anchor_world'],
                         'removed_roots': removed, 'source_bodies': model.nbody,
                         'retained_bodies': local_model.nbody}
    return local
