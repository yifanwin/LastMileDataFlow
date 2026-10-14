"""Seeded, size-aware obstacle pool and proposal sampling without agents."""
from types import SimpleNamespace
import mujoco
import numpy as np

from .obstacle_regions import rotated_placement, ground_coverage
from ..scenes.geometry import body_points, support_rays


ENVIRONMENT_POOLS = {
    'DiningTable': {'ground': ['Chair'], 'surface': ['Bowl', 'Plate', 'Bottle', 'Cup', 'Mug', 'Book', 'Kettle', 'Toaster', 'CoffeeMachine', 'DeskLamp', 'Vase', 'SaltShaker']},
    'CounterTop': {'ground': [], 'surface': ['Bowl', 'Plate', 'Bottle', 'Cup', 'Mug', 'Kettle', 'Toaster', 'CoffeeMachine', 'Vase', 'SaltShaker']},
    'Desk': {'ground': ['Chair', 'OfficeChair'], 'surface': ['Book', 'Laptop', 'CellPhone', 'DeskLamp', 'Vase', 'Cup', 'Mug']},
    'CoffeeTable': {'ground': ['Chair', 'Stool'], 'surface': ['Book', 'Bowl', 'Bottle']},
    'SideTable': {'ground': ['Chair', 'Stool'], 'surface': ['Book', 'Bottle']},
}


def environment_pool(sim, task, config):
    support_names = {o['body'] for o in task['support_obstacles']}
    categories = sorted({e.get('category') for e in sim.catalog
                         if e['mjcf_body'] in support_names and e.get('category')})
    ground, surface = set(), set()
    for category in categories:
        rule = ENVIRONMENT_POOLS.get(category, {'ground': [], 'surface': []})
        ground.update(rule['ground'])
        surface.update(rule['surface'])
    ground &= set(config.ground_categories)
    surface &= set(config.surface_categories)
    if config.case_policy and config.case_policy.rule_version in ('scene-cases-v2', 'scene-cases-v3'):
        # Surface geometry and physical checks, not furniture names, decide support.
        ground = set(config.ground_categories)
        if not surface:
            surface = set(config.surface_categories)
        if config.case_policy.rule_version == 'scene-cases-v3':
            surface = set(config.surface_categories)
    return {'support_categories': categories, 'ground': sorted(ground), 'surface': sorted(surface),
            'rules': 'explicit_environment_category_mapping_and_measured_geometry_not_visual_agent'}


def asset_geometry(catalog):
    rows = {}
    for asset_id in sorted(catalog.assets):
        spec, record = catalog.load(asset_id)
        model = spec.compile()
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        body = model.body(record['root_body']).id
        local = (body_points(SimpleNamespace(model=model, data=data), body) - data.xpos[body]) @ data.xmat[body].reshape(3, 3)
        rows[asset_id] = {**record, 'local_points': local, 'model': model, 'data': data, 'body': body}
    return rows


def sample_placement(sim, geometry, point, yaw, config, *, region=None):
    if region is None:
        # Ground plane is measured by a ray; never assume a floor at z=0.
        xy = np.asarray(point['xy'])
        distances = []
        for g in range(sim.model.ngeom):
            if 'floor' not in sim.model.geom(g).name.lower() or not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
                continue
            start = np.r_[xy, .2]
            direction = np.array([0., 0., -1.])
            if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                distance = mujoco.mj_rayMesh(sim.model, sim.data, g, start, direction)
            else:
                distance = mujoco.mju_rayGeom(sim.data.geom_xpos[g], sim.data.geom_xmat[g], sim.model.geom_size[g], start, direction, sim.model.geom_type[g])
            if 0 <= distance <= .3:
                distances.append(float(distance))
        if not distances:
            return None, 'no_ground_support'
        height = .2 - min(distances)
        pose, points = rotated_placement(geometry['local_points'], xy, yaw, height, config.placement_gap_m)
        if not ground_coverage(sim, points):
            return None, 'full_footprint_outside_ground'
        coverage = False
        kind = 'ground'
    else:
        xy = np.asarray(point['xy'])
        pose, points = rotated_placement(geometry['local_points'], xy, yaw, region.height, config.placement_gap_m)
        local = region.local(points)
        a, b, c, d = region.bounds
        if not (local[:, 0].min() >= a + .01 and local[:, 0].max() <= b - .01
                and local[:, 1].min() >= c + .01 and local[:, 1].max() <= d - .01):
            return None, 'full_footprint_outside_surface'
        if not support_rays(sim, 0, region, points):
            return None, 'surface_support_rays_failed'
        height, coverage, kind = region.height, True, 'surface'
    return {'asset_id': geometry['asset_id'], 'category': geometry['category'],
            'point_id': point['point_id'], 'point_xy': xy.tolist(), 'point_sources': point['sources'],
            'pose': pose, 'yaw_rad': float(yaw), 'kind': kind, 'support_height_m': float(height),
            'support_geom': region.geom if region is not None else None,
            'support_instance': region.support if region is not None else None,
            'surface_coverage_verified': coverage, 'measured_size_m': geometry['size'],
            'world_bounds': {'min': points.min(axis=0).tolist(), 'max': points.max(axis=0).tolist()}}, None


def substantial_asset(asset, policy):
    size = asset['size']
    return (asset['category'] in policy.substantial_categories and
            (size['height'] >= policy.substantial_height_m or max(size['width'], size['depth']) >= policy.substantial_width_m))


def case_assets(rng, count, pool, geometry, policy, mandatory, *, surface_area_m2=None):
    """Choose the complete asset tuple once: pose retries reuse compiled models."""
    available = [g for g in geometry.values() if g['category'] in pool['surface']]
    if policy and policy.case == 'Case2':
        substantial = [g for g in available if substantial_asset(g, policy)]
        if not substantial:
            raise ValueError('no_measured_substantial_surface_asset')
        groups = sorted({g['category'] for g in substantial})
        order = list(rng.permutation(groups))
        selected = []
        for i in range(policy.min_substantial):
            choices = [g for g in substantial if g['category'] == order[i % len(order)]]
            selected.append(choices[int(rng.integers(len(choices)))])
        # Larger pieces first, avoiding a small-items-first packing dead end.
        substantial_ids = {g['asset_id'] for g in substantial}
        ordinary = [g for g in available if g['asset_id'] not in substantial_ids]
    else:
        selected = []
        for category in mandatory:
            choices = [g for g in geometry.values() if g['category'] == category]
            if not choices:
                raise ValueError('no_loadable_asset_for_requested_category:' + category)
            selected.append(choices[int(rng.integers(len(choices)))])
        ordinary = available if policy else [g for g in geometry.values() if g['category'] in pool['ground'] + pool['surface']]
    if len(selected) < count and not ordinary:
        raise ValueError('no_loadable_ordinary_asset')
    if policy and policy.rule_version in ('scene-cases-v2', 'scene-cases-v3') and surface_area_m2 is not None and len(selected) < count:
        reserved = sum(g['size']['width'] * g['size']['depth'] for g in selected if g['category'] in pool['surface'])
        allowance = max(0., .70 * surface_area_m2 - reserved) / (count - len(selected))
        compact = [g for g in ordinary if g['size']['width'] * g['size']['depth'] <= allowance]
        if compact:
            ordinary = compact
        else:
            raise ValueError('no_assets_within_measured_surface_packing_budget')
    while len(selected) < count:
        selected.append(ordinary[int(rng.integers(len(ordinary)))])
    if policy and (policy.case == 'Case2' or policy.rule_version in ('scene-cases-v2', 'scene-cases-v3')):
        ground_selected = [g for g in selected if g['category'] in pool['ground']]
        surface_selected = [g for g in selected if g['category'] not in pool['ground']]
        surface_selected.sort(key=lambda g: g['size']['width'] * g['size']['depth'], reverse=True)
        selected = ground_selected + surface_selected
    return selected


def surface_points(regions, spacing):
    points = []
    for region in regions:
        a, b, c, d = region.bounds
        for x in np.arange(a + .02, b - .02, spacing):
            for y in np.arange(c + .02, d - .02, spacing):
                world = np.asarray(region.origin) + np.asarray(region.axes) @ [x, y, 0.]
                points.append({'point_id': f'T{len(points):04d}', 'xy': world[:2].tolist(),
                               'sources': [{'kind': 'support_surface', 'region_id': region.region_id}],
                               'region': region})
    return points


def surface_occupied_bounds(sim, task):
    """Measured source footprints are sampling preferences, not collision proof."""
    ignored = {o['body'] for o in task['support_obstacles']}
    boxes = []
    for entry in sim.catalog:
        name = entry['mjcf_body']
        if name in ignored or name.startswith(sim.robot.config.namespace):
            continue
        try:
            points = body_points(sim, sim.model.body(name).id)
        except ValueError:
            continue
        boxes.append([points.min(axis=0), points.max(axis=0)])
    return np.asarray(boxes).reshape(-1, 2, 3)


def sample_surface_point(rng, asset, points, placements, occupied, policy, gap):
    """Footprint-conditioned free-region proposal with source-overlap preference.

    It does NOT exempt contact pairs: the actual compiled MuJoCo narrowphase and
    physical settling still decide legality. Nonzero exploration handles AABBs
    that conservatively cover empty mesh interiors.
    """
    if not points:
        return None, None
    region = points[int(rng.integers(len(points)))]['region']
    choices = [p for p in points if p['region'].region_id == region.region_id]
    axes = np.asarray(region.axes)
    alignment = float(np.arctan2(axes[1, 0], axes[0, 0]))
    jitter = float(rng.choice([-policy.surface_alignment_jitter_rad, 0., policy.surface_alignment_jitter_rad])) if policy.rule_version == 'scene-cases-v3' else float(rng.uniform(-policy.surface_alignment_jitter_rad, policy.surface_alignment_jitter_rad))
    yaw = alignment + int(rng.integers(4)) * np.pi / 2 + jitter
    c, s = np.cos(yaw), np.sin(yaw)
    rotated = asset['local_points'] @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T
    center = (rotated[:, :2].min(0) + rotated[:, :2].max(0)) / 2
    relative = rotated - np.r_[center, rotated[:, 2].min()]
    offsets = relative @ axes
    xy = np.array([p['xy'] for p in choices])
    local = region.local(np.column_stack((xy, np.full(len(xy), region.height))))
    lo, hi = local[:, :2] + offsets[:, :2].min(0), local[:, :2] + offsets[:, :2].max(0)
    a, b, cc, d = region.bounds
    fit = (lo[:, 0] >= a + .01) & (hi[:, 0] <= b - .01) & (lo[:, 1] >= cc + .01) & (hi[:, 1] <= d - .01)
    world_lo, world_hi = xy + relative[:, :2].min(0), xy + relative[:, :2].max(0)
    for placed in placements:
        if placed['kind'] != 'surface' or policy.rule_version == 'scene-cases-v3':
            continue
        plo, phi = np.asarray(placed['world_bounds']['min'])[:2], np.asarray(placed['world_bounds']['max'])[:2]
        fit &= ~((world_lo < phi + .005).all(1) & (plo < world_hi + .005).all(1))
    ids = np.flatnonzero(fit)
    if not len(ids):
        return None, yaw
    ob = occupied[(occupied[:, 1, 2] >= region.height + gap) &
                  (occupied[:, 0, 2] <= region.height + gap + relative[:, 2].max())]
    if len(ob):
        overlap = np.maximum(0., np.minimum(world_hi[ids, None, :], ob[None, :, 1, :2]) -
                             np.maximum(world_lo[ids, None, :], ob[None, :, 0, :2]))
        area = np.prod(world_hi[ids] - world_lo[ids], axis=1)
        score = np.prod(overlap, axis=2).sum(1) / np.maximum(area, 1e-9)
        weights = np.exp(-40 * (score - score.min())) + .002
        index = int(rng.choice(ids, p=weights / weights.sum()))
    else:
        index = int(rng.choice(ids))
    return choices[index], float(yaw)
