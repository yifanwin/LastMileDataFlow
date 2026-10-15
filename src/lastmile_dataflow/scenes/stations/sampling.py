"""Uniform disk samples and pre-begin, robot-only visible initialization."""
import copy
import math
import mujoco
import numpy as np

from ...data.rendering.head_views import head_frame
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
            result.append({'station_id': f'S{len(result):04d}', 'xy': xy.tolist(),
                           'grid_index': [ix,iy]})
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


def initialize_station(sim, station, anchor, config, *, renderer=None, check_visibility=True, evidence_path=None):
    if sim.started or sim.closed:
        raise RuntimeError('station initialization only before begin')
    original = sim.data.qpos.copy()
    initial = copy.deepcopy(sim.robot.config.initial)
    xy = np.asarray(station['xy'])
    initial['base'] = [*xy, float(np.arctan2(anchor[1]-xy[1],anchor[0]-xy[0]))]
    yaw_adjustments=[]
    def legal_initial_yaw(value):
        from ...runtime.control.action_limits import project, yaw_interval, ANGULAR_TOLERANCE_RAD
        return project(value,yaw_interval(sim.robot),ANGULAR_TOLERANCE_RAD,'base_theta',yaw_adjustments)
    initial['base'][2]=legal_initial_yaw(initial['base'][2])
    initial['head'] = [0., .6]
    sim.robot.initialize(initial)
    cid = sim.model.camera(sim.robot.camera_names['head_camera']).id
    # Camera -Z is forward; mounting offset is not assumed equal to base +X.
    for _ in range(3):
        forward = -sim.data.cam_xmat[cid].reshape(3,3)[:,2]
        delta = np.asarray(anchor[:2])-sim.data.cam_xpos[cid,:2]
        error = np.arctan2(delta[1],delta[0])-np.arctan2(forward[1],forward[0])
        initial['base'][2] = legal_initial_yaw(float((initial['base'][2]+error+np.pi)%(2*np.pi)-np.pi))
        sim.robot.initialize(initial)
    joint = sim.robot.joints['head_1']
    low,high = sim.model.jnt_range[joint]
    # Solve pitch from the ACTUAL optical axis, avoiding an expensive 25-pose scan
    # per lattice point. The short numerical derivative also handles mount sign.
    for _ in range(2):
        forward=-sim.data.cam_xmat[cid].reshape(3,3)[:,2]
        inclination=float(np.arctan2(forward[2],np.linalg.norm(forward[:2])))
        delta=np.asarray(anchor)-sim.data.cam_xpos[cid]
        desired=float(np.arctan2(delta[2],np.linalg.norm(delta[:2])))
        pitch=initial['head'][1]; probe=min(high,pitch+.001)
        if probe == pitch: probe=max(low,pitch-.001)
        initial['head'][1]=float(probe); sim.robot.initialize(initial)
        axis=-sim.data.cam_xmat[cid].reshape(3,3)[:,2]
        slope=(float(np.arctan2(axis[2],np.linalg.norm(axis[:2])))-inclination)/(probe-pitch)
        corrected=pitch+(desired-inclination)/slope if abs(slope)>.1 else pitch
        initial['head'][1]=float(np.clip(corrected,low,high)); sim.robot.initialize(initial)
    robot_q = set(sim.robot.addresses.values())
    mask = [i for i in range(sim.model.nq) if i not in robot_q]
    if not np.array_equal(original[mask],sim.data.qpos[mask]):
        raise RuntimeError('initialization changed non-robot qpos')
    collisions = robot_contacts(sim)
    ground = floor_support(sim,xy)
    finite = bool(np.isfinite(sim.data.qpos).all() and np.isfinite(sim.data.qvel).all())
    row = {**station, 'base': sim.robot.group('base').tolist(),
           'head': sim.robot.group('head').tolist(), 'initial': initial,
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
    if visible['visible_pixels'] < config.min_target_pixels:
        row['geometry'] = 'visibility_filtered'
    return row


def place_frozen_station(sim, station):
    if sim.started or sim.closed:
        raise RuntimeError('post-begin station placement prohibited')
    old = sim.data.qpos.copy()
    old_velocity = sim.data.qvel.copy()
    sim.robot.initialize(copy.deepcopy(station['initial']))
    # initialize() resets all qvel. A frozen station may change only the robot:
    # keep object velocities, including nonzero residual frozen motion.
    velocity_mask = np.ones(sim.model.nv, bool)
    robot_dofs = [int(sim.model.jnt_dofadr[j]) for j in sim.robot.joints.values()]
    velocity_mask[robot_dofs] = False
    sim.data.qvel[velocity_mask] = old_velocity[velocity_mask]
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
        initial['base'] = [*xy, float(rng.uniform(-3., 3.))]
        sim.robot.initialize(initial)
        valid = bool(floor_support(sim, xy) and not robot_contacts(sim))
        trials.append({'base': initial['base'], 'valid': valid})
        if valid:
            if not np.array_equal(source[source_mask], sim.data.qpos[source_mask]):
                raise RuntimeError('parking changed source object state')
            return {'status': 'valid', 'trials': trials, 'initial': initial,
                    'source_qpos_unchanged': True, 'scope': 'pre_begin_parking_not_navigation'}
    raise ValueError('no_legal_robot_parking_pose')


def sample_stations(sim, task, config, *, frozen_stations=None, static_only=False, stop_after_valid=None):
    stations = disk_samples(task['anchor_world'], config.radius_m, config.spacing_m)
    if frozen_stations is not None:
        stations = [{'station_id': s['station_id'], 'xy': s['xy'], 'grid_index': s['grid_index']} for s in frozen_stations]
    renderer = None if static_only else mujoco.Renderer(sim.model, height=config.height, width=config.width)
    rows = []
    if stop_after_valid is not None and (not static_only or type(stop_after_valid) is not int or stop_after_valid <= 0):
        raise ValueError('early-stop is only for positive-budget static discovery')
    try:
        for station in stations:
            probe = clone_preparation(sim)
            try:
                warning_before = [w.number for w in probe.data.warning]
                row = initialize_station(probe, station, task['anchor_world'], config, renderer=renderer,
                                         check_visibility=not static_only)
                new_warnings = [{'index': i, 'number': int(w.number), 'lastinfo': int(w.lastinfo)}
                                for i, w in enumerate(probe.data.warning) if w.number > warning_before[i]]
                row.update(independent_simulation=True, check_scope='static_geometry' if static_only else 'actual_robot_initialization_and_visibility',
                           initialization_attempt_id=station['station_id'], new_solver_warnings=new_warnings)
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
