"""Measured scene snapshots: topology, planar regions and conservative support facts.

Metadata parent links are hints, not support edges. Numeric queries are deliberately
lazy. Unsupported geometry remains unknown rather than being promoted to evidence.
"""
import copy
from dataclasses import dataclass, field, replace

import mujoco
import numpy as np

from ..io import digest
from .geometry import (SupportRegion, body_points, collision_geoms, descendants,
                       extract_regions, geom_points, support_rays)


STATION_ID = 'station_start'


@dataclass(frozen=True)
class GraphConfig:
    support_tolerance_m: float = .008
    boundary_tolerance_m: float = .001
    penetration_tolerance_m: float = .01

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if type(value) not in (int, float) or not np.isfinite(value) or value <= 0:
                raise ValueError(f'invalid graph setting {name}')


@dataclass(frozen=True)
class SceneGraph:
    scene_id: str
    revision: int
    time_s: float
    nodes: dict
    edges: list
    stage: str = 'observed'
    issues: list = field(default_factory=list)

    def __post_init__(self):
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('revision must be a nonnegative integer')
        if self.stage not in ('observed', 'settled'):
            raise ValueError('unknown graph observation stage')
        if not np.isfinite(self.time_s):
            raise ValueError('nonfinite graph time')

    def to_dict(self):
        value = {'graph_version': '0.1', 'scene_id': self.scene_id,
                 'revision': self.revision, 'time_s': self.time_s, 'stage': self.stage,
                 'units': 'SI', 'pose_order': 'xyz_wxyz', 'nodes': copy.deepcopy(self.nodes),
                 'edges': copy.deepcopy(self.edges), 'issues': copy.deepcopy(self.issues)}
        value['graph_id'] = digest(value)
        return value

    @classmethod
    def from_dict(cls, value):
        if value.get('graph_version') != '0.1' or value.get('units') != 'SI' or value.get('pose_order') != 'xyz_wxyz':
            raise ValueError('unsupported graph format')
        if not isinstance(value.get('nodes'), dict) or not isinstance(value.get('edges'), list):
            raise ValueError('invalid graph nodes/edges')
        return cls(**{k: copy.deepcopy(value[k]) for k in
                      ('scene_id', 'revision', 'time_s', 'nodes', 'edges', 'stage', 'issues')})


def _pose(position, quaternion):
    return {'frame': 'world', 'position': np.asarray(position).tolist(),
            'quaternion_wxyz': np.asarray(quaternion).tolist()}


def _hull(points):
    """2D convex footprint, not a claim of exact concave mesh coverage."""
    xy = sorted(set(map(tuple, np.asarray(points)[:, :2].tolist())))
    if len(xy) <= 2:
        return xy
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    halves = []
    for ordered in (xy, list(reversed(xy))):
        half = []
        for point in ordered:
            while len(half) >= 2 and cross(half[-2], half[-1], point) <= 0:
                half.pop()
            half.append(point)
        halves.append(half[:-1])
    return halves[0] + halves[1]


def _geometry(node, points):
    if points is None:
        node.update(geometry_status='unknown', footprint_world=None, bottom_height_m=None)
    else:
        bottom = float(points[:, 2].min())
        node.update(geometry_status='known', bottom_height_m=bottom,
                    collision_bounds_world=[points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                    footprint_world=[[x, y, bottom] for x, y in _hull(points)],
                    geometry_scope='collision_vertices_or_conservative_primitive_bounds')


def _region_check(sim, points, body, region, contacts, config):
    local = region.local(points)
    a, b, c, d = region.bounds
    eps = config.boundary_tolerance_m
    inside = bool(local[:, 0].min() >= a-eps and local[:, 0].max() <= b+eps and
                  local[:, 1].min() >= c-eps and local[:, 1].max() <= d+eps)
    bottom = float(points[:, 2].min())
    height = abs(bottom-region.height) <= config.support_tolerance_m
    contact = any(-config.penetration_tolerance_m <= x['distance_m'] <= config.support_tolerance_m
                  and x['normal_vertical'] > .7 and x['normal_force_n'] > 1e-8 for x in contacts)
    rays = bool(inside and height and support_rays(sim, body, region, points))
    ok = bool(inside and height and rays and contact)
    return {'region_id': region.region_id, 'valid': ok, 'footprint_inside': inside,
            'height_correct': bool(height), 'support_rays_hit': rays, 'load_bearing_contact': bool(contact),
            'bottom_height_m': bottom, 'support_height_m': region.height}


def build_scene_graph(sim, *, revision=0, stage='observed', config=None, station=None):
    """Read the current model/data. Resolve names afresh even if catalog IDs are stale."""
    if sim.closed:
        raise RuntimeError('cannot observe a closed simulation')
    config = config or GraphConfig()
    m, d = sim.model, sim.data
    if not all(np.isfinite(x).all() for x in (d.xpos, d.xquat, d.geom_xpos)):
        raise ValueError('nonfinite scene geometry')
    nodes, edges, issues = {}, [], []
    roots, points_by_id, geoms_by_id, regions_by_id = {}, {}, {}, {}
    for item in sorted(sim.catalog, key=lambda x: x['instance_id']):
        name = item['instance_id']
        try:
            body = m.body(item.get('mjcf_body', name)).id
        except KeyError:
            issues.append({'code': 'instance_missing_from_model', 'instance': name})
            continue
        if name == STATION_ID:
            raise ValueError('instance collides with station_start graph identity')
        count = int(m.body_jntnum[body])
        free = count == 1 and m.jnt_type[int(m.body_jntadr[body])] == mujoco.mjtJoint.mjJNT_FREE
        roots[name] = body
        node = {'kind': 'object', 'instance_id': name, 'mjcf_body': m.body(body).name,
                'asset_id': item.get('asset_id'), 'category': item.get('category'),
                'category_status': 'known' if item.get('category') is not None else 'unknown',
                'manipulable': 'unknown', 'parent_hint': item.get('parent_instance_id'),
                'root_motion': 'free' if free else 'fixed' if not count else 'articulated',
                'pose': _pose(d.xpos[body], d.xquat[body]), 'directions': {}}
        try:
            points = body_points(sim, body)
        except ValueError as exc:
            points = None
            issues.append({'code': 'unsupported_collision_geometry', 'instance': name, 'reason': str(exc)})
        _geometry(node, points)
        # Known construction capabilities, not inferred robot manipulation ability.
        editable_root = int(m.body_parentid[body]) == 0
        node['construction_capabilities'] = (['move', 'rotate', 'remove'] if (free or not count) and points is not None
                                             else ['remove']) if editable_root else []
        if points is not None:
            local = (points-d.xpos[body]) @ d.xmat[body].reshape(3, 3)
            node['collision_bounds_local'] = [local.min(axis=0).tolist(), local.max(axis=0).tolist()]
        nodes[name] = node
        points_by_id[name] = points
        geoms_by_id[name] = set(collision_geoms(sim, body))
        try:
            regions_by_id[name] = extract_regions(sim, m.body(body).name)
        except (ValueError, KeyError) as exc:
            regions_by_id[name] = []
            issues.append({'code': 'unsupported_support_geometry', 'instance': name, 'reason': str(exc)})
        # Region support uses the stable instance ID, not the current body index.
        regions_by_id[name] = [replace(r, support=name) for r in regions_by_id[name]]
        pure_plane = (not count and len(geoms_by_id[name]) == 1 and all(
            m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE and
            d.geom_xmat[g].reshape(3, 3)[2, 2] > .999 for g in geoms_by_id[name]))
        node['support_capability'] = 'planar' if regions_by_id[name] else 'horizontal_plane' if pure_plane else 'unknown'
        if pure_plane:
            node['support_plane_origin_world'] = d.geom_xpos[next(iter(geoms_by_id[name]))].tolist()

    # World-owned collision surfaces have no instance_catalog body, but can bear objects.
    for g in range(m.ngeom):
        geom_name = m.geom(g).name
        if int(m.geom_bodyid[g]) != 0 or not geom_name or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        name = 'world:' + geom_name
        if name in nodes:
            raise ValueError('duplicate world surface identity')
        plane = m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE
        horizontal = d.geom_xmat[g].reshape(3, 3)[2, 2] > .999
        quaternion = np.zeros(4)
        mujoco.mju_mat2Quat(quaternion, d.geom_xmat[g])
        node = {'kind': 'object', 'instance_id': name, 'category': None, 'category_status': 'unknown',
                'root_motion': 'fixed', 'manipulable': 'unknown', 'world_geom': geom_name,
                'pose': _pose(d.geom_xpos[g], quaternion), 'directions': {}}
        try:
            points = None if plane else geom_points(sim, g)
            regions = [] if plane else extract_regions(sim, m.body(0).name, geom_name)
        except ValueError:
            points, regions = None, []
        _geometry(node, points)
        regions = [replace(r, support=name, region_id=name + ':top') for r in regions]
        node['support_capability'] = 'horizontal_plane' if plane and horizontal else 'planar' if regions else 'unknown'
        if plane and horizontal:
            node['support_plane_origin_world'] = d.geom_xpos[g].tolist()
        nodes[name] = node
        geoms_by_id[name] = {g}
        regions_by_id[name] = regions

    for name, body in roots.items():
        for child in sorted(descendants(m, body) - {body}):
            part = m.body(child).name
            if not part or part in roots:
                continue
            if part not in nodes:
                nodes[part] = {'kind': 'part', 'pose': _pose(d.xpos[child], d.xquat[child]), 'directions': {}}
                edges.append({'predicate': 'part_of', 'args': [part, name], 'source': 'model_tree'})
    for name, regions in regions_by_id.items():
        for region in regions:
            if region.region_id in nodes:
                raise ValueError('duplicate region identity')
            nodes[region.region_id] = {**region.to_dict(), 'kind': 'region', 'region_kind': region.kind}
            edges.append({'predicate': 'has_region', 'args': [name, region.region_id], 'source': 'collision_surface'})

    geom_owners = {}
    for name, geoms in geoms_by_id.items():
        for g in geoms:
            geom_owners.setdefault(g, []).append(name)
    contact_pairs = {}
    for index, contact in enumerate(d.contact):
        force = np.zeros(6)
        mujoco.mj_contactForce(m, d, index, force)
        for left in geom_owners.get(int(contact.geom1), []):
            for right in geom_owners.get(int(contact.geom2), []):
                if left == right:
                    continue
                evidence = {'distance_m': float(contact.dist),
                            'normal_vertical': abs(float(contact.frame[2])),
                            'normal_force_n': float(force[0]),
                            'geoms': [m.geom(int(contact.geom1)).name, m.geom(int(contact.geom2)).name]}
                contact_pairs.setdefault((left, right), []).append(evidence)
                contact_pairs.setdefault((right, left), []).append(evidence)

    for name, body in roots.items():
        points = points_by_id[name]
        observations, supported, uncertain = {}, False, False
        for (subject, support), contacts in contact_pairs.items():
            if subject != name:
                continue
            capability = nodes[support]['support_capability']
            if points is None or capability == 'unknown':
                observations[support] = {'status': 'unknown', 'reason': 'unsupported_support_geometry'}
                uncertain = True
                continue
            if capability == 'horizontal_plane':
                plane_g = next(iter(geoms_by_id[support]))
                height = float(d.geom_xpos[plane_g, 2])
                ok = bool(abs(float(points[:, 2].min())-height) <= config.support_tolerance_m and
                          any(-config.penetration_tolerance_m <= c['distance_m'] <= config.support_tolerance_m and
                              c['normal_vertical'] > .7 and c['normal_force_n'] > 1e-8 for c in contacts))
                checks = [{'valid': ok, 'support_height_m': height}]
            else:
                checks = [_region_check(sim, points, body, r,
                          [c for c in contacts if r.geom in c['geoms']], config) for r in regions_by_id[support]]
                ok = any(c['valid'] for c in checks)
            observations[support] = {'status': 'pass' if ok else 'fail', 'value': ok,
                                     'evidence': {'regions': checks, 'contacts': contacts,
                                                  'scope': 'conservative_geometry_contact_not_stability_certificate'}}
            if ok:
                supported = True
                edges.append({'predicate': 'supported_by', 'args': [name, support], 'source': 'geometry_and_load_bearing_contact'})
        node = nodes[name]
        node['support_observations'] = observations
        node['support_status'] = 'pass' if supported else 'unknown' if (
            uncertain or points is None or node['root_motion'] != 'free') else 'fail'
        node['support_reason'] = 'verified_support' if supported else 'unsupported_or_kinematic_support' if node['support_status'] == 'unknown' else 'no_verified_support'

    if station is None:
        base = sim.robot.group('base')
        station = _pose([float(base[0]), float(base[1]), 0.], [np.cos(base[2]/2), 0., 0., np.sin(base[2]/2)])
    if STATION_ID in nodes:
        raise ValueError('node collides with station_start graph identity')
    nodes[STATION_ID] = {'kind': 'station', 'pose': copy.deepcopy(station), 'anchor': 'base_xy_floor_z', 'directions': {}}
    scene_id = sim.source.scene_id if hasattr(sim, 'source') else 'unknown'
    return SceneGraph(scene_id, revision, float(d.time), nodes, edges, stage, issues)
