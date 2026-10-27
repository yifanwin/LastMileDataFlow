"""Uniform disk samples and pre-begin, robot-only visible initialization."""
import copy
from contextlib import closing, nullcontext
import math
import mujoco
import numpy as np

from ...data.rendering.head_views import head_frame, TaskVisibility
from ...data.rendering.edit_views import ViewConfig
from .initialization import floor_support, room_triangles
from ...runtime.construction.preparation import preparation_guard
from ...runtime.construction.session import clone_preparation


def disk_samples(anchor, radius, spacing):
    if not np.isfinite([*anchor[:2], radius, spacing]).all() or radius <= 0 or spacing <= 0:
        raise ValueError('invalid disk lattice')
    result = []
    extent = math.floor(radius/spacing)
    for iy in range(-extent, extent+1):
        for ix in range(-extent, extent+1):
            delta = spacing*np.array([ix, iy], float)
            if np.dot(delta,delta) > radius**2+1e-9:
                continue
            xy = np.asarray(anchor[:2])+delta
            identity = f'S{len(result):04d}'
            result.append({'station_id': identity, 'xy_id': identity, 'xy': xy.tolist(),
                           'grid_index': [ix,iy], 'yaw_source': 'target_facing'})
    return result


def yaw_variants(station, offsets=(-math.pi / 4, math.pi / 4)):
    """Create explicit initial headings relative to an initialized nominal pose."""
    xy_id = station['xy_id']
    result = [station]
    pose = {key: station[key] for key in ('xy_id', 'xy', 'grid_index', 'initial')}
    for index, offset in enumerate(offsets, 1):
        yaw = station['base'][2] + offset
        yaw = float(math.atan2(math.sin(yaw), math.cos(yaw)))
        result.append({**pose, 'station_id': f'{xy_id}-Y{index:02d}',
                       'yaw': yaw, 'yaw_source': 'explicit', 'yaw_offset_rad': offset})
    return result


def robot_contacts(sim, *, target_bodies=(), fingers=(), initialization=True):
    """Initialization overlap filter OR runtime record-only robot contacts.

    Never use the initialization filter to stop an already-running simulation.
    """
    from ...runtime.outcomes.contacts import contact_observations
    contacts = [c for c in contact_observations(sim, target_bodies=target_bodies, fingers=fingers)
                if c['robot_contact']]
    if not initialization:
        return contacts
    return [c for c in contacts if not (c['allowed_floor_base'] and c['distance_m'] >= -.005) and not c['intended_target_contact']
            and c['distance_m'] < (-.002 if c['kind']=='self_collision' else -.001)]


def initialize_station(sim, station, anchor, config, *, renderer=None, check_visibility=True, evidence_path=None, floor_triangles=None):
    if sim.started or sim.closed:
        raise RuntimeError('station initialization only before begin')
    original = sim.data.qpos.copy()
    original_velocity = sim.data.qvel.copy()
    initial = copy.deepcopy(station.get('initial', sim.robot.config.initial))
    xy = np.asarray(station['xy'])
    explicit_yaw = station.get('yaw')
    yaw = explicit_yaw if explicit_yaw is not None else float(np.arctan2(anchor[1]-xy[1],anchor[0]-xy[0]))
    initial[sim.robot.initial_base_field] = [*xy, yaw]
    if explicit_yaw is None:
        yaw_adjustments = sim.robot.aim_station(initial, anchor)
    else:
        sim.robot.initialize(initial)
        yaw_adjustments = []
    _restore_object_velocity(sim, original_velocity)
    mujoco.mj_forward(sim.model, sim.data)
    robot_q = set(sim.robot.addresses.values())
    mask = [i for i in range(sim.model.nq) if i not in robot_q]
    if not np.array_equal(original[mask],sim.data.qpos[mask]):
        raise RuntimeError('initialization changed non-robot qpos')
    collisions = robot_contacts(sim)
    ground = floor_support(sim,xy,triangles=floor_triangles)
    finite = bool(np.isfinite(sim.data.qpos).all() and np.isfinite(sim.data.qvel).all())
    row = {**station, 'base': sim.robot.group('base').tolist(),
           'observation_camera': sim.robot.observation_camera, 'initial': initial,
           'geometry': 'valid' if ground and finite and not collisions else 'geometry_filtered',
           'ground': ground, 'collisions': collisions, 'non_robot_qpos_unchanged': True,
           'visibility': None, 'initial_yaw_adjustments':yaw_adjustments}
    if row['geometry'] != 'valid' or not check_visibility:
        return row
    rgb,mask,visible = head_frame(sim, sim.model.body(sim.target_id).name,
                                ViewConfig(width=config.width,height=config.height),renderer=renderer)
    row['visibility'] = visible
    if evidence_path is not None:
        import imageio.v2 as imageio
        from pathlib import Path
        evidence_path=Path(evidence_path); evidence_path.mkdir(parents=True,exist_ok=True)
        imageio.imwrite(evidence_path/'head.png',rgb)
        imageio.imwrite(evidence_path/'target_mask.png',mask.astype(np.uint8)*255)
        row['head_image']=str(evidence_path/'head.png')
    if getattr(config, 'require_target_visibility', False) and visible['visible_pixels'] < config.min_target_pixels:
        row['geometry'] = 'visibility_filtered'
    return row


def _restore_object_velocity(sim, original_velocity):
    velocity_mask = np.ones(sim.model.nv, bool)
    robot_dofs = [int(sim.model.jnt_dofadr[j]) for j in sim.robot.joints.values()]
    velocity_mask[robot_dofs] = False
    sim.data.qvel[velocity_mask] = original_velocity[velocity_mask]


def place_frozen_station(sim, station):
    if sim.started or sim.closed:
        raise RuntimeError('post-begin station placement prohibited')
    old = sim.data.qpos.copy()
    old_velocity = sim.data.qvel.copy()
    sim.robot.initialize(copy.deepcopy(station['initial']))
    # initialize() resets all qvel. A frozen station may change only the robot:
    # keep object velocities, including nonzero residual frozen motion.
    _restore_object_velocity(sim, old_velocity)
    mujoco.mj_forward(sim.model, sim.data)
    mask = np.ones(sim.model.nq,bool)
    mask[list(sim.robot.addresses.values())] = False
    if not np.array_equal(old[mask],sim.data.qpos[mask]):
        raise RuntimeError('station placement changed object poses')


def park_robot(sim, anchor, config):
    """Initialize once, outside the augmentation disk. Do not alter source objects."""
    preparation_guard(sim)
    triangles = room_triangles(sim)
    if not len(triangles):
        raise ValueError('bounded room floor required for robot parking')
    points = triangles.reshape(-1, 2)
    lo, hi = points.min(axis=0), points.max(axis=0)
    rng = np.random.default_rng(config.seed)
    trials = []
    source = sim.data.qpos.copy()
    source_mask = np.ones(sim.model.nq, bool)
    source_mask[list(sim.robot.addresses.values())] = False
    for i in range(256):
        xy = rng.uniform(lo, hi)
        if np.linalg.norm(xy - anchor[:2]) < config.radius_m + 1.25:
            continue
        initial = copy.deepcopy(sim.robot.config.initial)
        initial[sim.robot.initial_base_field] = [*xy, float(rng.uniform(-3., 3.))]
        sim.robot.initialize(initial)
        valid = bool(floor_support(sim, xy) and not robot_contacts(sim))
        trials.append({'base': initial[sim.robot.initial_base_field], 'valid': valid})
        if valid:
            if not np.array_equal(source[source_mask], sim.data.qpos[source_mask]):
                raise RuntimeError('parking changed source object state')
            return {'status': 'valid', 'trials': trials, 'initial': initial,
                    'source_qpos_unchanged': True, 'scope': 'pre_begin_parking_not_navigation'}
    raise ValueError('no_legal_robot_parking_pose')


def sample_stations(sim, task, config, *, frozen_stations=None, static_only=False, stop_after_valid=None, checked_stations=()):
    stations = disk_samples(task['anchor_world'], config.radius_m, config.spacing_m)
    if frozen_stations is not None:
        keys = ('station_id', 'xy_id', 'xy', 'grid_index', 'yaw', 'yaw_source', 'yaw_offset_rad', 'initial')
        stations = [{key: s[key] for key in keys if key in s} for s in frozen_stations]
    renderer = None if static_only else mujoco.Renderer(sim.model, height=config.height, width=config.width)
    navigation_grid = None
    if config.navigation_whole_body and getattr(config, 'require_navigation_clearance', False):
        from ...planning.navigation.astar import scene_grid, task_support_obstacles
        from ..tasks.discovery import robot_footprint
        footprint = robot_footprint(sim, whole_body=True)
        navigation_grid = scene_grid(sim, task['anchor_world'], config.radius_m, config.map_resolution_m,
            footprint['radius_m'], config.navigation_margin_m, robot_height=footprint['max'][2],
            support_obstacles=task_support_obstacles(sim, task))
    rows = []
    checked = {row['station_id']: row for row in checked_stations}
    floor_triangles = room_triangles(sim)
    if stop_after_valid is not None and (not static_only or type(stop_after_valid) is not int or stop_after_valid <= 0):
        raise ValueError('early-stop is only for positive-budget static discovery')
    try:
        for station in stations:
            if station['station_id'] in checked:
                rows.append(checked[station['station_id']])
                continue
            probe = clone_preparation(sim)
            try:
                warning_before = [w.number for w in probe.data.warning]
                row = initialize_station(probe, station, task['anchor_world'], config, renderer=renderer,
                                         check_visibility=not static_only, floor_triangles=floor_triangles)
                new_warnings = [{'index': i, 'number': int(w.number), 'lastinfo': int(w.lastinfo)}
                                for i, w in enumerate(probe.data.warning) if w.number > warning_before[i]]
                row.update(independent_simulation=True, check_scope='static_geometry' if static_only else 'actual_robot_initialization_and_visibility',
                           initialization_attempt_id=station['station_id'], new_solver_warnings=new_warnings)
                if navigation_grid is not None and row['geometry'] == 'valid':
                    cell = navigation_grid.cell(row['xy'])
                    if cell is None or not navigation_grid.free[cell]:
                        row.update(geometry='geometry_filtered', reason='conservative_navigation_clearance')
                if new_warnings:
                    row.update(geometry='geometry_filtered', reason='robot_initialization_solver_warning')
                if row['geometry'] != 'valid':
                    row.setdefault('reason', 'target_visibility' if row['geometry'] == 'visibility_filtered' else 'robot_collision_or_floor_or_nonfinite')
                rows.append(row)
            except ValueError as exc:
                rows.append({**station, 'geometry': 'geometry_filtered', 'reason': str(exc),
                             'independent_simulation': True,
                             'check_scope': 'static_geometry' if static_only else 'actual_robot_initialization_and_visibility',
                             'initialization_attempt_id': station['station_id']})
            finally:
                probe.close()
            if stop_after_valid is not None and sum(r['geometry'] == 'valid' for r in rows) >= stop_after_valid:
                break
    finally:
        if renderer is not None:
            renderer.close()
    return rows


def record_station_visibility(sim, stations, config):
    """Render only selected stations after all candidates pass physical filtering."""
    with mujoco.Renderer(sim.model, height=config.height, width=config.width) as renderer:
        for station in stations:
            with closing(clone_preparation(sim)) as probe:
                place_frozen_station(probe, station)
                _, _, visible = head_frame(probe, sim.model.body(sim.target_id).name,
                    ViewConfig(width=config.width, height=config.height), renderer=renderer)
                station['visibility'] = visible
    return stations


def select_task_stations(ctx, sim, geometry, proposal, *, measure_visibility=True):
    """Hints/explicit yaw first, then coverage; only valid initial poses consume slots."""
    from ..augmentation.selection import coverage_select
    from ..modes.geometry import lattice_points
    candidates = {row['station_id']: row for row in geometry.lattice}
    if proposal.pose_edit is not None:
        candidates.update({row['station_id']: row for row in proposal.pose_edit.stations})
    hinted = [candidates[identity] for identity in proposal.station_hints]
    if proposal.pose_edit is not None:
        hinted.extend(proposal.pose_edit.stations)
    # Occupied cells cannot host the robot base; filtering them before real
    # initialization removes the dominant cost on cluttered scenes.
    free_lattice = lattice_points(geometry, navigable=False)
    covered, _ = coverage_select([{**row, 'geometry': 'valid'} for row in free_lattice],
                                  geometry.task['anchor_world'], ctx.recipe.max_final_stations)
    order = hinted + covered + free_lattice
    seen, selected, checked = set(), [], []
    initializations = 0
    floor_triangles = room_triangles(sim)
    cached = ctx.station_checks if sim is ctx.baseline else {}
    cfg = ctx.station_config
    visibility_context = TaskVisibility(sim, geometry.task, cfg) if measure_visibility else nullcontext(None)
    with visibility_context as visibility:
        for candidate in order:
            identity = candidate['station_id']
            if identity in seen:
                continue
            seen.add(identity)
            if identity in cached:
                row = copy.deepcopy(cached[identity])
            else:
                prepared = geometry.pose_checks.get(identity)
                if prepared is None:
                    initializations += 1
                with closing(clone_preparation(sim)) as probe:
                    warnings = [warning.number for warning in probe.data.warning]
                    if prepared is not None:
                        row = copy.deepcopy(prepared)
                        row.pop('visibility_proxy', None)
                        if row['geometry'] == 'valid':
                            place_frozen_station(probe, row)
                    else:
                        try:
                            row = initialize_station(probe, candidate, geometry.task['anchor_world'], cfg,
                                                     check_visibility=False, floor_triangles=floor_triangles)
                        except ValueError as exc:
                            row = {**candidate, 'geometry': 'geometry_filtered', 'reason': str(exc)}
                    fresh = [{'index': i, 'number': int(w.number)} for i, w in enumerate(probe.data.warning)
                             if w.number > warnings[i]]
                    if fresh:
                        row.update(geometry='geometry_filtered', reason='robot_initialization_solver_warning')
                    row.update(new_solver_warnings=fresh, independent_simulation=True,
                               initialization_attempt_id=identity, check_scope='actual_robot_initialization_and_three_camera_roi')
                    if row['geometry'] == 'valid' and visibility is not None:
                        row['visibility'] = visibility.measure(probe)
                if sim is ctx.baseline:
                    cached[identity] = copy.deepcopy(row)
            checked.append(row)
            if row['geometry'] == 'valid':
                selected.append(row)
            if len(selected) == ctx.recipe.max_final_stations:
                break
    return selected, {'candidates_checked': len(checked), 'selected_count': len(selected),
                       'new_initializations': initializations, 'reused_initializations': len(checked) - initializations,
                       'pose_budget_fraction': len(selected) / ctx.recipe.max_final_stations,
                       'lattice_count': len(geometry.lattice), 'free_lattice_count': len(free_lattice),
                       'visibility_measured': measure_visibility,
                       'selected_ids': [row['station_id'] for row in selected],
                       'priority': 'station_hints_then_explicit_yaw_then_coverage',
                       'rejected_stations': [row for row in checked if row['geometry'] != 'valid']}
