"""Shared geometric proxies and lightweight placements; no IK or rollout."""
import copy
from contextlib import closing
from dataclasses import replace
import itertools
from types import SimpleNamespace

import mujoco
import numpy as np

from ...data.rendering.head_views import aggregate_visibility, ray_visibility
from ...planning.astar import Grid
from ...planning.waypoints import GRASP_APPROACH_DISTANCE_M
from ...runtime.construction.session import clone_preparation
from ..augmentation.sampling import sample_placement
from ..build.proposal import AddObject
from ..geometry import body_pose, descendants, geom_points
from ..loading.asset_library import AssetRequest
from ..stations.initialization import floor_height
from ..stations.sampling import initialize_station, place_frozen_station


def lattice_points(geometry, *, navigable=False):
    grid = geometry.navigation
    rows = []
    for station in geometry.lattice:
        cell = grid.cell(station['xy'])
        if cell is not None and not geometry.occupancy.occupied[cell] and (not navigable or grid.free[cell]):
            rows.append(station)
    return rows


def nominal(ctx, station, *, sim=None, geometry=None):
    sim = ctx.baseline if sim is None else sim
    geometry = ctx.geometry if geometry is None else geometry
    identity = station['station_id']
    if identity not in geometry.pose_checks:
        with closing(clone_preparation(sim)) as probe:
            cached = ctx.station_checks.get(identity) if sim is ctx.baseline else None
            if cached is not None:
                row = copy.deepcopy(cached)
                if row['geometry'] == 'valid':
                    place_frozen_station(probe, row)
            else:
                ctx.proxy_initializations += 1
                warnings = [w.number for w in probe.data.warning]
                try:
                    row = initialize_station(probe, station, geometry.task['anchor_world'], ctx.station_config,
                                             check_visibility=False)
                except ValueError as exc:
                    row = {**station, 'geometry': 'geometry_filtered', 'reason': str(exc)}
                fresh = [i for i, w in enumerate(probe.data.warning) if w.number > warnings[i]]
                if fresh:
                    row.update(geometry='geometry_filtered', reason='robot_initialization_solver_warning')
            if row['geometry'] == 'valid':
                row['visibility_proxy'] = ray_visibility(probe, geometry.task, ctx.station_config, trace=True)
            geometry.pose_checks[identity] = row
    return geometry.pose_checks[identity]


def proxy_visibility(row, edits=()):
    observed = row.get('visibility_proxy')
    if observed is None:
        return {'complete': False, 'value': None, 'known_max': None}
    cameras = {}
    boxes = np.array([[e.placement['world_bounds']['min'], e.placement['world_bounds']['max']]
                      for e in edits if isinstance(e, AddObject)]).reshape(-1, 2, 3)
    for name, camera in observed['cameras'].items():
        if camera.get('fraction') is None:
            cameras[name] = {'fraction': None}
            continue
        count = sum(ray['visible'] and not segment_boxes(camera['origin'], ray['end'], boxes).any()
                    for ray in camera['rays'])
        cameras[name] = {'fraction': count / len(camera['rays'])}
    return aggregate_visibility(cameras)


def segment_boxes(start, end, boxes, radius=0.):
    """Segment versus expanded AABBs; capsule/channel proxy, not exact planning."""
    boxes = np.asarray(boxes).reshape(-1, 2, 3)
    delta = np.asarray(end) - start
    low, high = boxes[:, 0] - radius, boxes[:, 1] + radius
    parallel = np.abs(delta) < 1e-10
    divisor = np.where(parallel, 1., delta)
    a, b = (low - start) / divisor, (high - start) / divisor
    enter, leave = np.minimum(a, b), np.maximum(a, b)
    enter[:, parallel], leave[:, parallel] = -np.inf, np.inf
    outside = ((np.asarray(start) < low) | (np.asarray(start) > high))[:, parallel].any(axis=1)
    return ~outside & (np.maximum(0., enter.max(axis=1)) <= np.minimum(1., leave.min(axis=1)))


def obstacles(sim, geometry):
    if geometry.proxy_obstacles is None:
        excluded = descendants(sim.model, sim.model.body(geometry.task['target_body']).id)
        boxes = []
        for g in range(sim.model.ngeom):
            body = int(sim.model.geom_bodyid[g])
            if body in excluded or sim.model.body(body).name.startswith(sim.robot.config.namespace):
                continue
            if not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]) or sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE:
                continue
            points = geom_points(sim, g)
            boxes.append([points.min(axis=0), points.max(axis=0)])
        geometry.proxy_obstacles = np.asarray(boxes).reshape(-1, 2, 3)
    return geometry.proxy_obstacles


def proposal_boxes(ctx, proposal, *, sim=None, geometry=None):
    geometry = ctx.geometry if geometry is None else geometry
    boxes = obstacles(ctx.baseline if sim is None else sim, geometry)
    if sim is None and proposal.edits:
        extra = [[e.placement['world_bounds']['min'], e.placement['world_bounds']['max']]
                 for e in proposal.edits if isinstance(e, AddObject)]
        if extra:
            boxes = np.concatenate((boxes, np.asarray(extra)))
    return boxes


def navigation(ctx, proposal, *, sim=None, geometry=None):
    geometry = ctx.geometry if geometry is None else geometry
    grid = geometry.navigation
    clearance = geometry.clearance_base
    if sim is not None or not proposal.edits:
        return grid, clearance
    free = grid.free.copy()
    yy, xx = np.indices(free.shape)
    xy = grid.origin + np.stack((xx, yy), axis=-1) * grid.resolution
    distance = geometry.occupancy.obstacle_distance_m.copy()
    inflation = geometry.navigation_footprint['radius_m'] + ctx.station_config.navigation_margin_m
    for edit in proposal.edits:
        if not isinstance(edit, AddObject):
            continue
        bounds = edit.placement['world_bounds']
        lo, hi = np.asarray(bounds['min']), np.asarray(bounds['max'])
        if lo[2] >= (geometry.navigation_footprint['max'][2] if ctx.station_config.navigation_whole_body else .4):
            continue
        delta = np.maximum(np.maximum(lo[:2] - xy, xy - hi[:2]), 0.)
        d = np.maximum(0., np.linalg.norm(delta, axis=-1) - grid.resolution / np.sqrt(2))
        free &= d >= inflation
        distance = np.minimum(distance, d)
    return Grid(grid.origin, grid.resolution, free, grid.support_obstacles), distance - geometry.chassis['radius_m']


def role_profiles(ctx, role, rng, *, floor=False, size_bounds=None):
    available = None
    support = 'floor' if floor else None
    if not floor and ctx.geometry.supports:
        largest = max(ctx.geometry.supports, key=lambda r: (r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]))
        a, b, c, d = largest.bounds
        available = (b - a, d - c)
        support = next((entry['category'] for entry in ctx.baseline.catalog
                        if entry['mjcf_body'] == largest.support), None)
    return ctx.selector.select(AssetRequest(role, support=support,
        available_size_m=available,
        excluded_categories=(ctx.task['category'],), size_bounds=size_bounds or {},
        limit=ctx.settings.asset_shortlist), rng)


def asset_candidates(ctx, profiles, centers, *, floor=False, yaw=0.):
    """Placement proxies contain measured dimensions, never compile source assets."""
    target_low, target_high = ctx.geometry.target_points.min(axis=0), ctx.geometry.target_points.max(axis=0)
    regions = [None] if floor else ctx.geometry.supports
    for profile in profiles:
        for region in regions:
            if region is not None and profile.support_kind not in ('surface', 'floor_or_surface'):
                continue
            for center in centers:
                xy = np.asarray(center[:2], float)
                height = floor_height(ctx.baseline, xy) if region is None else region.height
                if height is None:
                    continue
                c, s = abs(np.cos(yaw)), abs(np.sin(yaw))
                half = np.array([c * profile.width_m + s * profile.depth_m,
                                 s * profile.width_m + c * profile.depth_m]) / 2
                lo = np.r_[xy - half, height + .003]
                hi = np.r_[xy + half, height + .003 + profile.height_m]
                if np.all(lo < target_high) and np.all(target_low < hi):
                    continue
                if region is not None:
                    corners = np.array(list(itertools.product(*zip(lo, hi))))
                    local = region.local(corners)
                    a, b, c, d = region.bounds
                    if not (local[:, 0].min() >= a + .01 and local[:, 0].max() <= b - .01
                            and local[:, 1].min() >= c + .01 and local[:, 1].max() <= d - .01):
                        continue
                yield AddObject({'asset_id': profile.asset_id, 'category': profile.category,
                    'kind': 'ground' if floor else 'surface', 'profile_proxy': True,
                    'point_xy': xy.tolist(), 'yaw_rad': yaw,
                    'pose': {'position': [*xy, height + .003 + profile.height_m / 2],
                             'quaternion_wxyz': [float(np.cos(yaw / 2)), 0., 0., float(np.sin(yaw / 2))]},
                    'support_height_m': height, 'support_geom': region.geom if region else None,
                    'support_instance': region.support if region else None,
                    'world_bounds': {'min': lo.tolist(), 'max': hi.tolist()}})


def resolve_assets(ctx, proposal):
    """Called only after cheap pass; precise placement uses a few cached assets."""
    edits = []
    for edit in proposal.edits:
        if not isinstance(edit, AddObject) or not edit.placement.get('profile_proxy'):
            edits.append(edit)
            continue
        p = edit.placement
        geometry = ctx.assets.geometry(p['asset_id'])
        region = next((r for r in ctx.geometry.supports if r.geom == p['support_geom']), None)
        placement, reason = sample_placement(ctx.baseline, geometry,
            {'point_id': proposal.proposal_id, 'xy': p['point_xy'], 'sources': [{'kind': proposal.proposal_intent}]},
            p['yaw_rad'], SimpleNamespace(placement_gap_m=.003), region=region)
        if placement is None:
            raise ValueError(reason)
        edits.append(AddObject(placement))
        profile = next(p for p in ctx.selector.profiles if p.asset_id == placement['asset_id'])
        ctx.selector.record_use(profile)
    return replace(proposal, edits=edits)


def shift_repair(ctx, proposal, direction):
    norm = np.linalg.norm(direction)
    if not proposal.edits or norm == 0:
        return None
    delta = np.r_[np.asarray(direction)[:2] / norm * ctx.settings.repair_translation_m, 0.]
    repaired = copy.deepcopy(proposal)
    for edit in repaired.edits:
        if isinstance(edit, AddObject):
            p = edit.placement
            p['pose']['position'] = (np.asarray(p['pose']['position']) + delta).tolist()
            p['point_xy'] = (np.asarray(p['point_xy']) + delta[:2]).tolist()
            for key in ('min', 'max'):
                p['world_bounds'][key] = (np.asarray(p['world_bounds'][key]) + delta).tolist()
    return repaired


def arm_channels(ctx, station, *, sim=None, geometry=None, approach=False):
    sim = ctx.baseline if sim is None else sim
    geometry = ctx.geometry if geometry is None else geometry
    row = nominal(ctx, station, sim=sim, geometry=geometry)
    if row['geometry'] != 'valid':
        return []
    key = ('approach' if approach else 'arm', row['station_id'])
    if key not in geometry.pose_checks:
        with closing(clone_preparation(sim)) as probe:
            place_frozen_station(probe, row)
            robot = probe.robot
            bodies = [int(probe.model.jnt_bodyid[robot.joints[name]]) for name in robot.groups['arm']]
            joints = probe.data.xpos[bodies]
            physical = np.flatnonzero(np.isin(probe.model.geom_bodyid, bodies) &
                ((probe.model.geom_contype != 0) | (probe.model.geom_conaffinity != 0)))
            if not len(physical):
                raise ValueError('nominal_arm_collision_geometry_missing')
            widths = []
            for geom in physical:
                local = geom_points(probe, geom) @ probe.data.geom_xmat[geom].reshape(3, 3)
                widths.append(float(np.ptp(local, axis=0).min() / 2))
            radius = float(np.median(widths))
            fingers = set().union(*robot.finger_bodies(robot.arms[0]))
            finger_geoms = np.flatnonzero(np.isin(probe.model.geom_bodyid, list(fingers)))
            finger_points = np.concatenate([geom_points(probe, g) for g in finger_geoms])
            gripper_radius = float(np.linalg.norm(np.ptp(finger_points, axis=0)) / 2)
            tcp = probe.data.site_xpos[robot.tcp_site(robot.arms[0])]
            goals = [body_pose(probe, probe.model.body(g['body']).id) @ g['pose_local'] for g in ctx.grasps]
            goals.sort(key=lambda goal: np.linalg.norm(goal[:3, 3] - tcp))
            channels = []
            for index, goal in enumerate(goals[:ctx.execution.max_grasp_candidates]):
                sign = -1 if geometry.task['operation'] == 'open' else 1
                end = goal[:3, 3] + goal[:3, 2] * ctx.execution.approach_offset_m * sign
                pre = end - goal[:3, 2] * GRASP_APPROACH_DISTANCE_M
                points = np.array([pre, end]) if approach else np.array([joints[0], joints[3], pre])
                channels.append({'points': points, 'radius': gripper_radius if approach else radius,
                                 'goal_radius': gripper_radius,
                                 'grasp_index': index, 'scope': 'geometric_nominal_channel_not_solved_joint_trajectory'})
            geometry.pose_checks[key] = channels
    return geometry.pose_checks[key]


def channel_blocked(channel, boxes):
    return any(segment_boxes(a, b, boxes, channel['radius']).any()
               for a, b in zip(channel['points'], channel['points'][1:]))
