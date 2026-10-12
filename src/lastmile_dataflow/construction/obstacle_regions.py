"""Local station edges, independently checked midpoints and support regions."""
import itertools
import numpy as np

from ..navigation.astar import Grid, search, scene_grid
from ..scenes.geometry import body_points, extract_regions
from ..scenes.initialization import floor_support, inside_triangles, room_triangles


def point_is_free(grid, xy):
    cell = grid.cell(xy)
    return bool(cell is not None and grid.free[cell])


def station_regions(sim, stations, task, baseline_config, footprint_radius, band_m):
    nav = scene_grid(sim, task['anchor_world'], baseline_config.radius_m,
                     baseline_config.map_resolution_m, footprint_radius,
                     baseline_config.navigation_margin_m,
                     support_obstacles=task['support_obstacles'])
    raw = scene_grid(sim, task['anchor_world'], baseline_config.radius_m,
                     baseline_config.map_resolution_m, 0., 0.,
                     support_obstacles=task['support_obstacles'])
    points, edges, rejected = {}, [], []

    def add(xy, source):
        key = tuple(np.round(xy, 9))
        if key not in points:
            points[key] = {'point_id': f'P{len(points):04d}', 'xy': list(map(float, xy)),
                           'kind': 'ground', 'sources': []}
        points[key]['sources'].append(source)

    valid = [s for s in stations if s['geometry'] == 'valid']
    for s in valid:
        if point_is_free(raw, s['xy']):
            add(s['xy'], {'kind': 'station', 'station_id': s['station_id']})
    corridor = np.zeros_like(nav.free)
    yy, xx = np.indices(nav.free.shape)
    centers = nav.origin + np.stack((xx, yy), axis=-1) * nav.resolution
    for a, b in itertools.combinations(valid, 2):
        delta = np.abs(np.asarray(a['grid_index']) - b['grid_index'])
        if not (delta.max() == 1 and np.any(delta)):
            continue
        path = search(nav, a['xy'], b['xy'])
        row = {'stations': [a['station_id'], b['station_id']],
               'connected': path is not None, 'path': path}
        edges.append(row)
        if path is None:
            continue
        for xy in path['xy']:
            corridor |= nav.free & (np.linalg.norm(centers - xy, axis=-1) <= band_m)
        midpoint = (np.asarray(a['xy']) + b['xy']) / 2
        source = {'kind': 'arithmetic_midpoint', 'stations': row['stations']}
        if point_is_free(raw, midpoint) and floor_support(sim, midpoint):
            add(midpoint, source)
        else:
            rejected.append({'xy': midpoint.tolist(), 'source': source, 'reason': 'midpoint_not_free'})
    return {'points': list(points.values()), 'edges': edges, 'rejected_midpoints': rejected,
            'corridor': corridor, 'navigation_grid': nav, 'placement_grid': raw}


def support_regions(sim, task):
    """Select the plane beneath the target, not exposed tops of table legs."""
    candidates = []
    for support in task['support_obstacles']:
        candidates.extend(extract_regions(sim, support['body']))
    anchor = np.asarray(task['anchor_world'])
    beneath = []
    for region in candidates:
        local = region.local(anchor.reshape(1, 3))[0]
        a, b, c, d = region.bounds
        if a <= local[0] <= b and c <= local[1] <= d and region.height <= anchor[2] + .001:
            beneath.append(region)
    if not beneath:
        return []
    actual = max(beneath, key=lambda r: r.height)
    # Coplanar patches of the same actual support are allowed; lower shelves are not.
    return [r for r in candidates if r.support == actual.support and abs(r.height - actual.height) <= .001]


def approach_points(sim, stations, task, regions):
    """Add only measured free samples on station-to-target approach segments."""
    points = list(regions['points'])
    seen = {tuple(np.round(p['xy'], 9)) for p in points}
    anchor = np.asarray(task['anchor_world'][:2])
    for station in stations:
        if station['geometry'] != 'valid':
            continue
        for fraction in (.25, .5, .75):
            xy = (1 - fraction) * np.asarray(station['xy']) + fraction * anchor
            key = tuple(np.round(xy, 9))
            if key in seen or not point_is_free(regions['placement_grid'], xy) or not floor_support(sim, xy):
                continue
            seen.add(key)
            points.append({'point_id': f'P{len(points):04d}', 'xy': xy.tolist(), 'kind': 'ground',
                           'sources': [{'kind': 'station_target_approach', 'station_id': station['station_id'],
                                        'fraction': fraction}]})
    regions['points'] = points
    return regions


def rotated_placement(local_points, xy, yaw, height, gap):
    """Align footprint CENTER, not a possibly off-center asset root, to the point."""
    c, s = np.cos(yaw), np.sin(yaw)
    rotation = np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])
    rotated = np.asarray(local_points) @ rotation.T
    center = (rotated[:, :2].min(axis=0) + rotated[:, :2].max(axis=0)) / 2
    position = np.r_[np.asarray(xy) - center, height + gap - rotated[:, 2].min()]
    return {'position': position.tolist(), 'quaternion_wxyz': [float(np.cos(yaw / 2)), 0., 0., float(np.sin(yaw / 2))]}, rotated + position


def footprint_samples(points):
    lo, hi = np.asarray(points)[:, :2].min(axis=0), np.asarray(points)[:, :2].max(axis=0)
    return np.array(list(itertools.product(np.linspace(lo[0], hi[0], 5),
                                          np.linspace(lo[1], hi[1], 5))))


def ground_coverage(sim, points):
    triangles = room_triangles(sim)
    samples = footprint_samples(points)
    return all(inside_triangles(xy, triangles) and floor_support(sim, xy) for xy in samples)


def augmented_grid(sim, task, config, footprint_radius, ground_names):
    obstacles = list(task['support_obstacles'])
    for name in ground_names:
        points = body_points(sim, sim.model.body(name).id)
        obstacles.append({'body': name, 'kind': 'added_ground_obstacle',
                          'min': points.min(axis=0).tolist(), 'max': points.max(axis=0).tolist(),
                          'evidence': 'settled_full_collision_geometry_projection'})
    return scene_grid(sim, task['anchor_world'], config.radius_m, config.map_resolution_m,
                      footprint_radius, config.navigation_margin_m, support_obstacles=obstacles)


def connectivity(grid, stations):
    valid = [s for s in stations if s['geometry'] == 'valid' and point_is_free(grid, s['xy'])]
    connected = []
    for a, b in itertools.combinations(valid, 2):
        path = search(grid, a['xy'], b['xy'])
        if path:
            connected.append({'stations': [a['station_id'], b['station_id']], 'length_m': path['length_m']})
    return {'station_ids_on_free_grid': [s['station_id'] for s in valid],
            'connected_pairs': connected, 'free_cells': int(grid.free.sum())}
