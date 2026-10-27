#!/usr/bin/env python3
"""P2 same-state distance check: MuJoCo base geoms vs cuRobo base spheres.

For each diagnostic station, restore the frozen station state into a fresh,
un-begun simulation and compare, against the SAME scene geoms:
  - exact MuJoCo distance from the physical chassis/column geoms (mj_geomDistance)
  - distance from the cuRobo planning spheres of omron_base / panda_link0
Spheres are placed with the planner's own frame convention: the planning
reference is base_site (position) with the mobilebase0_base orientation, and
the URDF fixes omron_base at +0.21 m x and panda_link0 at +0.70 m z above it.
Output: one JSON per run, no copies of historical trials.
"""
import argparse
import json
from pathlib import Path

import mujoco
import numpy as np
import yaml

from lastmile_dataflow.config import robot_config
from lastmile_dataflow.runtime.simulation import Simulation

SPHERES = Path(__file__).resolve().parents[2] / 'src/lastmile_dataflow/planning/assets/panda_omron/spheres.yml'
# URDF fixed joints (assets/panda_omron/model.urdf): omron_root, torso_height origin.
LINK_OFFSETS = {'omron_base': np.array([.21, 0., 0.]), 'panda_link0': np.array([.21, 0., .70])}
PHYSICAL = {'omron_base': ('mobilebase0_pedestal_feet_col', 'mobilebase0_g0_col', 'mobilebase0_g1_col'),
            'panda_link0': ('robot0_link0_collision',)}
BUFFER_M = .002        # collision_sphere_buffer in planning/panda_omron.py
ACTIVATION_M = .01     # optimizer_collision_activation_distance in curobo_v2.py
REACH_M = 1.5


def load_station(entry):
    base = Path('outputs/_calibration_inputs') / entry['run']
    scene = next(json.loads(line) for line in (base / 'scenes.jsonl').read_text().splitlines()
                 if json.loads(line)['scene_id'] == entry['scene'])
    baseline = base / scene['scene']
    cfg = robot_config(json.loads((baseline / 'config.json').read_text())['robot'])
    sim = Simulation.from_snapshot(baseline, cfg, target=scene['task']['target_body'])
    sim.restore_snapshot(Path('outputs') / entry['run'] / 'preparations' / entry['scene'] / entry['station'] / 'state.npz')
    mujoco.mj_forward(sim.model, sim.data)
    return sim


def planning_frame(sim):
    m, d, ns = sim.model, sim.data, sim.robot.config.namespace
    frame = np.eye(4)
    frame[:3, :3] = d.xmat[m.body(ns + 'mobilebase0_base').id].reshape(3, 3)
    frame[:3, 3] = d.site_xpos[m.site(ns + 'base_site').id]
    return frame


def scene_geoms(sim, center):
    """Collidable non-robot, non-floor geoms whose bounding sphere is within reach."""
    m, d = sim.model, sim.data
    result = []
    for g in range(m.ngeom):
        body = m.body(int(m.geom_bodyid[g])).name
        if body.startswith(sim.robot.config.namespace) or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE or 'floor' in m.geom(g).name.lower():
            continue
        if np.linalg.norm(d.geom_xpos[g] - center) - m.geom_rbound[g] < REACH_M:
            result.append(g)
    return result


def planning_meshes(sim, frame, geoms):
    """Per-geom world meshes exactly as collision_world exports them to cuRobo."""
    import tempfile
    import trimesh
    from lastmile_dataflow.planning.curobo.collision_world import collision_world
    with tempfile.TemporaryDirectory() as tmp:
        collision_world(sim, Path(tmp), 2 * REACH_M, reference_base=frame)
    m, d = sim.model, sim.data
    meshes = {}
    for g in geoms:
        if g not in sim.planning_geometry:
            continue
        v, f = sim.planning_geometry[g]
        world = v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        meshes[g] = trimesh.Trimesh(vertices=world, faces=f, process=False)
    return meshes


def sphere_rows(sim, link, frame, meshes):
    import trimesh
    spheres = yaml.safe_load(SPHERES.read_text())['collision_spheres'][link]
    m = sim.model
    centers = np.array([frame[:3, :3] @ (LINK_OFFSETS[link] + np.asarray(s['center'])) + frame[:3, 3]
                        for s in spheres])
    distance = np.full(len(spheres), np.inf)
    nearest = [None] * len(spheres)
    for g, mesh in meshes.items():
        closest,_,_ = trimesh.proximity.closest_point(mesh, centers)
        value = np.linalg.norm(centers - closest, axis=1)
        better = value < distance
        distance[better] = value[better]
        for i in np.flatnonzero(better):
            nearest[i] = g
    rows = []
    for i, sphere in enumerate(spheres):
        clearance = float(distance[i] - sphere['radius'])
        g = nearest[i]
        rows.append({'sphere': i, 'center_world': centers[i].round(4).tolist(), 'radius': sphere['radius'],
                     'nearest_geom': m.geom(g).name if g is not None else None,
                     'nearest_body': m.body(int(m.geom_bodyid[g])).name if g is not None else None,
                     'clearance_raw_m': round(clearance, 4),
                     'clearance_buffered_m': round(clearance - BUFFER_M, 4),
                     'within_activation': bool(clearance - BUFFER_M < ACTIVATION_M)})
    return rows


def robot_geom_mesh(sim, geom):
    """World-frame surface mesh of one robot collision primitive or mesh."""
    import trimesh
    m, d = sim.model, sim.data
    kind, size = m.geom_type[geom], m.geom_size[geom]
    if kind == mujoco.mjtGeom.mjGEOM_BOX:
        mesh = trimesh.creation.box(extents=2 * size)
    elif kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
        mesh = trimesh.creation.cylinder(radius=size[0], height=2 * size[1], sections=48)
    elif kind == mujoco.mjtGeom.mjGEOM_MESH:
        i = int(m.geom_dataid[geom])
        va, vn, fa, fn = m.mesh_vertadr[i], m.mesh_vertnum[i], m.mesh_faceadr[i], m.mesh_facenum[i]
        mesh = trimesh.Trimesh(m.mesh_vert[va:va+vn].copy(), m.mesh_face[fa:fa+fn].copy(), process=False)
    else:
        raise ValueError(f'unsupported robot geom {m.geom(geom).name}')
    transform = np.eye(4)
    transform[:3, :3], transform[:3, 3] = d.geom_xmat[geom].reshape(3, 3), d.geom_xpos[geom]
    return mesh.apply_transform(transform)


def mesh_clearance(robot, scene, spacing=.01):
    """Clearance between two surface meshes; negative when scene vertices lie inside the robot solid."""
    import trimesh
    samples = np.vstack([robot.vertices, trimesh.sample.sample_surface_even(robot, 4000)[0]])
    _, distance, _ = trimesh.proximity.closest_point(scene, samples)
    clearance = float(distance.min())
    inside = robot.contains(scene.vertices) if robot.is_watertight else np.zeros(len(scene.vertices), bool)
    if inside.any():
        _, depth, _ = trimesh.proximity.closest_point(robot, scene.vertices[inside])
        clearance = -float(depth.max())
    return clearance, samples[int(distance.argmin())]


def physical_rows(sim, link, meshes):
    """Physical robot geoms against the SAME exported planning meshes the spheres see."""
    m, ns = sim.model, sim.robot.config.namespace
    rows = []
    for name in PHYSICAL[link]:
        robot = robot_geom_mesh(sim, m.geom(ns + name).id)
        best = (np.inf, None, None)
        for g, scene in meshes.items():
            if np.linalg.norm(scene.bounds.mean(0) - robot.bounds.mean(0)) > REACH_M + np.linalg.norm(scene.extents):
                continue
            value, point = mesh_clearance(robot, scene)
            if value < best[0]:
                best = (value, g, point)
        rows.append({'geom': name, 'distance_m': round(best[0], 4),
                     'nearest_geom': m.geom(best[1]).name if best[1] is not None else None,
                     'nearest_body': m.body(int(m.geom_bodyid[best[1]])).name if best[1] is not None else None,
                     'robot_point_world': best[2].round(4).tolist() if best[2] is not None else None})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('selection', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = []
    for entry in json.loads(args.selection.read_text()):
        sim = load_station(entry)
        frame = planning_frame(sim)
        geoms = scene_geoms(sim, frame[:3, 3])
        meshes = planning_meshes(sim, frame, geoms)
        station = {'group': entry['group'], 'op': entry['op'], 'house': entry['house'], 'run': entry['run'],
                   'scene': entry['scene'], 'station': entry['station'], 'rollouts': entry['rollouts'],
                   'base_pose_world': sim.robot.base_pose().round(4).tolist(), 'scene_geoms': len(geoms)}
        for link in ('omron_base', 'panda_link0'):
            spheres = sphere_rows(sim, link, frame, meshes)
            station[link] = {'physical': physical_rows(sim, link, meshes), 'spheres': spheres,
                             'min_sphere_clearance_raw_m': min(r['clearance_raw_m'] for r in spheres),
                             'spheres_within_activation': sum(r['within_activation'] for r in spheres)}
        report.append(station)
        print(f"{entry['group'][:14]:14s} {entry['op']:4s} {entry['house']:7s} {entry['station']} "
              f"base phys={min(r['distance_m'] for r in station['omron_base']['physical']):+.3f} "
              f"sphere={station['omron_base']['min_sphere_clearance_raw_m']:+.3f} "
              f"act={station['omron_base']['spheres_within_activation']:2d}/20 | "
              f"link0 phys={min(r['distance_m'] for r in station['panda_link0']['physical']):+.3f} "
              f"sphere={station['panda_link0']['min_sphere_clearance_raw_m']:+.3f}")
        sim.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
