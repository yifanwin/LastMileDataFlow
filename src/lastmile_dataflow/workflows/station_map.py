"""Fixed-base feasible trajectory quality maps; no physical stepping or grasp execution."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import json
import logging
import math
import multiprocessing
import os
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

from ..config import load_base_map_scene
from ..scenes.geometry import body_pose, descendants, tcp_pose
from ..utils.io import create_run, read_json, write_json

logger = logging.getLogger(__name__)

STATUS = ('not_evaluated', 'no_heading', 'no_floor', 'base_collision', 'distance_filtered',
          'ik_no_solution', 'valid', 'initial_state_invalid', 'no_valid_trajectory', 'incomplete')
CODE = {name: i for i, name in enumerate(STATUS)}
MAP_KIND = 'base-trajectory-quality-map-v1'
QUALITY_MAP_KINDS = (MAP_KIND, 'base-quality-map-v1')


def reject_quality_map(saved):
    """A quality map is not a physical calibration; S0/S1 rules from maps are not defined yet."""
    if saved.get('kind') in QUALITY_MAP_KINDS:
        raise ValueError('a base quality map (evaluate-stations) is not a physical calibration: it has no trials '
                         'or success rates, and S0/S1 selection from quality maps is not implemented')


@dataclass(frozen=True)
class MapScene:
    """Picklable way to reopen the same frozen scene in scoring processes."""
    scene_id: str
    kind: str
    path: str
    metadata: str | None
    robot: object
    target: str
    asset_id: str
    assets_dir: str

    def open(self):
        from ..runtime.simulation import Simulation
        if self.kind == 'source':
            from ..scenes.loading.source import SceneSource
            return Simulation.from_source(SceneSource(self.scene_id, self.path, self.metadata),
                                          self.robot, target=self.target)
        return Simulation.from_snapshot(self.path, self.robot, target=self.target)


def recipe_scenes(path):
    scene = load_base_map_scene(path)
    metadata = json.loads(Path(scene['metadata']).read_text())['objects']
    target = next(name for name, item in metadata.items()
                  if item['asset_id'] == scene['asset_id'] and item['object_id'] == scene['object_id'])
    robot = scene['robot']
    initial = deepcopy(robot.initial)
    initial.update(base_xyyaw=list(scene['reference_base_xyyaw']), lift=scene['lift'],
                   gripper_width={'right': scene['gripper_width']})
    return [MapScene(scene['scene_id'], 'source', scene['scene_xml'], scene['metadata'],
                     replace(robot, initial=initial), target, scene['asset_id'], scene['assets_dir'])]


def dataset_scenes(dataset, assets_dir, samples=None):
    from ..config import robot_config
    from ..config import read_config
    from ..scenes.dataset import load_scenes
    result = []
    for scene in load_scenes(dataset, samples):
        task = scene['task']
        if task['operation'] != 'pick':
            raise ValueError(f"{scene['scene_id']}: base quality maps currently cover pick targets only")
        robot = robot_config(read_config(Path(scene['scene_path']) / 'config.json')['robot'])
        result.append(MapScene(scene['scene_id'], 'snapshot', scene['scene_path'], None, robot,
                               task['target_body'], task['asset_id'], str(assets_dir)))
    return result


def select_grasps(poses, count, position_scale, rotation_scale):
    """Deterministic farthest-point subset of object-frame TCP poses, first row first."""
    positions = np.array([p[:3, 3] for p in poses])
    rotations = np.array([p[:3, :3] for p in poses])
    def distance(index):
        traces = np.einsum('ij,nij->n', rotations[index], rotations)
        angle = np.arccos(np.clip((traces - 1) / 2, -1, 1))
        return np.sum((positions - positions[index]) ** 2, axis=1) / position_scale ** 2 + angle ** 2 / rotation_scale ** 2
    chosen = [0]
    nearest = distance(0)
    while len(chosen) < min(count, len(poses)):
        index = int(np.argmax(nearest))  # ties keep the lowest source row
        chosen.append(index)
        nearest = np.minimum(nearest, distance(index))
    return chosen


def tcp_local(pose, offset, roll):
    """Asset grasp -> planning TCP in the object frame (same roll/offset as fixed pick)."""
    result = np.asarray(pose, float).copy()
    result[:3, 3] += result[:3, 2] * offset
    c, s = math.cos(roll), math.sin(roll)
    result[:3, :3] = result[:3, :3] @ np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])
    return result


def base_matrix(x, y, yaw, height):
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, -s, 0., x], [s, c, 0., y], [0., 0., 1., height], [0., 0., 0., 1.]])


def heading_mean(status, quality):
    """Per grasp (Ny, Nx, K): mean quality over a position's valid headings, and their count.

    Headings without a valid configuration are left out rather than scored zero:
    feasibility and quality stay separate, and the count shows how many of the
    headings the mean covers. NaN where no heading is valid.
    """
    valid = status == CODE['valid']
    count = valid.sum(axis=2)
    total = np.where(valid, quality, 0.).sum(axis=2)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan), count


# ---- scoring processes ------------------------------------------------------

_WORKER = {}


def _start_worker(scene, config, support_names):
    from ..planning.manipulability import ScoringModel
    from ..planning.robots.panda_omron import self_collision_links
    sim = scene.open()
    m = sim.model
    links, ignore = self_collision_links(sim.robot)
    _WORKER.update(sim=sim, config=config, initial=deepcopy(sim.robot.config.initial),
                   model=ScoringModel(sim, 'right', config, link_bodies=links, ignore=ignore,
                                      support_bodies=[m.body(name).id for name in support_names]))


    from ..planning.trajectory_validation import TrajectoryValidator
    _WORKER['validator'] = TrajectoryValidator(sim, _WORKER['model'], config)


def _place_worker(base):
    sim = _WORKER['sim']
    if _WORKER.get('base') != tuple(base):
        initial = deepcopy(_WORKER['initial'])
        initial['base_xyyaw'] = list(base)
        sim.robot.initialize(initial)
        _WORKER.update(base=tuple(base), quality_cache={})
    return sim, _WORKER['validator']


def _initial_valid(base):
    sim, validator = _place_worker(base)
    return validator.initial_valid(np.asarray(_WORKER['initial']['arm_q']['right']))


def _terminal_checks(job):
    _, validator = _place_worker(job['base'])
    valid = np.zeros(job['solver_success'].shape, bool)
    for lane, seed in zip(*np.nonzero(job['solver_success'])):
        valid[lane, seed] = validator.terminal_valid(job['q'][lane, seed], job['goals'][lane])
    return valid


def _evaluate_trajectories(job):
    from ..planning.trajectory_quality import joint_distance, score_trajectory
    from ..planning.curobo.planner import pose_error
    sim, validator = _place_worker(job['base'])
    model, cfg = _WORKER['model'], _WORKER['config']
    rotation = base_matrix(*job['base'], 0.)[:3, :3]
    rows, validation_s, scoring_s = [], 0., 0.
    from ..planning.trajectory_validation import TrajectoryStateScorer
    evaluate = TrajectoryStateScorer(validator, rotation, job['goal'], _WORKER['quality_cache'])
    for candidate in job['candidates']:
        row = dict(candidate=candidate, quality=None, validation=None)
        if candidate.curve is not None:
            final = candidate.curve.at(candidate.curve.time[-1])
            validator.set_q(final)
            row['distance_seed_final'] = float(joint_distance(candidate.q_seed, final, model.upper-model.lower))
            row['fk_error'] = pose_error(tcp_pose(sim, 'right'), job['goal'])
            t = time.monotonic()
            row['validation'] = validator.validate(candidate.curve, job['q0'], job['goal'],
                                                     job['velocity_limits'], job['acceleration_limits'])
            validation_s += time.monotonic() - t
            if row['validation'].valid:
                t = time.monotonic()
                row['quality'] = score_trajectory(candidate.curve, row['validation'].trace, evaluate,
                                                  cfg, row['validation'].approach_time)
                scoring_s += time.monotonic() - t
                collision = next((point for point in row['quality'].samples if not point['valid']), None)
                if collision is not None:
                    row['validation'].valid = False
                    row['validation'].reason = 'collision_at_score'
                    row['validation'].failure_time = collision['time']
                    row['quality'] = None
                row['validation'].trace = None  # dense validation trace need not cross the process boundary
        rows.append(row)
    return rows, validation_s, scoring_s


@dataclass
class TrajectoryQuery:
    lane: int
    grasp: int
    query: int
    candidates: list
    seeds: np.ndarray
    planning_s: float = 0.
    pending: list = field(default_factory=list)
    error: Exception | None = None


def _submit_trajectories(planner, pool, requests, base, goals, q0, velocity_limits, acceleration_limits):
    """Pack one yaw into fixed GPU lanes and overlap per-trajectory CPU work.

    Requests stay ordered. The caller collects every result before the next yaw,
    so concurrency does not change which completed queries supply warm starts.
    """
    entries = []
    for request in requests:
        request.pending = []
        entries.extend((request, candidate, seed) for candidate, seed in zip(request.candidates, request.seeds))
    elapsed_batches, evaluated = [], []
    for start in range(0, len(entries), planner.batch_size):
        batch = entries[start:start+planner.batch_size]
        candidates = [item[1] for item in batch]
        started = time.monotonic()
        try:
            _, elapsed = planner.trajectory_candidates(np.array([goals[item[0].grasp] for item in batch]),
                                                       candidates, np.array([item[2] for item in batch]))
        except Exception as error:
            elapsed = time.monotonic() - started
            for request, _, _ in batch:
                request.error = error
        else:
            for request, candidate, _ in batch:
                curve = candidate.curve
                duplicate = next((pending for grasp, old, pending in evaluated
                    if grasp == request.grasp and curve is not None and old is not None
                    and np.array_equal(curve.time, old.time)
                    and np.array_equal(curve.polynomial.c, old.polynomial.c)), None)
                if duplicate is None:
                    pending = pool.apply_async(_evaluate_trajectories, ({
                        'base': base, 'goal': goals[request.grasp], 'q0': q0, 'candidates': [candidate],
                        'velocity_limits': velocity_limits, 'acceleration_limits': acceleration_limits},))
                    evaluated.append((request.grasp, curve, pending))
                else:
                    pending = duplicate
                request.pending.append((pending, duplicate is not None))
        elapsed_batches.append(elapsed)
        # Batch wall time is allocated by lane count, never counted once per grasp.
        for request, _, _ in batch:
            request.planning_s += elapsed / len(batch)
    return elapsed_batches


# ---- one scene --------------------------------------------------------------

def _planner_options(config):
    return SimpleNamespace(seed=config.seed, head_fov_enabled=False, width=320, height=240,
        head_fov_margin=.9, head_fov_weight=1e4, head_fov_min_depth_m=.01,
        num_ik_seeds=config.seeds_per_goal, num_trajopt_seeds=1, max_candidates=1, time_dilation=.5,
        environment_representation='merged_hull', operation_base_mode='fixed', trajectory_quality=config)


def _support_bodies(sim):
    """Bodies in contact with the target subtree in the frozen initial state."""
    m, d = sim.model, sim.data
    target = descendants(m, sim.target_id)
    robot = sim.robot.config.namespace
    result = set()
    for contact in d.contact[:d.ncon]:
        bodies = int(m.geom_bodyid[contact.geom1]), int(m.geom_bodyid[contact.geom2])
        for own, other in (bodies, bodies[::-1]):
            if own in target and other not in target and not m.body(other).name.startswith(robot) and other != 0:
                result.add(m.body(other).name)
    return sorted(result)


def build_scene_map(scene, config, root, *, workers=None):
    """Build and save one scene's map under `root`; returns the summary."""
    from ..planning.curobo.planner import V2Planner
    from ..planning.robots.panda_omron import arm_reach_bound
    from ..scenes.stations.initialization import room_triangles
    from ..scenes.stations.sampling import base_clearance, heading_fan
    from ..scenes.tasks.discovery import grasp_candidates, local_bounds

    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    timing = dict(geometry_s=0., ik_s=0.)
    sim = scene.open()
    m = sim.model
    initial = deepcopy(sim.robot.config.initial)
    reference = list(map(float, sim.robot.base_pose()))
    object_pose = body_pose(sim, sim.target_id)
    lo, hi = local_bounds(sim, sim.target_id)
    center = (object_pose @ np.r_[(lo + hi) / 2, 1.])[:3]
    pool_rows = grasp_candidates({'operation': 'pick', 'asset_id': scene.asset_id, 'target_body': scene.target,
                                  'local_bounds': [lo.tolist(), hi.tolist()]}, scene.assets_dir, allow_fallback=False)
    summary = {'kind': MAP_KIND, 'scene_id': scene.scene_id, 'status': 'incomplete'}
    if not pool_rows:
        summary.update(status='no_grasp_candidates')
        write_json(root / 'summary.json', summary)
        sim.close()
        return summary
    local = [tcp_local(row['pose_local'], config.approach_offset_m, config.grasp_roll_rad) for row in pool_rows]
    chosen = select_grasps(local, config.max_grasps, config.grasp_position_scale_m, config.grasp_rotation_scale_rad)
    grasps_local = [local[i] for i in chosen]
    goals = np.array([object_pose @ g for g in grasps_local])
    K = len(goals)

    # Fixed robot geometry measured at the reference station.
    height = float(sim.data.site_xpos[m.site(sim.robot.config.namespace + 'base_site').id][2])
    reference_base = base_matrix(*reference[:2], reference[2], height)
    shoulder = np.linalg.solve(reference_base, np.r_[
        sim.data.xpos[m.body(sim.robot.config.namespace + 'robot0_link1').id], 1.])[:3]
    reach = arm_reach_bound()
    moving = descendants(m, m.body(sim.robot.config.namespace + 'robot0_link1').id)
    base_bodies = {m.body(b).name for b in range(m.nbody)
                   if m.body(b).name.startswith(sim.robot.config.namespace) and b not in moving}
    support = _support_bodies(sim)

    # Grid anchored on the object centre, covering every grasp's reach disk.
    radius = reach + float(np.hypot(*shoulder[:2]))
    spacing = config.spacing_m
    low = np.floor((goals[:, :2, 3].min(axis=0) - radius - center[:2]) / spacing).astype(int)
    high = np.ceil((goals[:, :2, 3].max(axis=0) + radius - center[:2]) / spacing).astype(int)
    if config.region_m is not None:
        dx0, dx1, dy0, dy1 = config.region_m
        low = np.maximum(low, np.ceil(np.array([dx0, dy0]) / spacing - 1e-9).astype(int))
        high = np.minimum(high, np.floor(np.array([dx1, dy1]) / spacing + 1e-9).astype(int))
    xs = center[0] + spacing * np.arange(low[0], high[0] + 1)
    ys = center[1] + spacing * np.arange(low[1], high[1] + 1)
    offsets = np.deg2rad(np.array(config.heading_offsets_deg))
    Ny, Nx, M = len(ys), len(xs), len(offsets)
    heading_order = [int(i) for i in np.argsort(offsets)]

    shape = (Ny, Nx, M, K)
    status = np.zeros(shape, np.int8)
    quality = np.full(shape, np.nan)
    valid_seeds = np.zeros(shape, np.int16)
    converged_seeds = np.zeros(shape, np.int16)
    executed_seeds = np.zeros(shape, np.int16)
    distinct_valid = np.zeros(shape, np.int16)
    post_quality = np.full(shape, np.nan)
    attempted = np.zeros(shape, np.int16)
    completed = np.zeros(shape, np.int16)
    retained = np.zeros(shape, np.int16)
    representatives_count = np.zeros(shape, np.int16)
    r_plan = np.full(shape, np.nan)
    cluster_share = np.full(shape, np.nan)
    bottleneck_phase = np.full(shape, '', dtype='<U20')
    warm_seeds = np.zeros(shape, np.int16)
    best_seed = np.full(shape, -1, np.int16)
    best_q = np.full((*shape, 7), np.nan)
    coverage = np.ones(shape, bool)
    yaw = np.full((Ny, Nx, M), np.nan)
    warm_cache = {}

    corners = np.array([[x, y] for x in xs[[0, -1]] for y in ys[[0, -1]]])
    world_radius = float(np.max(np.linalg.norm(corners - np.asarray(reference[:2]), axis=1)) + reach + 1.)
    scene_record = {
        'scene_id': scene.scene_id, 'kind': scene.kind, 'path': scene.path, 'target_body': scene.target,
        'asset_id': scene.asset_id, 'object_pose_world': object_pose.tolist(), 'object_center_world': center.tolist(),
        'support_bodies': support, 'support_penalized': config.penalize_support_surface,
        'reference_base_xyyaw': reference, 'base_height_m': height, 'lift_m': float(sim.robot.lift_target),
        'gripper_width_m': float(sim.robot.gripper_width('right')), 'arm_q_initial': initial['arm_q']['right'],
        'shoulder_in_base_m': shoulder.tolist(), 'reach_bound_m': reach, 'search_radius_m': radius,
        'grid': {'origin_xy': [float(xs[0]), float(ys[0])], 'spacing_m': spacing, 'shape_yx': [Ny, Nx],
                 'region_m': config.region_m,
                 'anchor': 'object collision-bounds centre', 'heading_offsets_rad': offsets.tolist(),
                 'heading_zero': 'base x-axis pointing at the object centre'},
        'grasp_candidates': len(pool_rows), 'grasp_source': pool_rows[0]['source'],
        'grasps': [{'index': k, 'source_row': pool_rows[i]['row'], 'tcp_object': grasps_local[k].tolist(),
                    'tcp_world': goals[k].tolist()} for k, i in enumerate(chosen)],
        'planner_world_radius_m': world_radius, 'quality_config': asdict(config),
        'validation': 'discrete time/joint/near-geometry checks; analytic polynomial motion limits',
        'curve': 'native B-spline polynomial reconstructed at evaluated knot boundaries'}
    write_json(root / 'scene.json', scene_record)
    logger.info('scene=%s grasps=%s/%s grid=%sx%s headings=%s reach=%.3f', scene.scene_id, K, len(pool_rows), Nx, Ny, M, reach)

    sim.robot.initialize(initial)
    (root / 'planner').mkdir()
    planner = V2Planner(sim, _planner_options(config), 'right', root / 'planner',
                        workspace=(center, max(1., (world_radius - 1.) / 2)), batch_size=max(K, 2 * config.max_representatives), ik_only=False)
    if not np.allclose(planner.base, reference_base, atol=1e-6):
        raise ValueError('planning base frame differs from the analytic station frame')
    joints = [m.joint(sim.robot.config.namespace+f'robot0_joint{i}').id for i in range(1, 8)]
    bounds = (m.jnt_range[joints, 0].copy(), m.jnt_range[joints, 1].copy())
    from ..planning.robots.panda_omron import robot_description, ARM_NAMES
    urdf = robot_description()[0].getroot()
    velocity_limits = np.array([float(urdf.find(f"joint[@name='{name}']/limit").get('velocity')) for name in ARM_NAMES])
    acceleration_limits = np.full(7, 5.)
    scene_record.update(joint_names=list(ARM_NAMES), joint_lower=bounds[0].tolist(), joint_upper=bounds[1].tolist(),
                        velocity_limits=velocity_limits.tolist(), acceleration_limits=acceleration_limits.tolist())
    write_json(root / 'scene.json', scene_record)
    triangles = room_triangles(sim)
    t = time.monotonic()
    from ..planning.navigation.astar import scene_grid
    extent = (float(np.max(np.abs(np.r_[xs[[0, -1]] - center[0], ys[[0, -1]] - center[1]]))) + spacing) * math.sqrt(2)
    background = scene_grid(sim, center, extent, .02, 0., 0., robot_height=1.5)
    timing['geometry_s'] += time.monotonic() - t
    # Spawned workers import NumPy/SciPy after inheriting these limits.
    # Each trajectory already owns a process; BLAS threads must not multiply it.
    for variable in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS'):
        os.environ[variable] = '1'
    ctx = multiprocessing.get_context('spawn')
    pool = ctx.Pool(workers or config.scoring_workers, initializer=_start_worker,
                    initargs=(scene, config, support))
    chunk, chunk_index, done_stations = [], 0, 0

    timing.update(planning_s=0., validation_cpu_s=0., trajectory_scoring_cpu_s=0., cpu_wait_s=0.,
                  planning_batches=0, planning_lanes=0, planning_padding_lanes=0, reused_trajectories=0)
    q0 = np.asarray(initial['arm_q']['right'])
    try:
        for iy in range(Ny):
            for ix in range(Nx):
                warm_cache = {cell: entries for cell, entries in warm_cache.items()
                              if config.spacing_m * (iy-cell[1]) <= config.warm_start_radius_m + 1e-9}
                fan = heading_fan((xs[ix], ys[iy]), center, offsets)
                if fan is None:
                    status[iy, ix] = CODE['no_heading']
                    continue
                for mi in heading_order:
                    yaw[iy, ix, mi] = fan[mi]
                    base = (float(xs[ix]), float(ys[iy]), float(fan[mi]))
                    station = (iy * Nx + ix) * M + mi
                    t = time.monotonic()
                    clear, _ = base_clearance(sim, base, initial, base_bodies, floor_triangles=triangles)
                    if clear != 'clear':
                        status[iy, ix, mi] = CODE[clear]
                        timing['geometry_s'] += time.monotonic() - t
                        continue
                    world = base_matrix(*base[:2], base[2], height)
                    pivot = (world @ np.r_[shoulder, 1.])[:3]
                    lanes = [k for k in range(K) if np.linalg.norm(goals[k][:3, 3] - pivot) <= reach]
                    status[iy, ix, mi, [k for k in range(K) if k not in lanes]] = CODE['distance_filtered']
                    timing['geometry_s'] += time.monotonic() - t
                    if not lanes:
                        continue
                    if not pool.apply(_initial_valid, (base,)):
                        status[iy, ix, mi, lanes] = CODE['initial_state_invalid']
                        continue
                    station_initial = deepcopy(initial)
                    station_initial.update(base_xyyaw=list(base))
                    sim.robot.initialize(station_initial)
                    seeds, warm = _initial_values(warm_cache, (ix, iy), base[2], lanes, K, config, bounds,
                                                  [config.seed + station*K+k for k in range(K)])
                    padded = [*lanes, *[lanes[0]] * (planner.batch_size - len(lanes))]
                    planner.bind_station(world)
                    t = time.monotonic()
                    result = planner.ik_candidates([goals[k] for k in padded],
                        [config.seed+station*K+k for k in padded], seeds_per_goal=config.seeds_per_goal,
                        seed_config=None if seeds is None else seeds[padded])
                    timing['ik_s'] += time.monotonic() - t
                    valid = pool.apply(_terminal_checks, ({'base': base, 'goals': goals[lanes],
                        'q': result['q'][:len(lanes)], 'solver_success': result['solver_success'][:len(lanes)]},))
                    requests = []
                    for lane, k in enumerate(lanes):
                        cell = (iy, ix, mi, k)
                        query = station*K+k
                        executed_seeds[cell] = config.seeds_per_goal
                        converged_seeds[cell] = int(result['solver_success'][lane].sum())
                        valid_seeds[cell] = int(valid[lane].sum())
                        warm_seeds[cell] = warm[k]
                        coverage[cell] = result['full_seed_coverage']
                        if not coverage[cell]:
                            status[cell] = CODE['incomplete']
                            continue
                        terminals = result['q'][lane][valid[lane]]
                        from ..planning.trajectory_quality import representatives, ranked_candidates, terminal_clusters, planning_rate
                        indices = representatives(terminals, bounds[1]-bounds[0], config.joint_distance_threshold,
                                                  config.max_representatives)
                        reps = terminals[indices]
                        representatives_count[cell] = len(reps)
                        if not len(reps):
                            status[cell] = CODE['ik_no_solution']
                            continue
                        history = _neighbours(warm_cache, (ix, iy), base[2], k, config)
                        candidates, initial_paths = _candidate_seeds(reps, q0, planner.motion.trajopt_solver.action_horizon,
                                                                     bounds, config, query, history)
                        requests.append(TrajectoryQuery(lane, k, query, candidates, initial_paths))
                    batches = _submit_trajectories(planner, pool, requests, base, goals, q0,
                                                   velocity_limits, acceleration_limits)
                    timing['planning_s'] += sum(batches)
                    timing['planning_batches'] += len(batches)
                    timing['planning_lanes'] += sum(len(item.candidates) for item in requests)
                    timing['planning_padding_lanes'] += len(batches)*planner.batch_size - sum(len(item.candidates) for item in requests)
                    for request in requests:
                        lane, k, query = request.lane, request.grasp, request.query
                        cell = (iy, ix, mi, k)
                        candidates, elapsed = request.candidates, request.planning_s
                        attempted[cell] = sum(c.submitted for c in candidates)
                        wait_started = time.monotonic()
                        try:
                            results = [(pending.get(), reused) for pending, reused in request.pending]
                            if request.error is not None:
                                raise request.error
                            from ..planning.trajectory_quality import joint_distance
                            rows = [dict(item[0][0], candidate=candidate, evaluation_reused=reused)
                                    for candidate, (item, reused) in zip(candidates, results)]
                            for row in rows:
                                candidate = row['candidate']
                                if candidate.curve is not None:
                                    row['distance_seed_final'] = float(joint_distance(candidate.q_seed,
                                        candidate.curve.at(candidate.curve.time[-1]), bounds[1]-bounds[0]))
                            validation_s = sum(item[1] for item, reused in results if not reused)
                            scoring_s = sum(item[2] for item, reused in results if not reused)
                            timing['reused_trajectories'] += sum(reused for _, reused in results)
                            timing['validation_cpu_s'] += validation_s
                            timing['trajectory_scoring_cpu_s'] += scoring_s
                        except Exception as error:
                            logger.exception('trajectory query %s incomplete', query)
                            status[cell] = CODE['incomplete']
                            chunk.append(dict(query=query, base=base, grasp=k, error=str(error),
                                rows=[dict(candidate=c, validation=None, quality=None) for c in candidates], kept=[],
                                n_attempted=int(attempted[cell]), n_completed=0, n_valid=0, n_retained=0))
                            continue
                        finally:
                            timing['cpu_wait_s'] += time.monotonic() - wait_started
                        completed[cell] = len(rows)
                        good = [row for row in rows if row['quality'] is not None]
                        kept = ranked_candidates(good, config.retain_trajectories)
                        retained[cell] = len(kept)
                        r_plan[cell] = planning_rate(len(candidates), len(rows), len(good))
                        labels, clusters = terminal_clusters([row['candidate'].curve.at(row['candidate'].curve.time[-1])
                                                             for row in good], bounds[1]-bounds[0], config.joint_distance_threshold)
                        distinct_valid[cell] = len(clusters)
                        cluster_share[cell] = max(map(len, clusters))/len(good) if good else np.nan
                        for row, label in zip(good, labels):
                            row['terminal_cluster'] = int(label)
                        if kept:
                            winner = kept[0]
                            status[cell] = CODE['valid']
                            quality[cell], post_quality[cell] = winner['quality'].full, winner['quality'].post
                            bottleneck_phase[cell] = winner['quality'].full_bottleneck['phase']
                            best_seed[cell] = winner['candidate'].candidate_id
                            best_q[cell] = winner['candidate'].curve.at(winner['candidate'].curve.time[-1])
                            warm_cache.setdefault((ix, iy), {}).setdefault(k, []).extend(
                                WarmTrajectory(base[2], query, row['candidate'].candidate_id,
                                               row['candidate'].curve.q.copy()) for row in good)
                        else:
                            status[cell] = CODE['no_valid_trajectory']
                        chunk.append(dict(query=query, base=base, grasp=k, rows=rows, kept=kept,
                            ik_q=result['q'][lane], ik_solver_success=result['solver_success'][lane], ik_valid=valid[lane],
                            timing=dict(planning_s=elapsed, validation_cpu_s=validation_s, scoring_cpu_s=scoring_s),
                            n_attempted=len(candidates), n_completed=len(rows), n_valid=len(good), n_retained=len(kept)))
                        done_stations += 1
                        if done_stations % config.chunk_stations == 0:
                            chunk, chunk_index = _flush_chunk(root, chunk, chunk_index)
            logger.info('scene=%s row %s/%s valid=%s elapsed=%.0fs', scene.scene_id, iy+1, Ny,
                        int((status == CODE['valid']).sum()), time.monotonic()-started)
        summary['status'] = 'incomplete' if np.any(status == CODE['incomplete']) else 'complete'
    finally:
        chunk, chunk_index = _flush_chunk(root, chunk, chunk_index)
        pool.close()
        pool.join()
        planner.close()
        position_quality, valid_headings = heading_mean(status, quality)
        np.savez_compressed(root / 'map.npz', kind=MAP_KIND, x=xs, y=ys,
            xy=np.stack(np.meshgrid(xs, ys), axis=-1), heading_offsets_rad=offsets, yaw=yaw,
            status=status, status_names=np.array(STATUS), quality=quality, post_quality=post_quality,
            position_quality=position_quality, valid_headings=valid_headings,
            valid_seeds=valid_seeds, converged_seeds=converged_seeds, executed_seeds=executed_seeds,
            distinct_valid=distinct_valid, warm_start_seeds=warm_seeds, best_seed=best_seed, best_q=best_q,
            full_seed_coverage=coverage, n_attempted=attempted, n_completed=completed, n_retained=retained,
            n_valid=np.rint(np.nan_to_num(r_plan)*attempted).astype(int),
            r_plan=r_plan, representatives=representatives_count, cluster_share=cluster_share,
            bottleneck_phase=bottleneck_phase,
            background_free=background.free, background_origin=np.asarray(background.origin),
            background_resolution_m=background.resolution)
        complete = (status != CODE['incomplete']) & (attempted == completed)
        n_valid = np.rint(np.nan_to_num(r_plan)*attempted).astype(int)
        denominator = int(attempted[complete].sum())
        summary.update(_counts(status, valid_seeds, converged_seeds, executed_seeds, coverage),
            grasps=K, grid_shape_yx=[Ny, Nx], headings=M, chunks=chunk_index,
            n_attempted=int(attempted.sum()), n_completed=int(completed.sum()), n_valid=int(n_valid.sum()),
            n_retained=int(retained.sum()), incomplete_queries=int((status == CODE['incomplete']).sum()),
            r_plan=float(n_valid[complete].sum()/denominator) if denominator else None,
            elapsed_s=time.monotonic()-started, timing=timing,
            quality_range=[float(np.nanmin(quality)), float(np.nanmax(quality))] if np.isfinite(quality).any() else None)
        write_json(root / 'summary.json', summary)
        sim.close()
    return summary


@dataclass
class WarmTrajectory:
    yaw: float
    query: int
    candidate: int
    q: np.ndarray


def _neighbours(cache, cell, heading, grasp, config):
    if config.warm_start_seeds == 0:
        return []
    found = []
    for (nx, ny), by_grasp in cache.items():
        distance = config.spacing_m * math.hypot(nx-cell[0], ny-cell[1])
        if distance > config.warm_start_radius_m + 1e-9:
            continue
        for entry in by_grasp.get(grasp, []):
            turn = abs(math.atan2(math.sin(entry.yaw-heading), math.cos(entry.yaw-heading)))
            if turn <= config.warm_start_yaw_rad + 1e-9:
                found.append((distance != 0, turn, distance, entry.query, entry.candidate, entry))
    return [item[-1] for item in sorted(found, key=lambda item: item[:-1])]


def _initial_values(cache, cell, heading, lanes, K, config, bounds, lane_seeds):
    from ..planning.trajectory_quality import joint_distance
    chosen = {}
    for k in lanes:
        own = []
        for entry in _neighbours(cache, cell, heading, k, config):
            q = entry.q[-1]
            if all(joint_distance(q, old, bounds[1]-bounds[0]) > config.joint_distance_threshold for old in own):
                own.append(q)
            if len(own) >= config.warm_start_seeds:
                break
        chosen[k] = own[:config.warm_start_seeds]
    width = max(map(len, chosen.values()), default=0)
    warm = {k: len(chosen.get(k, [])) for k in range(K)}
    if not width:
        return None, warm
    seeds = np.empty((K, width, len(bounds[0])))
    for k in range(K):
        own = chosen.get(k, [])
        seeds[k] = np.random.default_rng(lane_seeds[k]).uniform(*bounds, size=(width, len(bounds[0])))
        seeds[k, :len(own)] = np.asarray(own).reshape(-1, len(bounds[0]))
    return seeds, warm


def _candidate_seeds(reps, q0, horizon, bounds, config, query, history):
    from ..planning.trajectory_quality import TrajectoryCandidate, joint_distance, trajectory_seeds
    candidates, seeds = [], []
    remaining = list(history)
    for i, q in enumerate(reps):
        index = min(range(len(remaining)), key=lambda j: (joint_distance(q, remaining[j].q[-1], bounds[1]-bounds[0]),
                                                         remaining[j].query, remaining[j].candidate)) if remaining else None
        source = remaining.pop(index) if index is not None else None
        paths = trajectory_seeds(q0, q, horizon, *bounds, np.random.default_rng(config.seed+query*4+i),
                                 config.perturbation_fraction, None if source is None else source.q)
        for variant, path in enumerate(paths):
            candidates.append(TrajectoryCandidate(2*i+variant, i, q.copy(),
                'linear' if variant == 0 else 'history' if source else 'perturbed',
                source.query if variant and source else -1, source.candidate if variant and source else -1))
            seeds.append(path)
    return candidates, np.array(seeds)


def _json_value(value):
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist())
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _candidate_summary(row):
    candidate, validation, quality = row['candidate'], row['validation'], row['quality']
    final = candidate.curve.at(candidate.curve.time[-1]) if candidate.curve is not None else None
    return dict(candidate_id=candidate.candidate_id, representative_id=candidate.representative_id,
        q_seed=candidate.q_seed, q_final=final, seed_kind=candidate.seed_kind,
        source_query=candidate.source_query, source_candidate=candidate.source_candidate,
        distance_seed_final=row.get('distance_seed_final'), fk_error=row.get('fk_error'),
        submitted=candidate.submitted, solver_success=candidate.solver_success, terminal_cluster=row.get('terminal_cluster'),
        evaluation_reused=row.get('evaluation_reused', False),
        validation=asdict(validation) if validation else {'valid': False, 'reason': 'no_trajectory'},
        quality=asdict(quality) if quality else None)


def _write_chunk(path, chunk):
    summaries, times, positions, velocities, accelerations, query_ids, candidate_ids = [], [], [], [], [], [], []
    offsets = [0]
    for query in chunk:
        summary = {key: value for key, value in query.items() if key not in ('rows', 'kept')}
        summary['candidates'] = [_candidate_summary(row) for row in query['rows']]
        retained_ids = {row['candidate'].candidate_id for row in query['kept']}
        for item in summary['candidates']:
            if item['quality'] is not None and item['candidate_id'] not in retained_ids:
                item['quality'].pop('samples')
        summary['retained_ids'] = [row['candidate'].candidate_id for row in query['kept']]
        summaries.append(json.dumps(_json_value(summary), allow_nan=False, separators=(',', ':')))
        for row in query['kept']:
            candidate = row['candidate']
            curve = candidate.curve
            times.extend(curve.time)
            positions.extend(curve.q)
            velocities.extend(curve.at(curve.time, 1))
            accelerations.extend(curve.at(curve.time, 2))
            offsets.append(len(times))
            query_ids.append(query['query'])
            candidate_ids.append(candidate.candidate_id)
    np.savez_compressed(path, summary_json=np.array(summaries), trajectory_offsets=np.array(offsets),
        trajectory_query=np.array(query_ids), trajectory_candidate=np.array(candidate_ids),
        trajectory_time=np.array(times), trajectory_q=np.array(positions).reshape(-1, 7),
        trajectory_velocity=np.array(velocities).reshape(-1, 7),
        trajectory_acceleration=np.array(accelerations).reshape(-1, 7))


def _counts(status, valid, converged, executed, coverage):
    station = status[..., 0]
    executed_total = int(executed.sum())
    return {'stations': int(station.size),
            'station_status': {name: int((station == CODE[name]).sum()) for name in ('no_heading', 'no_floor', 'base_collision')},
            'stations_all_distance_filtered': int((status == CODE['distance_filtered']).all(axis=-1).sum()),
            'stations_with_valid_grasp': int((status == CODE['valid']).any(axis=-1).sum()),
            'query_status': {name: int((status == CODE[name]).sum()) for name in STATUS},
            'executed_queries': int((executed > 0).sum()), 'executed_seeds': executed_total,
            'converged_seeds': int(converged.sum()), 'valid_seeds': int(valid.sum()),
            'ik_valid_rate': float(valid.sum()) / executed_total if executed_total else None,
            'solver_valid_rate': float(converged.sum()) / executed_total if executed_total else None,
            'incomplete_seed_coverage_queries': int(((executed > 0) & ~coverage).sum())}


def build_station_map(output_dir, run_id, config, *, recipe=None, dataset=None, assets_dir=None, samples=None,
                      gpu=None, source_config=None):
    """`evaluate-stations`: one map per scene of a base-map scene file or a scene dataset."""
    scenes = recipe_scenes(recipe) if recipe is not None else dataset_scenes(dataset, assets_dir, samples)
    root = create_run(Path(output_dir) / run_id, {
        'kind': MAP_KIND, 'recipe': str(Path(recipe).resolve()) if recipe else None,
        'dataset': str(Path(dataset).resolve()) if dataset else None, 'samples': samples,
        'assets_dir': str(Path(assets_dir).resolve()) if assets_dir else None,
        'config_path': str(Path(source_config).resolve()) if source_config else None,
        'config': asdict(config), 'gpu': gpu, 'scenes': [scene.scene_id for scene in scenes]})
    summaries = []
    for scene in scenes:
        summaries.append(build_scene_map(scene, config, root / scene.scene_id))
    write_json(root / 'summary.json', {'kind': MAP_KIND, 'scenes': summaries,
               'status': 'complete' if all(s['status'] == 'complete' for s in summaries) else 'incomplete'})
    return root


def _flush_chunk(root, chunk, index):
    if chunk:
        _write_chunk(root / f'candidates-{index:04d}.npz', chunk)
        return [], index + 1
    return chunk, index
