#!/usr/bin/env python3
"""N4 chassis spheres: minimum block cover of the PandaOmron chassis box.

The physical chassis is one MuJoCo box (`mobilebase0_pedestal_feet_col`,
half sizes 0.35/0.25/0.19 m) at x=-0.20, z=0.192 m in the omron_base link
frame (planning frame minus the URDF `omron_root` offset x=0.21). The box is
cut into a regular cell grid; a candidate is any cell-aligned sub-block, and
its sphere is the sub-block's circumscribed sphere, so a set of sub-blocks
whose union is the box gives a sphere set that provably covers the solid.
A candidate is admissible when its sphere extends at most `beta_side` beyond
the side faces and `beta_top` above the top face (exact at the face normals:
radius minus the center's distance to that face). The floor side is not
bounded: the planner exports no floor. A set-cover ILP picks the fewest
admissible sub-blocks; the result is the minimum over these cell-aligned
candidates, not a global minimum sphere count.

For comparison the same box is fitted with cuRobo `fit_spheres_to_mesh`
(MORPHIT, VOXEL). Every candidate gets the same metrics: radius inflation
needed to cover the solid (5 mm grid, certified by adding h*sqrt(3)/2; zero
for block covers; a conservative bound, not the minimum inflation) and the
exact overhang of the loaded spheres (center +/- radius, inflation and buffer)
past the side, top and bottom faces. The floor side is left unbounded on
purpose, so the bottom overhang is reported, not minimized.
"""
import argparse
import json
from pathlib import Path

import numpy as np

OFFSET_X = .21                                             # URDF omron_root
LO = np.array([-.34, -.25, .002]) - [OFFSET_X, 0, 0]       # omron_base frame
HI = np.array([.36, .25, .382]) - [OFFSET_X, 0, 0]
GRID_M = .005
BUFFER_M = .002                                            # collision_sphere_buffer, robots/panda_omron.py


def evaluate(c, r, certified, buffer=BUFFER_M):
    """Coverage gap of the solid box (5 mm grid) and exact overhang of the final spheres.

    Overhang is `center +/- (radius + inflation + buffer)` beyond each face, i.e. what the
    planner loads. For centers inside the box its largest value is also the largest
    distance from the box of any sphere point.
    """
    c, r = np.asarray(c, float), np.asarray(r, float)
    axes = [np.unique(np.r_[np.arange(LO[i], HI[i], GRID_M), HI[i]]) for i in range(3)]
    grid = np.stack(np.meshgrid(*axes, indexing='ij'), -1).reshape(-1, 3)
    gap = np.concatenate([(np.linalg.norm(g[:, None] - c[None], axis=-1) - r[None]).min(1)
                          for g in np.array_split(grid, max(1, len(grid) // 20000))])
    inflation = 0. if certified else max(0., float(gap.max()) + GRID_M * np.sqrt(3) / 2)
    loaded = r + inflation + buffer
    upper, lower = c + loaded[:, None] - HI, LO - (c - loaded[:, None])
    return {'spheres': len(r), 'max_gap_before_inflation_m': round(float(max(gap.max(), 0.)), 4),
            'inflation_to_cover_m': round(inflation, 4), 'buffer_m': buffer,
            'side_overhang_m': round(float(max(upper[:, :2].max(), lower[:, :2].max())), 4),
            'top_overhang_m': round(float(upper[:, 2].max()), 4),
            'bottom_overhang_m': round(float(lower[:, 2].max()), 4),
            'max_radius_m': round(float((r + inflation).max()), 3)}


def admissible(edges, a, b, beta_side, beta_top):
    lo = np.stack([edges[k][a[:, k]] for k in range(3)], 1)
    hi = np.stack([edges[k][b[:, k]] for k in range(3)], 1)
    c, r = (lo + hi) / 2, np.linalg.norm(hi - lo, axis=1) / 2
    side = np.min([c[:, 0] - LO[0], HI[0] - c[:, 0], c[:, 1] - LO[1], HI[1] - c[:, 1]], axis=0)
    return (r - side <= beta_side) & (r - (HI[2] - c[:, 2]) <= beta_top), c, r


def block_cover(shape, beta_side, beta_top, time_limit):
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import csr_matrix
    edges = [np.linspace(LO[k], HI[k], n + 1) for k, n in enumerate(shape)]
    pairs = [np.triu_indices(n + 1, 1) for n in shape]
    index = [g.ravel() for g in np.meshgrid(*[np.arange(len(p[0])) for p in pairs], indexing='ij')]
    a = np.stack([pairs[k][0][index[k]] for k in range(3)], 1)
    b = np.stack([pairs[k][1][index[k]] for k in range(3)], 1)
    ok = admissible(edges, a, b, beta_side, beta_top)[0]
    a, b = a[ok], b[ok]
    maximal = np.ones(len(a), bool)                 # drop blocks that still admit a one-cell extension
    for k in range(3):
        grown_a, grown_b = a.copy(), b.copy()
        grown_a[:, k] = np.maximum(a[:, k] - 1, 0)
        maximal &= ~((a[:, k] > 0) & admissible(edges, grown_a, b, beta_side, beta_top)[0])
        grown_b[:, k] = np.minimum(b[:, k] + 1, shape[k])
        maximal &= ~((b[:, k] < shape[k]) & admissible(edges, a, grown_b, beta_side, beta_top)[0])
    a, b = a[maximal], b[maximal]
    _, c, r = admissible(edges, a, b, beta_side, beta_top)
    cells = np.arange(np.prod(shape)).reshape(shape)
    members = [cells[a[j, 0]:b[j, 0], a[j, 1]:b[j, 1], a[j, 2]:b[j, 2]].ravel() for j in range(len(a))]
    cover = csr_matrix((np.ones(sum(map(len, members))),
                        (np.concatenate(members), np.repeat(np.arange(len(a)), list(map(len, members))))),
                       shape=(cells.size, len(a)))
    result = milp(np.ones(len(a)) + 1e-3 * r, constraints=LinearConstraint(cover, lb=1), integrality=np.ones(len(a)),
                  bounds=Bounds(0, 1), options={'time_limit': time_limit})
    if result.x is None:
        raise SystemExit(f'no feasible cover within the limit: {result.message}')
    chosen = result.x > .5
    if (cover @ chosen.astype(float)).min() < 1:
        raise SystemExit('selected sub-blocks leave a cell uncovered')
    # status 0: optimal over these candidate blocks (not a global minimum sphere count); 1: time limit, feasible only
    return c[chosen], r[chosen], {'candidates': len(a), 'milp_status': int(result.status),
                                  'solution': 'optimal_over_candidates' if result.status == 0 else 'feasible',
                                  'milp_message': result.message, 'selected_blocks': int(chosen.sum())}


def stored(c, r):
    """Spheres as written to YAML: centers to 0.1 mm, radii widened by the rounding shift.

    Distinct sub-blocks can share a circumscribed sphere (same center, same diagonal);
    identical spheres are written once, which leaves their union unchanged.
    """
    centers = np.round(c, 4)
    radii = np.ceil((r + np.linalg.norm(centers - c, axis=1)) * 1e4) / 1e4
    unique = np.unique(np.c_[centers, radii], axis=0)
    order = np.lexsort((unique[:, 2], unique[:, 1], unique[:, 0]))
    return unique[order, :3], unique[order, 3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='new directory for report and YAML block')
    parser.add_argument('--grid', default='28x20x12')
    parser.add_argument('--beta-side', type=float, default=.035)
    parser.add_argument('--beta-top', type=float, default=.06)
    parser.add_argument('--time-limit', type=float, default=1200.)
    parser.add_argument('--skip-curobo-fit', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    shape = tuple(int(v) for v in args.grid.split('x'))
    c, r, solver = block_cover(shape, args.beta_side, args.beta_top, args.time_limit)
    centers, radii = stored(c, r)
    original = np.array([[x, y, z] for x in (-.42, -.18, .04) for y in (-.13, .13) for z in (.1, .28)])
    report = {'box_omron_base_frame': {'lo': LO.round(4).tolist(), 'hi': HI.round(4).tolist()},
              'grid': shape, 'beta_side_m': args.beta_side, 'beta_top_m': args.beta_top, 'solver': solver,
              'candidates': {'original_12': evaluate(original, np.full(12, .21), False),
                             'block_cover': evaluate(centers, radii, True)}}
    if not args.skip_curobo_fit:
        import trimesh
        from curobo._src.geom.sphere_fit.fit_spheres import fit_spheres_to_mesh
        from curobo._src.geom.sphere_fit.types import SphereFitType
        mesh = trimesh.creation.box(extents=HI - LO)
        mesh.apply_translation((HI + LO) / 2)
        for kind in (SphereFitType.MORPHIT, SphereFitType.VOXEL):
            for n in (12, 24, 64, 100):
                fit = fit_spheres_to_mesh(mesh, num_spheres=n, fit_type=kind, compute_metrics=True)
                fc, fr = (np.asarray(v.cpu() if hasattr(v, 'cpu') else v, float) for v in (fit.centers, fit.radii))
                report['candidates'][f'curobo_{kind.name.lower()}_{n}'] = {
                    **evaluate(fc, fr, False), 'curobo_coverage': round(float(fit.metrics.coverage), 4),
                    'curobo_protrusion_p95_m': round(float(fit.metrics.protrusion_dist_p95), 4)}
    lines = [f'  - {{center: [{x:.4f}, {y:.4f}, {z:.4f}], radius: {s:.4f}}}' for (x, y, z), s in zip(centers, radii)]
    (args.output / 'omron_base_chassis.yml').write_text('\n'.join(lines) + '\n')
    (args.output / 'chassis_spheres.json').write_text(json.dumps(report, indent=1))
    for name, row in report['candidates'].items():
        print(f'{name:22s} {json.dumps(row)}')


if __name__ == '__main__':
    main()
