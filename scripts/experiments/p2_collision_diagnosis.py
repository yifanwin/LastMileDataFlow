#!/usr/bin/env python3
"""P2 same-state geometry check: one robot part against one obstacle geom.

For each diagnostic station, restore the frozen station state into a fresh,
un-begun simulation and compare, for the SAME state and the SAME scene geom:
  - physics: `mj_geomDistance` between the robot collision geom (chassis box,
    fixed column, lift column, link0 mesh) and the scene geom, as MuJoCo
    collides them (mesh geoms as convex hulls);
  - planner: the signed distance of each planning sphere center to the
    exported triangle mesh of that geom (a cuRobo checker on that mesh
    alone, warp ray-cast sign), minus the effective sphere radius.
Sphere centers come from cuRobo FK of the planner's own PlanningModel (torso
lock, measured base frame); obstacle geoms from the production export rule.
The planner's whole-world verdict per sphere is read from checkers built by
`collision_world` exactly as the planner builds them. Known primitive cases
validate signs and call parameters first. Output: one JSON per run.
"""
import argparse
import itertools
import json
from pathlib import Path
import tempfile

import mujoco
import numpy as np

from lastmile_dataflow.config import robot_config
from n1_family_reuse_check import run_dataset
from lastmile_dataflow.planning.curobo.collision_world import collision_world, exported_geoms
from lastmile_dataflow.planning.robots.rby1m_mobile_torso import minimum_mesh_query_dimension, ordered_points
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.geometry import descendants

# Sphere groups of planning/robots/assets/panda_omron/spheres.yml and the
# MuJoCo collision geom each group covers. The lift column rides on
# panda_link0 (torso_height); the fixed column stays on omron_base. Groups are
# told apart by the sphere-center height in the link, so they follow sphere
# count changes: chassis spheres lie below the chassis top (z=0.382), fixed
# column spheres above it; lift column spheres below the panda_link0 origin,
# arm-base spheres above it.
PARTS = {
    'chassis': ('omron_base', lambda z: z < .382, 'mobilebase0_pedestal_feet_col'),
    'fixed_column': ('omron_base', lambda z: z >= .382, 'mobilebase0_g0_col'),
    'lift_column': ('panda_link0', lambda z: z < 0, 'mobilebase0_g1_col'),
    'link0': ('panda_link0', lambda z: z >= 0, 'robot0_link0_collision'),
}
ACTIVATION_M = .01   # optimizer_collision_activation_distance in planning/curobo/planner.py
NEAR_M = .15         # pairs whose enclosing spheres are farther apart are skipped
DISTMAX_M = 1.       # mj_geomDistance returns exactly distmax when nothing is closer
QUERY_M = 2.         # sphere-center SDF query reach of the per-geom checker
_mesh_names = itertools.count()  # warp meshes are cached by name; never reuse one


def geom_distance(m, d, g1, g2, distmax=DISTMAX_M):
    """Signed physics distance; values at distmax only bound the true distance."""
    fromto = np.zeros(6)
    value = float(mujoco.mj_geomDistance(m, d, g1, g2, distmax, fromto))
    truncated = value >= distmax
    # The witness segment must be as long as the reported distance (or depth).
    consistent = truncated or abs(np.linalg.norm(fromto[3:] - fromto[:3]) - abs(value)) < 1e-3
    return {'signed_distance_m': None if truncated else round(value, 5), 'truncated_at_m': distmax if truncated else None,
            'witness_consistent': bool(consistent),
            'from_world': None if truncated else fromto[:3].round(4).tolist(),
            'to_world': None if truncated else fromto[3:].round(4).tolist()}


def hull_points(m, d, g):
    """World points whose convex hull is the shape MuJoCo collides for this geom."""
    import trimesh
    kind, size = m.geom_type[g], m.geom_size[g]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        i = int(m.geom_dataid[g])
        local = m.mesh_vert[m.mesh_vertadr[i]:m.mesh_vertadr[i] + m.mesh_vertnum[i]]
    elif kind == mujoco.mjtGeom.mjGEOM_BOX:
        local = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * size
    elif kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
        # 128-gon: inscribed within 0.04 mm of a 0.12 m column
        local = trimesh.creation.cylinder(radius=size[0], height=2 * size[1], sections=128).vertices
    else:
        return None
    return np.asarray(local) @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]


def hull_distance(a, b):
    """Euclidean distance between two convex hulls (0 when they intersect), by a small QP."""
    from scipy.optimize import minimize
    from scipy.spatial import ConvexHull
    a, b = (p[ConvexHull(p).vertices] if len(p) > 4 else p for p in (a, b))
    na = len(a)
    objective = lambda w: np.sum((w[:na] @ a - w[na:] @ b) ** 2)
    constraints = [{'type': 'eq', 'fun': lambda w: w[:na].sum() - 1}, {'type': 'eq', 'fun': lambda w: w[na:].sum() - 1}]
    start = np.r_[np.full(na, 1 / na), np.full(len(b), 1 / len(b))]
    result = minimize(objective, start, bounds=[(0, 1)] * len(start), constraints=constraints, method='SLSQP',
                      options={'ftol': 1e-14, 'maxiter': 1000})
    return round(float(np.sqrt(max(result.fun, 0.))), 5) if result.success else None


def with_ccd(m, native, function):
    """Run `function` with native or legacy CCD on this in-memory diagnostic model only."""
    flag = int(mujoco.mjtDisableBit.mjDSBL_NATIVECCD)
    saved = m.opt.disableflags
    m.opt.disableflags = saved & ~flag if native else saved | flag
    try:
        return function()
    finally:
        m.opt.disableflags = saved


def native_ccd(m):
    return not m.opt.disableflags & int(mujoco.mjtDisableBit.mjDSBL_NATIVECCD)


class SphereSDF:
    """cuRobo mesh checkers queried for sphere-center signed distance (negative inside)."""
    def __init__(self):
        from curobo.types import DeviceCfg
        self.device = DeviceCfg()

    def checker(self, scene, min_dimension):
        from curobo._src.geom.collision.collision_scene import SceneCollision, SceneCollisionCfg
        checker = SceneCollision.from_config(SceneCollisionCfg(device_cfg=self.device, scene_model=scene,
                                                               cache={'mesh': max(1, len(scene.mesh or ()))}))
        # Same query-bound padding as V2Planner.pad_target_query_range.
        for data in checker.data.get_valid_data():
            if hasattr(data, 'dims'):
                data.dims[..., :3].clamp_(min=min_dimension)
        return checker

    def mesh(self, vertices, faces):
        from curobo._src.geom.types import Mesh, SceneCfg
        return self.checker(SceneCfg(mesh=[Mesh(name=f'diagnostic_{next(_mesh_names)}', pose=[0, 0, 0, 1, 0, 0, 0],
                                                vertices=np.asarray(vertices).tolist(), faces=np.asarray(faces).tolist())]),
                            2 * QUERY_M + 1)

    def penetration(self, checker, spheres):
        """Planner penetration `radius - sdf(center)` (> 0 means collision), zero otherwise."""
        import torch
        from curobo._src.geom.collision.buffer_collision import CollisionBuffer
        query = self.device.to_device(np.asarray(spheres, np.float32)).view(1, 1, -1, 4).contiguous()
        buffer = CollisionBuffer.from_shape(query.shape, self.device)
        value = checker.get_sphere_distance_raw(query, buffer, self.device.to_device([1.]),
                                                self.device.to_device([0.]))
        return value.reshape(-1).detach().cpu().numpy().astype(float)

    def center_sdf(self, checker, centers):
        """Signed distance of points; None beyond the QUERY_M reach."""
        spheres = np.c_[centers, np.full(len(centers), QUERY_M)]
        value = self.penetration(checker, spheres)
        return [round(QUERY_M - v, 5) if v > 0 else None for v in value]


def plain(value):
    """numpy scalars in the report."""
    return value.item()


def known_cases(sdf):
    """Primitive pairs with analytic answers: sign, truncation and witness points."""
    m = mujoco.MjModel.from_xml_string("""<mujoco><asset>
      <mesh name="cube" vertex="-.1 -.1 -.1 .1 -.1 -.1 -.1 .1 -.1 .1 .1 -.1 -.1 -.1 .1 .1 -.1 .1 -.1 .1 .1 .1 .1 .1"/>
      </asset><worldbody>
      <body name="a" pos="0 0 1"><geom name="box" type="box" size=".1 .1 .1"/></body>
      <body name="b" mocap="true" pos=".3 0 1"><geom name="probe_box" type="box" size=".1 .1 .1"/></body>
      <body name="c" mocap="true" pos="0 .3 1"><geom name="probe_mesh" type="mesh" mesh="cube"/></body>
      <body name="e" mocap="true" pos="0 -.3 1"><geom name="probe_cylinder" type="cylinder" size=".1 .1"/></body>
      </worldbody></mujoco>""")
    d = mujoco.MjData(m)
    cases = []
    # (probe, mocap position, expected signed distance from the fixed box)
    for probe, index, position, expected in (
            ('probe_box', 0, [.3, 0, 1], .1), ('probe_box', 0, [.15, 0, 1], -.05), ('probe_box', 0, [3., 0, 1], None),
            ('probe_mesh', 1, [0, .3, 1], .1), ('probe_mesh', 1, [0, .15, 1], -.05), ('probe_mesh', 1, [0, 3., 1], None),
            ('probe_cylinder', 2, [0, -.3, 1], .1), ('probe_cylinder', 2, [0, -.15, 1], -.05)):
        d.mocap_pos[index] = position
        mujoco.mj_forward(m, d)
        for native in (True, False):
            row = with_ccd(m, native, lambda: geom_distance(m, d, m.geom('box').id, m.geom(probe).id))
            value = row['signed_distance_m']
            ok = (value is None) if expected is None else (value is not None and abs(value - expected) < 2e-3)
            cases.append({'kind': 'mj_geomDistance', 'pair': ['box', probe], 'probe_position': position,
                          'native_ccd': native, 'expected_m': expected, **row, 'correct': bool(ok)})
    # Planner side: a closed box mesh and an open single-sided quad ("wall" without thickness).
    import trimesh
    closed = trimesh.creation.box(extents=[.2, .2, .2])
    points = np.array([[.4, 0, 0], [.05, 0, 0], [0, 0, 0]])
    values = sdf.center_sdf(sdf.mesh(closed.vertices, closed.faces), points)
    for point, value, expected in zip(points, values, (.3, -.05, -.1)):
        cases.append({'kind': 'curobo_mesh_sdf', 'mesh': 'closed box 0.2 m', 'point': point.tolist(),
                      'expected_m': expected, 'sdf_m': value,
                      'correct': bool(value is not None and abs(value - expected) < 2e-3)})
    quad = sdf.mesh([[0, -1, -1], [0, 1, -1], [0, 1, 1], [0, -1, 1]], [[0, 1, 2], [0, 2, 3]])
    for point, value in zip(([.1, 0, 0], [-.1, 0, 0]), sdf.center_sdf(quad, np.array([[.1, 0, 0], [-.1, 0, 0]]))):
        # No inside for an open surface: recorded, not asserted.
        cases.append({'kind': 'curobo_mesh_sdf', 'mesh': 'open quad x=0', 'point': point, 'sdf_m': value,
                      'expected_m': None, 'correct': None})
    return cases


def load_station(entry):
    # The run's own input directory, absolute: read_config resolves relative paths against the package root.
    base = run_dataset(Path('outputs') / entry['run'])
    scene = next(json.loads(line) for line in (base / 'scenes.jsonl').read_text().splitlines()
                 if json.loads(line)['scene_id'] == entry['scene'])
    baseline = base / scene['scene']
    cfg = robot_config(json.loads((baseline / 'config.json').read_text())['robot'])
    task = scene['task']
    sim = Simulation.from_snapshot(baseline, cfg, target=task['target_body'])
    if task.get('handle_body'):
        sim.handle_id = sim.model.body(task['handle_body']).id
    sim.restore_snapshot(Path('outputs') / entry['run'] / 'preparations' / entry['scene'] / entry['station'] / 'state.npz')
    mujoco.mj_forward(sim.model, sim.data)
    config = ExecutionConfig(**json.loads((Path('outputs') / entry['run'] / 'config.json').read_text())['config'])
    return sim, task, config


def planning_spheres(sim, model, device):
    """World sphere centers and effective radii from cuRobo FK of the planner's model."""
    from curobo._src.robot.kinematics.kinematics import Kinematics
    from curobo._src.types.robot import RobotCfg
    from curobo.types import JointState
    kinematics = Kinematics(RobotCfg.create(model.cfg, device_cfg=device).kinematics)
    q = ordered_points(model.current_joints(), list(model.names), list(kinematics.joint_names))
    state = kinematics.compute_kinematics(JointState.from_position(
        device.to_device(np.asarray(q, np.float32)).view(1, -1), joint_names=list(kinematics.joint_names)))
    local = state.robot_spheres.reshape(-1, 4).detach().cpu().numpy().astype(float)
    params = kinematics.config.kinematics_config
    listed = model.cfg['kinematics']['collision_spheres']
    groups = {part: [i for i, s in enumerate(listed[link]) if rule(s['center'][2])]
              for part, (link, rule, _) in PARTS.items()}
    for link in {link for link, _, _ in PARTS.values()}:
        if len(params.get_sphere_index_from_link_name(link)) != len(listed[link]):
            raise ValueError(f'loaded {link} spheres differ from the configuration')
    result = {}
    for part, (link, _, _) in PARTS.items():
        indices = groups[part]
        rows = params.get_sphere_index_from_link_name(link).tolist()
        chosen = local[[rows[i] for i in indices]]
        world = chosen[:, :3] @ model.base[:3, :3].T + model.base[:3, 3]
        result[part] = {'link': link, 'link_sphere_indices': list(indices), 'local': chosen,
                        'centers_world': world, 'radii': chosen[:, 3]}
    return result


def physical_geoms(sim):
    m, ns = sim.model, sim.robot.config.namespace
    return {part: m.geom(ns + name).id for part, (_, _, name) in PARTS.items()}


def chassis_uncovered(sim, s, buffer, step=.01):
    """Largest distance from a point of the physical chassis box (1 cm grid, faces and
    corners included) to the nearest chassis sphere without the buffer; <= 0 means covered."""
    m, d = sim.model, sim.data
    g = physical_geoms(sim)['chassis']
    axes = [np.linspace(-h, h, int(np.ceil(2 * h / step)) + 1) for h in m.geom_size[g]]
    local = np.stack(np.meshgrid(*axes, indexing='ij'), -1).reshape(-1, 3)
    world = local @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
    gap = np.linalg.norm(world[:, None] - s['centers_world'][None], axis=-1) - (s['radii'] - buffer)[None]
    return round(float(gap.min(axis=1).max()), 5)


def parts_uncovered(sim, spheres, buffer, step=.005):
    """Per robot part: largest distance from a sample of its solid (grid inside plus surface;
    a mesh as its convex hull) to the nearest sphere of any part without the buffer. The
    union is used because each group only covers the exposed solid (the fixed column below
    the chassis top, the lift column inside the fixed column); <= 0 means covered."""
    import trimesh
    m, d = sim.model, sim.data
    centers = np.concatenate([s['centers_world'] for s in spheres.values()])
    radii = np.concatenate([s['radii'] for s in spheres.values()]) - buffer
    result = {}
    for part, g in physical_geoms(sim).items():
        size = m.geom_size[g]
        if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mesh = int(m.geom_dataid[g]); va, vn = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
            solid = trimesh.convex.convex_hull(m.mesh_vert[va:va + vn])
        elif m.geom_type[g] == mujoco.mjtGeom.mjGEOM_CYLINDER:
            solid = trimesh.creation.cylinder(radius=size[0], height=2 * size[1], sections=256)
        else:
            solid = trimesh.creation.box(extents=2 * size)
        lo, hi = solid.bounds
        grid = np.stack(np.meshgrid(*[np.arange(lo[k], hi[k] + 1e-9, step) for k in range(3)], indexing='ij'), -1).reshape(-1, 3)
        local = np.r_[grid[solid.contains(grid)], solid.vertices, solid.sample(20000)]
        world = local @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        gap = np.concatenate([(np.linalg.norm(w[:, None] - centers[None], axis=-1) - radii[None]).min(1)
                              for w in np.array_split(world, max(1, len(world) // 20000))])
        result[part] = round(float(gap.max()), 5)
    return result


def world_verdict(sim, model, sdf, spheres, range_m, directory):
    """Per-part penetration in the planner's own environment/target/handle checkers."""
    target = descendants(sim.model, sim.target_id)
    handle = descendants(sim.model, sim.handle_id) if sim.handle_id else set()
    scenes = {'environment': collision_world(sim, directory, range_m, reference_base=model.base),
              'target': collision_world(sim, directory, range_m, reference_base=model.base,
                                        target_only=True, bodies=target - handle)}
    if handle:
        scenes['handle'] = collision_world(sim, directory, range_m, reference_base=model.base, target_only=True,
                                           bodies=handle, filename='handle_world.json')
    largest = max(float(s['radii'].max()) for s in spheres.values())
    result = {}
    for name, scene in scenes.items():
        if not scene.mesh:
            continue
        padding = minimum_mesh_query_dimension(largest) if name != 'environment' else 0.
        checker = sdf.checker(scene, padding)
        for part, s in spheres.items():
            depth = sdf.penetration(checker, s['local'])
            near = sdf.penetration(checker, np.c_[s['local'][:, :3], s['radii'] + ACTIVATION_M])
            row = result.setdefault(part, {})
            row[name] = {'colliding_spheres': int((depth > 0).sum()), 'max_penetration_m': round(float(depth.max()), 5),
                         'within_activation_spheres': int((near > 0).sum())}
    return result


def pair_rows(sim, sdf, spheres, worlds):
    m, d = sim.model, sim.data
    robot = physical_geoms(sim)
    reach = {part: max(float(m.geom_rbound[g]), float((np.linalg.norm(spheres[part]['centers_world'] - d.geom_xpos[g], axis=1)
                                                       + spheres[part]['radii']).max()))
             for part, g in robot.items()}
    rows = []
    for world, geoms in worlds.items():
        for g, vertices, faces in geoms:
            checker = None
            for part, g_robot in robot.items():
                gap = np.linalg.norm(d.geom_xpos[g] - d.geom_xpos[g_robot]) - m.geom_rbound[g] - reach[part]
                if gap > NEAR_M:
                    continue
                checker = checker or sdf.mesh(vertices, faces)
                rows.append(pair_row(m, d, sdf, checker, spheres[part], part, g_robot, world, g, gap))
    return rows


def physical_distance(m, d, g_robot, g):
    """mj_geomDistance with the hull-QP check; `accepted_m` is None when neither is reliable."""
    physics = geom_distance(m, d, g_robot, g)
    value = physics['signed_distance_m']
    if not physics['witness_consistent'] or (value is not None and value < .1):
        a, b = hull_points(m, d, g_robot), hull_points(m, d, g)
        physics['hull_distance_m'] = hull_distance(a, b) if a is not None and b is not None else None
    hull = physics.get('hull_distance_m')
    # A positive hull QP minimum certifies separation; at contact/overlap only a
    # self-consistent non-positive mj_geomDistance supplies the depth.
    if hull is None:
        accepted = value if physics['witness_consistent'] else None
    elif hull > 1e-3:
        accepted = hull
    else:
        accepted = value if physics['witness_consistent'] and value is not None and value <= 1e-3 else None
    physics['accepted_m'] = accepted
    return physics


def pair_row(m, d, sdf, checker, s, part, g_robot, world, g, gap):
    physics = physical_distance(m, d, g_robot, g)
    alternate = with_ccd(m, not native_ccd(m), lambda: geom_distance(m, d, g_robot, g))
    centers = sdf.center_sdf(checker, s['centers_world'])
    clearance = [None if c is None else round(c - r, 5) for c, r in zip(centers, s['radii'])]
    known = [(c, i) for i, c in enumerate(clearance) if c is not None]
    best = min(known) if known else (None, None)
    return {
        'part': part, 'physical_geom': m.geom(g_robot).name, 'world': world,
        'obstacle_geom': m.geom(g).name, 'obstacle_body': m.body(int(m.geom_bodyid[g])).name,
        'obstacle_type': mujoco.mjtGeom(int(m.geom_type[g])).name, 'enclosing_gap_m': round(float(gap), 4),
        'physics': {**physics, 'native_ccd': native_ccd(m),
                    'alternate_ccd': {**alternate, 'native_ccd': not native_ccd(m)}},
        'planner': {'quantity': 'sdf(sphere center to exported mesh) - effective radius',
                    'min_m': best[0], 'sphere': best[1],
                    'center_world': None if best[1] is None else s['centers_world'][best[1]].round(4).tolist(),
                    'radius_m': None if best[1] is None else round(float(s['radii'][best[1]]), 4),
                    'negative_spheres': sum(c < 0 for c, _ in known),
                    'within_activation_spheres': sum(c < ACTIVATION_M for c, _ in known),
                    'beyond_query_spheres': len(clearance) - len(known)}}


def figure(sim, spheres, rows, path, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Polygon
    m, d = sim.model, sim.data
    colors = {'chassis': '#3b915d', 'fixed_column': '#2f6fb0', 'lift_column': '#b06f2f', 'link0': '#7a4fb0'}
    fig, ax = plt.subplots(figsize=(7, 7))
    names = {r['obstacle_geom'] for r in rows if r['planner']['min_m'] is not None and r['planner']['min_m'] < .1}
    for g in range(m.ngeom):
        if m.geom(g).name in names and g in sim.planning_geometry:
            v, f = sim.planning_geometry[g]
            world = v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
            for face in f:
                ax.add_patch(Polygon(world[face, :2], closed=True, fc='#e3e5e8', ec='#9aa0a6', lw=.3, alpha=.5))
            ax.text(*d.geom_xpos[g, :2], m.geom(g).name.split('/')[-1][-24:], fontsize=6, color='#6b7280')
    for part, s in spheres.items():
        for c, r in zip(s['centers_world'], s['radii']):
            ax.add_patch(Circle(c[:2], r, fill=False, ec=colors[part], lw=.8, alpha=.7))
    ns = sim.robot.config.namespace
    box = m.geom(ns + PARTS['chassis'][2]).id
    corners = np.array([[sx, sy, 0] for sx, sy in ((1, 1), (1, -1), (-1, -1), (-1, 1))]) * m.geom_size[box]
    ax.add_patch(Polygon((corners @ d.geom_xmat[box].reshape(3, 3).T + d.geom_xpos[box])[:, :2], closed=True,
                         fill=False, ec='#1f2328', lw=1.5, label='physical chassis box'))
    for part in ('fixed_column', 'lift_column'):
        g = m.geom(ns + PARTS[part][2]).id
        ax.add_patch(Circle(d.geom_xpos[g, :2], m.geom_size[g, 0], fill=False, ec='#1f2328', lw=1, ls='--'))
    for r in rows:
        if r['physics']['witness_consistent'] and r['physics']['from_world'] is not None and r['physics']['signed_distance_m'] < .1:
            a, b = np.array(r['physics']['from_world']), np.array(r['physics']['to_world'])
            ax.plot([a[0], b[0]], [a[1], b[1]], color='#d1242f', lw=1.2)
    center = d.geom_xpos[box, :2]
    ax.set_xlim(center[0] - 1.2, center[0] + 1.2)
    ax.set_ylim(center[1] - 1.2, center[1] + 1.2)
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=9)
    ax.legend(handles=[plt.Line2D([], [], color=c, label=f'{p} spheres') for p, c in colors.items()]
              + [plt.Line2D([], [], color='#1f2328', label='physical box / columns (dashed)'),
                 plt.Line2D([], [], color='#d1242f', label='mj_geomDistance witness < 0.1 m')], fontsize=7, loc='upper right')
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('selection', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stations', nargs='*', help='house/station keys, e.g. val103/S0000; default all')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sdf = SphereSDF()
    cases = known_cases(sdf)
    report = {'method_checks': cases, 'mujoco_version': mujoco.__version__,
              'near_m': NEAR_M, 'distmax_m': DISTMAX_M, 'activation_m': ACTIVATION_M, 'stations': []}
    if not all(c['correct'] is not False for c in cases):
        args.output.write_text(json.dumps(report, indent=1, default=plain))
        raise SystemExit('known geometry cases failed; station measurements not attempted')
    for entry in json.loads(args.selection.read_text()):
        if args.stations and f"{entry['house']}/{entry['station']}" not in args.stations:
            continue
        sim, task, config = load_station(entry)
        with tempfile.TemporaryDirectory() as tmp:
            model = sim.robot.planning_model(sim, config, sim.robot.arms[0], tmp, (task['anchor_world'], config.radius_m))
            range_m = 2 * config.radius_m + 1.  # V2Planner.world_range
            spheres = planning_spheres(sim, model, sdf.device)
            target = descendants(sim.model, sim.target_id)
            handle = descendants(sim.model, sim.handle_id) if sim.handle_id else set()
            worlds = {'environment': list(exported_geoms(sim, model.base, range_m)),
                      'target': list(exported_geoms(sim, model.base, range_m, target_only=True, bodies=target - handle))}
            if handle:
                worlds['handle'] = list(exported_geoms(sim, model.base, range_m, target_only=True, bodies=handle))
            rows = pair_rows(sim, sdf, spheres, worlds)
            verdict = world_verdict(sim, model, sdf, spheres, range_m, Path(tmp))
        m = sim.model
        ns = sim.robot.config.namespace
        station = {key: entry[key] for key in ('group', 'op', 'house', 'run', 'scene', 'station', 'rollouts')}
        station.update(
            base_pose_world=sim.robot.base_pose().round(4).tolist(),
            torso_qpos_m=round(float(sim.data.qpos[m.jnt_qposadr[m.joint(ns + 'mobilebase0_joint_torso_height').id]]), 4),
            planner_torso_lock_m=float(sim.robot.lift_target),
            planning_frame_world=model.base.round(5).tolist(), native_ccd=native_ccd(m),
            ccd_iterations=int(m.opt.ccd_iterations), ccd_tolerance=float(m.opt.ccd_tolerance),
            exported_geoms={k: len(v) for k, v in worlds.items()}, planner_world_verdict=verdict,
            chassis_box_max_uncovered_m=chassis_uncovered(
                sim, spheres['chassis'], model.cfg['kinematics']['collision_sphere_buffer']),
            parts_max_uncovered_m=parts_uncovered(
                sim, spheres, model.cfg['kinematics']['collision_sphere_buffer']),
            spheres={part: {'link': s['link'], 'link_sphere_indices': s['link_sphere_indices'],
                            'centers_world': s['centers_world'].round(4).tolist(), 'radii_m': s['radii'].round(4).tolist()}
                     for part, s in spheres.items()},
            pairs=rows)
        figure_path = args.output.with_name(f"{args.output.stem}-{entry['house']}-{entry['station']}.png")
        figure(sim, spheres, rows, figure_path, f"{entry['house']} {entry['station']} {entry['op']} (top view, world XY)")
        station['figure'] = figure_path.name
        report['stations'].append(station)
        near = [r for r in rows if r['planner']['min_m'] is not None]
        worst = min(near, key=lambda r: r['planner']['min_m'], default=None)
        print(f"{entry['house']:7s} {entry['station']} pairs={len(rows):3d} "
              + (f"worst sphere {worst['part']}->{worst['obstacle_geom'].split('/')[-1]} {worst['planner']['min_m']:+.3f} "
                 f"physics accepted {worst['physics']['accepted_m']} (mj {worst['physics']['signed_distance_m']})" if worst else 'no near pair'))
        sim.close()
    args.output.write_text(json.dumps(report, indent=1, default=plain))


if __name__ == '__main__':
    main()
