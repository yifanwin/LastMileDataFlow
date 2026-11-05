#!/usr/bin/env python3
"""N6 robot-part spheres: provable covers of the PandaOmron columns and arm base.

Shapes and poses are read from the robot of one frozen station and expressed
in the planning link frames (omron_base = planning frame + URDF `omron_root`
x=0.21; panda_link0 = omron_base + z 0.70 + torso lock):

  fixed column  `mobilebase0_g0_col`      cylinder on omron_base
  lift column   `mobilebase0_g1_col`      cylinder on panda_link0
  arm base      `robot0_link0_collision`  mesh, collided by MuJoCo as its convex hull

Only exposed solid needs its own spheres. The fixed column below the chassis
top lies inside the chassis box (covered by the chassis spheres). In the
panda_link0 frame the fixed column spans z <= -h (h = torso height); over the
whole torso travel the lift column below z = -h_max therefore stays inside the
wider fixed column, so only z >= -h_max of the lift column is covered here.
Both claims are checked by sampling at the travel ends and middle.

Columns are cut into an annular-sector grid (radial bands x angular sectors x
layers); a candidate is a contiguous range in each (angles cyclic) and its
sphere is centered on the bisector at c >= 0. For such a center the farthest
point of an annular-sector prism is one of its corners (distance is convex
along radial edges and monotone along arcs), so the corner radius encloses the
piece and pieces whose union is the exposed cylinder give a covering set. A
piece is admissible when its sphere extends at most `beta_side` past the
cylinder side (c + r - R) and `beta_end` past the exposed end (top only; the
bottom end lies inside other covered solids).

The arm base hull is cut into a box grid; cells that may touch the hull
(not separated by a hull face plane) must be covered and a candidate is any
cell-aligned sub-block with its circumscribed sphere, as for the chassis. Its
admissible overhang is measured at the hull face normals. Official cuRobo
`fit_spheres_to_mesh` fits of the same hull are reported for comparison.

Selection is the set-cover ILP of n4 (optimal over these candidates, not a
global minimum). Final spheres are rounded as written to YAML; reported
overhang includes the 2 mm buffer, and coverage is re-checked by sampling.
"""
import argparse
import json
from pathlib import Path
import tempfile

import mujoco
import numpy as np
import yaml

from n4_chassis_spheres import BUFFER_M, select_cover, stored
from p2_collision_diagnosis import load_station
from lastmile_dataflow.planning.robots.panda_omron import ASSETS

OMRON_ROOT_X = .21     # URDF omron_root
LINK0_Z = .70          # URDF torso_height origin


def link_frames(sim, model):
    """World transforms of the planning links omron_base and panda_link0 (base joints at 0)."""
    omron = model.base @ np.block([[np.eye(3), np.c_[[OMRON_ROOT_X, 0, 0]]], [np.zeros((1, 3)), 1]])
    link0 = omron.copy()
    link0[:3, 3] += omron[:3, 2] * (LINK0_Z + sim.robot.lift_target)
    return {'omron_base': omron, 'panda_link0': link0}


def geom_in(sim, g, frame):
    m, d = sim.model, sim.data
    inv = np.linalg.inv(frame)
    return inv[:3, :3] @ d.geom_xpos[g] + inv[:3, 3], inv[:3, :3] @ d.geom_xmat[g].reshape(3, 3)


def cylinder(sim, name, frame):
    m = sim.model
    g = m.geom(sim.robot.config.namespace + name).id
    if m.geom_type[g] != mujoco.mjtGeom.mjGEOM_CYLINDER:
        raise ValueError(f'{name} is not a cylinder')
    pos, rot = geom_in(sim, g, frame)
    if np.abs(rot[:, 2] - [0, 0, 1]).max() > 1e-6:
        raise ValueError(f'{name} axis is not the link z axis')
    radius, half = (float(v) for v in m.geom_size[g][:2])
    return {'geom': name, 'axis_xy': pos[:2], 'radius': radius, 'z': (pos[2] - half, pos[2] + half)}


def sector_spheres(rho0, rho1, width, z0, z1):
    """Smallest bisector-centered sphere of each annular-sector prism (vectorized)."""
    k = np.cos(width / 2)
    def corner2(c):
        return np.maximum(rho0**2 + c**2 - 2 * rho0 * c * k, rho1**2 + c**2 - 2 * rho1 * c * k)
    with np.errstate(divide='ignore', invalid='ignore'):
        meet = np.where(k > 0, (rho0 + rho1) / (2 * k), 0.)
    options = np.stack([np.zeros_like(rho1), rho0 * k, rho1 * k, meet])
    options = np.clip(np.nan_to_num(options), 0, rho1)
    value = corner2(options)
    best = value.argmin(0)
    c = np.take_along_axis(options, best[None], 0)[0]
    r = np.sqrt(np.take_along_axis(value, best[None], 0)[0] + ((z1 - z0) / 2)**2)
    return c, r


def column_cover(col, z_low, grid, beta_side, beta_end, time_limit):
    n_r, n_a, n_z = grid
    R, z_top = col['radius'], col['z'][1]
    rho = np.linspace(0, R, n_r + 1)
    zs = np.linspace(z_low, z_top, n_z + 1)
    radial = [(i, j) for i in range(n_r) for j in range(i + 1, n_r + 1)]
    angular = [(s, L) for L in range(1, n_a) for s in range(n_a)] + [(0, n_a)]
    layers = [(i, j) for i in range(n_z) for j in range(i + 1, n_z + 1)]
    ri, ai, li = (g.ravel() for g in np.meshgrid(np.arange(len(radial)), np.arange(len(angular)),
                                                 np.arange(len(layers)), indexing='ij'))
    rr, aa, ll = np.array(radial)[ri], np.array(angular)[ai], np.array(layers)[li]

    def admissible(rr, aa, ll):
        c, r = sector_spheres(rho[rr[:, 0]], rho[rr[:, 1]], aa[:, 1] * 2 * np.pi / n_a, zs[ll[:, 0]], zs[ll[:, 1]])
        zc = (zs[ll[:, 0]] + zs[ll[:, 1]]) / 2
        return (c + r - R <= beta_side) & (zc + r - z_top <= beta_end), c, r, zc

    ok = admissible(rr, aa, ll)[0]
    rr, aa, ll = rr[ok], aa[ok], ll[ok]
    # drop pieces that still admit a one-cell extension (they are dominated)
    maximal = np.ones(len(rr), bool)
    for grow in range(6):
        r2, a2, l2 = rr.copy(), aa.copy(), ll.copy()
        if grow == 0: valid = r2[:, 0] > 0; r2[:, 0] -= 1
        elif grow == 1: valid = r2[:, 1] < n_r; r2[:, 1] += 1
        elif grow == 2: valid = l2[:, 0] > 0; l2[:, 0] -= 1
        elif grow == 3: valid = l2[:, 1] < n_z; l2[:, 1] += 1
        elif grow == 4: valid = a2[:, 1] < n_a - 1; a2[:, 1] += 1
        else: valid = a2[:, 1] < n_a - 1; a2[:, 0] = (a2[:, 0] - 1) % n_a; a2[:, 1] += 1
        r2, a2, l2 = np.clip(r2, 0, n_r), a2, np.clip(l2, 0, n_z)
        maximal &= ~(valid & admissible(r2, a2, l2)[0])
    rr, aa, ll = rr[maximal], aa[maximal], ll[maximal]
    _, c, r, zc = admissible(rr, aa, ll)
    cells = np.arange(n_r * n_a * n_z).reshape(n_r, n_a, n_z)
    members = [cells[rr[j, 0]:rr[j, 1]][:, (aa[j, 0] + np.arange(aa[j, 1])) % n_a][:, :, ll[j, 0]:ll[j, 1]].ravel()
               for j in range(len(r))]
    print(f"  {len(members)} candidates", flush=True)
    chosen, solver = select_cover(members, r, cells.size, time_limit)
    theta = (aa[chosen, 0] + aa[chosen, 1] / 2) * 2 * np.pi / n_a
    centers = np.c_[col['axis_xy'][0] + c[chosen] * np.cos(theta), col['axis_xy'][1] + c[chosen] * np.sin(theta), zc[chosen]]
    return centers, r[chosen], solver


def column_metrics(col, z_low, centers, radii, buffer=BUFFER_M):
    """Overhang of loaded spheres (radius + buffer) and sampled coverage of the exposed part."""
    R, (_, z_top) = col['radius'], col['z']
    loaded = radii + buffer
    rel = centers[:, :2] - col['axis_xy']
    side = float((np.linalg.norm(rel, axis=1) + loaded - R).max())
    end = float((centers[:, 2] + loaded - z_top).max())
    # largest distance of any sphere point from the solid cylinder, in the plane through the axis
    alpha = np.linspace(0, 2 * np.pi, 721)
    rho = np.abs(np.linalg.norm(rel, axis=1)[:, None] + loaded[:, None] * np.cos(alpha))
    z = centers[:, 2:3] + loaded[:, None] * np.sin(alpha)
    dz = np.maximum(np.maximum(z - z_top, col['z'][0] - z), 0)
    far = float(np.sqrt(np.maximum(rho - R, 0)**2 + dz**2).max())
    return {'spheres': len(radii), 'side_overhang_m': round(side, 4), 'end_overhang_m': round(end, 4),
            'max_distance_from_solid_m': round(far, 4), 'max_radius_m': round(float(radii.max()), 4),
            'exposed_coverage_gap_m': coverage_gap(cylinder_points(col, z_low), centers, radii)}


def cylinder_points(col, z_low, step=.004):
    R = col['radius']
    ax = np.arange(-R, R + 1e-9, step)
    xy = np.stack(np.meshgrid(ax, ax, indexing='ij'), -1).reshape(-1, 2)
    xy = xy[np.linalg.norm(xy, axis=1) <= R]
    a = np.linspace(0, 2 * np.pi, 360, endpoint=False)
    xy = np.r_[xy, R * np.c_[np.cos(a), np.sin(a)]] + col['axis_xy']
    z = np.unique(np.r_[np.arange(z_low, col['z'][1], step), col['z'][1]])
    return np.c_[np.repeat(xy, len(z), 0), np.tile(z, len(xy))]


def coverage_gap(points, centers, radii):
    """Largest distance from a sample point to the nearest sphere surface; <= 0 means covered."""
    gap = np.concatenate([(np.linalg.norm(p[:, None] - centers[None], axis=-1) - radii[None]).min(1)
                          for p in np.array_split(points, max(1, len(points) // 20000))])
    return round(float(gap.max()), 5)


def hull_cover(hull, grid, beta, time_limit, max_span=10):
    lo, hi = hull.bounds
    edges = [np.linspace(lo[k], hi[k], n + 1) for k, n in enumerate(grid)]
    planes = np.c_[hull.face_normals, (hull.face_normals * hull.triangles[:, 0]).sum(1)]
    # a cell must be covered unless one hull face plane has all its corners strictly outside
    idx = np.stack(np.meshgrid(*[np.arange(n) for n in grid], indexing='ij'), -1).reshape(-1, 3)
    corner = np.stack([np.stack([edges[k][idx[:, k] + o[k]] for k in range(3)], 1)
                       for o in np.ndindex(2, 2, 2)], 1)
    outside = ((corner @ planes[:, :3].T) > planes[:, 3] + 1e-9).all(1).any(1)
    needed = np.zeros(grid, bool); needed[tuple(idx[~outside].T)] = True
    cell_id = -np.ones(grid, int); cell_id[needed] = np.arange(needed.sum())
    pairs = [np.triu_indices(n + 1, 1) for n in grid]
    pairs = [(i[j - i <= max_span], j[j - i <= max_span]) for i, j in pairs]
    pick = [g.ravel() for g in np.meshgrid(*[np.arange(len(p[0])) for p in pairs], indexing='ij')]
    a = np.stack([pairs[k][0][pick[k]] for k in range(3)], 1)
    b = np.stack([pairs[k][1][pick[k]] for k in range(3)], 1)

    def admissible(a, b):
        blo = np.stack([edges[k][a[:, k]] for k in range(3)], 1)
        bhi = np.stack([edges[k][b[:, k]] for k in range(3)], 1)
        c, r = (blo + bhi) / 2, np.linalg.norm(bhi - blo, axis=1) / 2
        ok = np.concatenate([(cc @ planes[:, :3].T - planes[:, 3] + rc[:, None]).max(1) <= beta
                             for cc, rc in zip(np.array_split(c, 64), np.array_split(r, 64))])
        return ok, c, r

    ok = admissible(a, b)[0]
    a, b = a[ok], b[ok]
    maximal = np.ones(len(a), bool)                 # drop blocks that still admit a one-cell extension
    for k in range(3):
        grown_a, grown_b = a.copy(), b.copy()
        grown_a[:, k] = np.maximum(a[:, k] - 1, 0)
        maximal &= ~((a[:, k] > 0) & (b[:, k] - grown_a[:, k] <= max_span) & admissible(grown_a, b)[0])
        grown_b[:, k] = np.minimum(b[:, k] + 1, grid[k])
        maximal &= ~((b[:, k] < grid[k]) & (grown_b[:, k] - a[:, k] <= max_span) & admissible(a, grown_b)[0])
    a, b = a[maximal], b[maximal]
    _, c, r = admissible(a, b)
    members = [m[m >= 0] for m in (cell_id[a[j, 0]:b[j, 0], a[j, 1]:b[j, 1], a[j, 2]:b[j, 2]].ravel()
                                   for j in range(len(a)))]
    keep = np.array([len(m) > 0 for m in members])
    members = [m for m, k in zip(members, keep) if k]
    print(f"  {len(members)} candidates, {int(needed.sum())} cells", flush=True)
    chosen, solver = select_cover(members, r[keep], int(needed.sum()), time_limit)
    solver['needed_cells'] = int(needed.sum())
    return c[keep][chosen], r[keep][chosen], solver


def hull_metrics(hull, centers, radii, buffer=BUFFER_M):
    """Face-normal overhang, sampled largest distance of sphere points from the hull, and coverage."""
    import trimesh
    loaded = radii + buffer
    normals = np.c_[hull.face_normals, (hull.face_normals * hull.triangles[:, 0]).sum(1)]
    face = float((centers @ normals[:, :3].T - normals[:, 3] + loaded[:, None]).max())
    u = trimesh.creation.icosphere(subdivisions=3).vertices
    surface = (centers[:, None] + loaded[:, None, None] * u[None]).reshape(-1, 3)
    far = float(np.maximum(-trimesh.proximity.signed_distance(hull, surface), 0).max())
    lo, hi = hull.bounds
    grid = np.stack(np.meshgrid(*[np.arange(lo[k], hi[k] + 1e-9, .003) for k in range(3)], indexing='ij'), -1).reshape(-1, 3)
    points = np.r_[grid[hull.contains(grid)], hull.vertices, hull.sample(20000)]
    return {'spheres': len(radii), 'face_overhang_m': round(face, 4), 'max_distance_from_solid_m': round(far, 4),
            'max_radius_m': round(float(radii.max()), 4), 'coverage_gap_m': coverage_gap(points, centers, radii)}


def yaml_lines(centers, radii, indent='  '):
    return [f'{indent}- {{center: [{x:.4f}, {y:.4f}, {z:.4f}], radius: {s:.4f}}}' for (x, y, z), s in zip(centers, radii)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True, help='run directory holding the frozen station')
    parser.add_argument('--scene', required=True)
    parser.add_argument('--station', required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory for report and YAML blocks')
    parser.add_argument('--column-grid', default='6x32x16', help='radial bands x sectors x layers')
    parser.add_argument('--base-grid', default='26x22x16')
    parser.add_argument('--beta-side', type=float, default=.01)
    parser.add_argument('--beta-end', type=float, default=.015)
    parser.add_argument('--beta-base', type=float, default=.02)
    parser.add_argument('--time-limit', type=float, default=600.)
    parser.add_argument('--skip-curobo-fit', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    import trimesh
    sim, task, config = load_station({'run': str(args.run.resolve()), 'scene': args.scene, 'station': args.station})
    with tempfile.TemporaryDirectory() as tmp:
        model = sim.robot.planning_model(sim, config, sim.robot.arms[0], tmp, (task['anchor_world'], config.radius_m))
    frames = link_frames(sim, model)
    m, ns = sim.model, sim.robot.config.namespace
    torso = m.joint(ns + 'mobilebase0_joint_torso_height').id
    travel = tuple(float(v) for v in m.jnt_range[torso])
    current = yaml.safe_load((ASSETS / 'spheres.yml').read_text())['collision_spheres']
    chassis = np.array([s['center'] for s in current['omron_base'][:51]]), np.array([s['radius'] for s in current['omron_base'][:51]])
    chassis_g = m.geom(ns + 'mobilebase0_pedestal_feet_col').id
    chassis_pos, _ = geom_in(sim, chassis_g, frames['omron_base'])
    chassis_top = float(chassis_pos[2] + m.geom_size[chassis_g][2])

    fixed = cylinder(sim, 'mobilebase0_g0_col', frames['omron_base'])
    lift = cylinder(sim, 'mobilebase0_g1_col', frames['panda_link0'])
    grid = tuple(int(v) for v in args.column_grid.split('x'))
    exposed = {'fixed_column': (fixed, chassis_top), 'lift_column': (lift, -travel[1])}
    report = {'station': {'run': str(args.run), 'scene': args.scene, 'station': args.station},
              'torso_travel_m': travel, 'torso_lock_m': float(sim.robot.lift_target),
              'beta': {'side': args.beta_side, 'end': args.beta_end, 'base': args.beta_base},
              'column_grid': grid, 'parts': {}}
    old = {'fixed_column': current['omron_base'][51:], 'lift_column': current['panda_link0'][1:]}
    written = {}
    for part, (col, z_low) in exposed.items():
        print('covering', part, flush=True)
        c, r, solver = column_cover(col, z_low, grid, args.beta_side, args.beta_end, args.time_limit)
        centers, radii = stored(c, r)
        oc, orr = np.array([s['center'] for s in old[part]]), np.array([s['radius'] for s in old[part]])
        report['parts'][part] = {
            'geometry': {**col, 'axis_xy': np.round(col['axis_xy'], 5).tolist(), 'z': np.round(col['z'], 5).tolist(),
                         'exposed_z': [round(z_low, 5), round(col['z'][1], 5)]},
            'solver': solver, 'new': column_metrics(col, z_low, centers, radii),
            'old': column_metrics(col, z_low, oc, orr)}
        written[part] = centers, radii

    # hidden parts: fixed column under the chassis top, lift column under the fixed column over the travel
    fc, fr = written['fixed_column']
    omron_c, omron_r = np.r_[chassis[0], fc], np.r_[chassis[1], fr]
    report['parts']['fixed_column']['whole_coverage_gap_m'] = coverage_gap(cylinder_points(fixed, fixed['z'][0]), omron_c, omron_r)
    lc, lr = written['lift_column']
    lift_whole = cylinder_points(lift, lift['z'][0])
    report['parts']['lift_column']['whole_coverage_gap_m_by_torso'] = {
        f'{h:.3f}': coverage_gap(lift_whole, np.r_[lc, omron_c - [0, 0, LINK0_Z + h]], np.r_[lr, omron_r])
        for h in (travel[0], sum(travel) / 2, travel[1])}

    g = m.geom(ns + 'robot0_link0_collision').id
    mesh = int(m.geom_dataid[g]); va, vn = int(m.mesh_vertadr[mesh]), int(m.mesh_vertnum[mesh])
    pos, rot = geom_in(sim, g, frames['panda_link0'])
    hull = trimesh.convex.convex_hull(m.mesh_vert[va:va + vn] @ rot.T + pos)
    print('covering link0', flush=True)
    bc, br, solver = hull_cover(hull, tuple(int(v) for v in args.base_grid.split('x')), args.beta_base, args.time_limit)
    bc, br = stored(bc, br)
    o0 = current['panda_link0'][0]
    report['parts']['link0'] = {'geometry': {'geom': 'robot0_link0_collision', 'hull_bounds': hull.bounds.round(4).tolist()},
                                'solver': solver, 'new': hull_metrics(hull, bc, br),
                                'old_link0_sphere': hull_metrics(hull, np.array([o0['center']]), np.array([o0['radius']])),
                                'old_link0_with_old_lift': hull_metrics(
                                    hull, np.array([s['center'] for s in current['panda_link0']]),
                                    np.array([s['radius'] for s in current['panda_link0']]))}
    if not args.skip_curobo_fit:
        from curobo._src.geom.sphere_fit.fit_spheres import fit_spheres_to_mesh
        from curobo._src.geom.sphere_fit.types import SphereFitType
        for kind in (SphereFitType.MORPHIT, SphereFitType.VOXEL):
            for n in (4, 8, 16):
                fit = fit_spheres_to_mesh(hull, num_spheres=n, fit_type=kind, compute_metrics=True)
                fc_, fr_ = (np.asarray(v.cpu() if hasattr(v, 'cpu') else v, float) for v in (fit.centers, fit.radii))
                report['parts']['link0'][f'curobo_{kind.name.lower()}_{n}'] = {
                    **hull_metrics(hull, fc_, fr_), 'curobo_coverage': round(float(fit.metrics.coverage), 4)}
    written['link0'] = bc, br
    blocks = {'omron_base_fixed_column.yml': yaml_lines(*written['fixed_column']),
              'panda_link0.yml': yaml_lines(*written['link0']) + yaml_lines(*written['lift_column'])}
    for name, lines in blocks.items():
        (args.output / name).write_text('\n'.join(lines) + '\n')
    report['yaml_blocks'] = {name: len(lines) for name, lines in blocks.items()}
    (args.output / 'robot_part_spheres.json').write_text(json.dumps(report, indent=1, default=float))
    for part, row in report['parts'].items():
        print(part, json.dumps({k: v for k, v in row.items() if k != 'geometry'}, default=float))
    sim.close()


if __name__ == '__main__':
    main()
