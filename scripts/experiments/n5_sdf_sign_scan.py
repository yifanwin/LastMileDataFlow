#!/usr/bin/env python3
"""N5: sign reliability of the planner's environment mesh SDF.

`collision_world` merges every exported environment geom into ONE cuRobo mesh.
Warp's `mesh_query_point` decides inside/outside by ray parity, which is only
reliable on watertight meshes; with open raw mesh geoms merged, free space was
marked deep inside and the sign depended on the frame of the mesh. The fix
exports mesh geoms as their convex hulls (closed, as MuJoCo collides them), so
the merged mesh below IS the production world. For each station state this
script samples points around the robot and compares, for the same exported geoms:
  - merged mesh in the planning frame (what the planner queries);
  - the same merged mesh in the world frame (a rigid transform of it);
  - each geom on its own mesh (reference: minimum of the per-geom signed distances).
Reported per station: points near surfaces where the planner says inside but the
reference says outside (false collision) and the reverse (missed collision; with
closed hulls these lie where hulls overlap, where parity counts two crossings).
`collision_world` itself is also probed as a collision test: a PROBE_M sphere at
each point, query range padded as the planner pads target worlds. Query times are
reported. Results of the raw-mesh world before the fix: n5-sdf-sign-20261028/.
Output: one JSON.
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

import numpy as np

PROBE_M = .005     # probe sphere radius for the production collision test
NEAR_M = .3        # only points this close to some surface matter for robot spheres
REACH_M = 1.5      # sampled cylinder around the planning frame origin
HEIGHT_M = 1.6
PADDING_M = 5.     # query range of every checker; the sign does not depend on it (checked on val10)


def checker(sdf, meshes, padding):
    from curobo._src.geom.types import Mesh, SceneCfg
    import p2_collision_diagnosis as p2
    scene = SceneCfg(mesh=[Mesh(name=f'sign_scan_{next(p2._mesh_names)}', pose=[0, 0, 0, 1, 0, 0, 0],
                                vertices=np.asarray(v).tolist(), faces=np.asarray(f).tolist()) for v, f in meshes])
    return sdf.checker(scene, padding)


def query(sdf, chk, points):
    started = time.perf_counter()
    values = sdf.center_sdf(chk, points)
    return np.array([np.nan if v is None else v for v in values]), time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('selection', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--points', type=int, default=20000)
    parser.add_argument('--stations', nargs='*', help='house/station keys; default all')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import p2_collision_diagnosis as p2
    from lastmile_dataflow.planning.curobo.collision_world import collision_world, exported_geoms
    from lastmile_dataflow.planning.robots.rby1m_mobile_torso import minimum_mesh_query_dimension
    sdf = p2.SphereSDF()
    rows = []
    for entry in json.loads(args.selection.read_text()):
        if args.stations and f"{entry['house']}/{entry['station']}" not in args.stations:
            continue
        sim, task, config = p2.load_station(entry)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                model = sim.robot.planning_model(sim, config, sim.robot.arms[0], tmp, (task['anchor_world'], config.radius_m))
                production = collision_world(sim, Path(tmp), 2 * config.radius_m + 1., reference_base=model.base)
            kinematics = model.cfg['kinematics']
            largest = max(sphere['radius'] for spheres in kinematics['collision_spheres'].values()
                          for sphere in spheres) + kinematics['collision_sphere_buffer']
            base, inverse = model.base, np.linalg.inv(model.base)
            geoms = list(exported_geoms(sim, base, 2 * config.radius_m + 1.))
            to_planning = lambda v: (np.c_[v, np.ones(len(v))] @ inverse.T)[:, :3]
            offsets = np.cumsum([0] + [len(v) for _, v, _ in geoms[:-1]])
            vertices = np.concatenate([v for _, v, _ in geoms])
            faces = np.concatenate([f + o for (_, _, f), o in zip(geoms, offsets)])
            rng = np.random.default_rng(0)
            radius, angle = REACH_M * np.sqrt(rng.random(args.points)), 2 * np.pi * rng.random(args.points)
            planning = np.c_[radius * np.cos(angle), radius * np.sin(angle), HEIGHT_M * rng.random(args.points)]
            world = (np.c_[planning, np.ones(len(planning))] @ base.T)[:, :3]
            merged_p, t_merged = query(sdf, checker(sdf, [(to_planning(vertices), faces)], PADDING_M), planning)
            merged_w, _ = query(sdf, checker(sdf, [(vertices, faces)], PADDING_M), world)
            # Query time of a one-mesh-per-geom checker (its value sums penetrations, so it is
            # timed only); the reference sign is the minimum over separate per-geom checkers.
            _, t_per_geom = query(sdf, checker(sdf, [(to_planning(v), f) for _, v, f in geoms], PADDING_M), planning)
            per_geom = np.full(len(planning), np.inf)
            for g, v, f in geoms:
                values, _ = query(sdf, checker(sdf, [(to_planning(v), f)], PADDING_M), planning)
                per_geom = np.fmin(per_geom, values)
            started = time.perf_counter()
            probe = sdf.penetration(sdf.checker(production, minimum_mesh_query_dimension(largest)),
                                    np.c_[planning, np.full(len(planning), PROBE_M)])
            t_production = time.perf_counter() - started
            near = np.isfinite(per_geom) & (np.abs(per_geom) < NEAR_M)
            production_false = near & (probe > 0) & (per_geom > PROBE_M)
            production_missed = near & (probe <= 0) & (per_geom < 0)
            false_inside = near & (merged_p < 0) & (per_geom > 0)
            false_outside = near & (merged_p > 0) & (per_geom < 0)
            rows.append({key: entry[key] for key in ('house', 'station', 'op', 'group')} | {
                'exported_geoms': len(geoms), 'points': args.points, 'near_points': int(near.sum()),
                'frame_sign_disagreement': int((near & (np.sign(merged_p) != np.sign(merged_w))).sum()),
                'false_inside': int(false_inside.sum()), 'false_outside': int(false_outside.sum()),
                'false_inside_depth_max_m': round(float(-merged_p[false_inside].min()), 4) if false_inside.any() else 0.,
                'false_inside_examples_planning': planning[false_inside][:5].round(3).tolist(),
                'production_meshes': len(production.mesh or ()),
                'production_false_collision': int(production_false.sum()),
                'production_missed_collision': int(production_missed.sum()),
                'production_false_examples_planning': planning[production_false][:5].round(3).tolist(),
                'query_s': {'merged': round(t_merged, 3), 'per_geom': round(t_per_geom, 3),
                            'production': round(t_production, 3)}})
            r = rows[-1]
            print(f"{entry['house']:7s} {entry['station']} geoms={len(geoms):4d} near={r['near_points']:5d} "
                  f"frame_disagree={r['frame_sign_disagreement']:4d} false_inside={r['false_inside']:4d} "
                  f"false_outside={r['false_outside']:4d} | production false={r['production_false_collision']:4d} "
                  f"missed={r['production_missed_collision']:4d} | query merged/production {t_merged:.3f}/{t_production:.3f}s", flush=True)
        finally:
            sim.close()
    args.output.write_text(json.dumps(rows, indent=1))


if __name__ == '__main__':
    main()
