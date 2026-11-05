"""Static base-quality map: SE(2) stations x fixed grasps -> full-seed IK -> extended manipulability.

The object never moves and nothing is stepped. Each station locks base and lift;
cuRobo solves the 7 arm joints with every seed returned; MuJoCo re-checks each
converged configuration (FK, contacts) and scores it. IK validity rate is kept
apart from quality. Warm starts come only from finished rows, so results do not
depend on scoring order.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import logging
import math
import multiprocessing
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

from ..config import load_base_map_scene
from ..scenes.geometry import body_pose, descendants, tcp_pose
from ..utils.io import create_run, read_json, write_json

logger = logging.getLogger(__name__)

STATUS = ('not_evaluated', 'no_heading', 'no_floor', 'base_collision', 'distance_filtered',
          'ik_no_solution', 'valid')
SEED_REASONS = ('not_run', 'solver_failed', 'pose_mismatch', 'mujoco_contact', 'touching_contact', 'valid')
CODE = {name: i for i, name in enumerate(STATUS)}
SEED = {name: i for i, name in enumerate(SEED_REASONS)}
MAP_KIND = 'base-quality-map-v1'


def reject_quality_map(saved):
    """A quality map is not a physical calibration; S0/S1 rules from maps are not defined yet."""
    if saved.get('kind') == MAP_KIND:
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


def best_valid(reasons, quality):
    """Seed with the highest quality among valid seeds; None when no seed is valid."""
    ok = reasons == SEED['valid']
    return int(np.argmax(np.where(ok, quality, -np.inf))) if ok.any() else None


def distinct(configurations, tolerance):
    """Greedy clusters of joint configurations closer than `tolerance` (max abs, rad)."""
    centers = []
    for q in configurations:
        if all(np.max(np.abs(q - c)) > tolerance for c in centers):
            centers.append(q)
    return centers


# ---- scoring processes ------------------------------------------------------

_WORKER = {}


def _start_worker(scene, config, support_names):
    from ..planning.manipulability import ScoringModel
    from ..planning.robots.panda_omron import self_collision_links
    sim = scene.open()
    m = sim.model
    links, ignore = self_collision_links(sim.robot)
    _WORKER.update(sim=sim, config=config, initial=deepcopy(sim.robot.config.initial),
                   target=descendants(m, sim.target_id),
                   fingers=tuple(set().union(*sim.robot.finger_bodies('right'))),
                   model=ScoringModel(sim, 'right', config, link_bodies=links, ignore=ignore,
                                      support_bodies=[m.body(name).id for name in support_names]))


def _score(job):
    """MuJoCo FK/contact check and extended manipulability of every converged seed."""
    from ..scenes.stations.sampling import robot_contacts
    from ..planning.curobo.planner import pose_error
    sim, config, model = _WORKER['sim'], _WORKER['config'], _WORKER['model']
    started = time.monotonic()
    base, goals, q, converged = job['base'], job['goals'], job['q'], job['solver_success']
    rotation = base_matrix(*base, 0.)[:3, :3]
    reasons = np.full(converged.shape, SEED['solver_failed'], np.int8)
    quality = np.full(converged.shape, np.nan)
    errors = np.full((*converged.shape, 2), np.nan)
    pairs = np.zeros(converged.shape, np.int16)
    for lane, seed in zip(*np.nonzero(converged)):
        initial = deepcopy(_WORKER['initial'])
        initial.update(base_xyyaw=list(base), arm_q={'right': q[lane, seed].tolist()})
        sim.robot.initialize(initial)
        errors[lane, seed] = pose_error(tcp_pose(sim, 'right'), goals[lane])
        if errors[lane, seed, 0] > config.pose_tolerance_m or errors[lane, seed, 1] > config.pose_tolerance_rad:
            reasons[lane, seed] = SEED['pose_mismatch']
        elif robot_contacts(sim, target_bodies=_WORKER['target'], fingers=_WORKER['fingers']):
            reasons[lane, seed] = SEED['mujoco_contact']
        else:
            score = model.score(sim, rotation)
            if score is None:
                reasons[lane, seed] = SEED['touching_contact']
            else:
                reasons[lane, seed] = SEED['valid']
                quality[lane, seed] = score['quality']
                pairs[lane, seed] = score['penalized_pairs']
    return {'reasons': reasons, 'quality': quality, 'fk_error': errors, 'penalized_pairs': pairs,
            'scoring_s': time.monotonic() - started}


# ---- one scene --------------------------------------------------------------

def _planner_options(config):
    return SimpleNamespace(seed=config.seed, head_fov_enabled=False, width=320, height=240,
        head_fov_margin=.9, head_fov_weight=1e4, head_fov_min_depth_m=.01,
        num_ik_seeds=config.seeds_per_goal, num_trajopt_seeds=4, max_candidates=1, time_dilation=.5,
        environment_representation='merged_hull', operation_base_mode='fixed')


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
    timing = dict(geometry_s=0., ik_s=0., scoring_wait_s=0., scoring_cpu_s=0., io_s=0.)
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
    heading_order = sorted(range(M), key=lambda i: (abs(offsets[i]), -offsets[i]))

    shape = (Ny, Nx, M, K)
    status = np.zeros(shape, np.int8)
    quality = np.full(shape, np.nan)
    valid_seeds = np.zeros(shape, np.int16)
    converged_seeds = np.zeros(shape, np.int16)
    executed_seeds = np.zeros(shape, np.int16)
    distinct_valid = np.zeros(shape, np.int16)
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
        'planner_world_radius_m': world_radius}
    write_json(root / 'scene.json', scene_record)
    logger.info('scene=%s grasps=%s/%s grid=%sx%s headings=%s reach=%.3f', scene.scene_id, K, len(pool_rows), Nx, Ny, M, reach)

    sim.robot.initialize(initial)
    (root / 'planner').mkdir()
    planner = V2Planner(sim, _planner_options(config), 'right', root / 'planner',
                        workspace=(center, max(1., (world_radius - 1.) / 2)), batch_size=K, ik_only=True)
    if not np.allclose(planner.base, reference_base, atol=1e-6):
        raise ValueError('planning base frame differs from the analytic station frame')
    lm = planner.motion.ik_solver.seed_ik_solver
    bounds = (lm.action_min.detach().cpu().numpy(), lm.action_max.detach().cpu().numpy())
    triangles = room_triangles(sim)
    t = time.monotonic()
    from ..planning.navigation.astar import scene_grid
    extent = (float(np.max(np.abs(np.r_[xs[[0, -1]] - center[0], ys[[0, -1]] - center[1]]))) + spacing) * math.sqrt(2)
    background = scene_grid(sim, center, extent, .02, 0., 0., robot_height=1.5)
    timing['geometry_s'] += time.monotonic() - t
    ctx = multiprocessing.get_context('spawn')
    pool = ctx.Pool(workers or config.scoring_workers, initializer=_start_worker,
                    initargs=(scene, config, support))
    chunk, chunk_index, done_stations = [], 0, 0

    def flush():
        nonlocal chunk, chunk_index
        if not chunk:
            return
        t = time.monotonic()
        fields = {key: np.array([row[key] for row in chunk]) for key in chunk[0]}
        np.savez_compressed(root / f'candidates-{chunk_index:04d}.npz', **fields)
        timing['io_s'] += time.monotonic() - t
        chunk, chunk_index = [], chunk_index + 1

    try:
        for iy in range(Ny):
            row_jobs = []
            columns = range(Nx) if iy % 2 == 0 else range(Nx - 1, -1, -1)
            for ix in columns:
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
                    t = time.monotonic()
                    seeds, warm = _initial_values(warm_cache, (ix, iy), base[2], lanes, K, config, bounds,
                                                  [config.seed + station * K + k for k in range(K)])
                    padded = [*lanes, *[lanes[0]] * (K - len(lanes))]
                    planner.bind_station(world)
                    result = planner.ik_candidates([goals[k] for k in padded],
                        [config.seed + station * K + k for k in range(K)], seeds_per_goal=config.seeds_per_goal,
                        seed_config=None if seeds is None else seeds[padded])
                    timing['ik_s'] += time.monotonic() - t
                    real = slice(0, len(lanes))
                    job = {'base': base, 'goals': goals[lanes], 'q': result['q'][real],
                           'solver_success': result['solver_success'][real]}
                    row_jobs.append(((ix, mi, base, station, lanes, warm, result), pool.apply_async(_score, (job,))))
            t = time.monotonic()
            for (ix, mi, base, station, lanes, warm, result), handle in row_jobs:
                scored = handle.get()
                timing['scoring_cpu_s'] += scored['scoring_s']
                for lane, k in enumerate(lanes):
                    reasons, values = scored['reasons'][lane], scored['quality'][lane]
                    ok = reasons == SEED['valid']
                    cell = (iy, ix, mi, k)
                    executed_seeds[cell] = config.seeds_per_goal
                    converged_seeds[cell] = int(result['solver_success'][lane].sum())
                    valid_seeds[cell] = int(ok.sum())
                    warm_seeds[cell] = warm[k]
                    coverage[cell] = result['full_seed_coverage']
                    winner = best_valid(reasons, values)
                    if winner is not None:
                        status[cell], quality[cell] = CODE['valid'], values[winner]
                        best_seed[cell], best_q[cell] = winner, result['q'][lane, winner]
                        ranked = [result['q'][lane, s] for s in np.argsort(-np.where(ok, values, -np.inf)) if ok[s]]
                        clusters = distinct(ranked, config.distinct_joint_tolerance_rad)
                        distinct_valid[cell] = len(clusters)
                        warm_cache.setdefault((ix, iy), {}).setdefault(k, []).append(
                            (yaw[iy, ix, mi], clusters[:config.warm_start_seeds]))
                    else:
                        status[cell] = CODE['ik_no_solution']
                    chunk.append({'station_index': station, 'grasp_index': k, 'base_xyyaw': np.asarray(base),
                                  'q': result['q'][lane], 'solver_success': result['solver_success'][lane],
                                  'reason': reasons, 'quality': values, 'fk_error': scored['fk_error'][lane],
                                  'penalized_pairs': scored['penalized_pairs'][lane],
                                  'solver_position_error_m': result['position_error_m'][lane],
                                  'solver_rotation_error_rad': result['rotation_error_rad'][lane],
                                  'warm_start_seeds': warm[k], 'solve_s': result['solve_s'],
                                  'full_seed_coverage': result['full_seed_coverage']})
                done_stations += 1
                if done_stations % config.chunk_stations == 0:
                    flush()
            timing['scoring_wait_s'] += time.monotonic() - t
            logger.info('scene=%s row %s/%s valid queries=%s elapsed=%.0fs', scene.scene_id, iy + 1, Ny,
                        int((status == CODE['valid']).sum()), time.monotonic() - started)
        summary['status'] = 'complete'
    finally:
        flush()
        pool.close()
        pool.join()
        planner.close()
        positions = np.stack(np.meshgrid(xs, ys), axis=-1)
        best = np.where(status == CODE['valid'], quality, np.nan)
        position_quality, valid_headings = heading_mean(status, quality)
        t = time.monotonic()
        np.savez_compressed(root / 'map.npz', kind=MAP_KIND, x=xs, y=ys, xy=positions, heading_offsets_rad=offsets,
            yaw=yaw, status=status, status_names=np.array(STATUS), quality=quality, position_quality=position_quality, valid_headings=valid_headings,
            valid_seeds=valid_seeds, converged_seeds=converged_seeds, executed_seeds=executed_seeds,
            distinct_valid=distinct_valid, warm_start_seeds=warm_seeds, best_seed=best_seed, best_q=best_q,
            full_seed_coverage=coverage, seed_reason_names=np.array(SEED_REASONS),
            background_free=background.free, background_origin=np.asarray(background.origin),
            background_resolution_m=background.resolution)
        timing['io_s'] += time.monotonic() - t
        summary.update(_counts(status, valid_seeds, converged_seeds, executed_seeds, coverage),
                       grasps=K, grid_shape_yx=[Ny, Nx], headings=M, chunks=chunk_index,
                       elapsed_s=time.monotonic() - started, timing=timing,
                       quality_range=[float(np.nanmin(best)), float(np.nanmax(best))] if np.isfinite(best).any() else None)
        write_json(root / 'summary.json', summary)
        sim.close()
    return summary


def _initial_values(cache, cell, heading, lanes, K, config, bounds, lane_seeds):
    """Up to `warm_start_seeds` distinct finished neighbour solutions per lane, inside the budget.

    Lanes with fewer neighbours are padded with uniform samples in the solver's
    own joint bounds; they count as default initial values, not warm starts.
    """
    ix, iy = cell
    chosen = {}
    for k in lanes:
        found = []
        for (nx, ny), by_grasp in cache.items():
            distance = config.spacing_m * math.hypot(nx - ix, ny - iy)
            if distance > config.warm_start_radius_m + 1e-9:
                continue
            for other_yaw, clusters in by_grasp.get(k, ()):
                turn = abs(math.atan2(math.sin(other_yaw - heading), math.cos(other_yaw - heading)))
                if turn <= config.warm_start_yaw_rad + 1e-9:
                    found.extend((distance, turn, rank, q) for rank, q in enumerate(clusters))
        found.sort(key=lambda item: item[:3])
        chosen[k] = distinct([q for *_, q in found], config.distinct_joint_tolerance_rad)[:config.warm_start_seeds]
    width = max(len(v) for v in chosen.values())
    warm = {k: len(chosen.get(k, ())) for k in range(K)}
    if not width:
        return None, warm
    seeds = np.empty((K, width, len(bounds[0])))
    for k in range(K):
        rng = np.random.default_rng(lane_seeds[k])
        own = chosen.get(k, [])
        if own:
            seeds[k, :len(own)] = np.array(own)
        seeds[k, len(own):] = rng.uniform(*bounds, size=(width - len(own), len(bounds[0])))
    return seeds, warm


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
