"""Bounded procedural obstacle construction. No agents or grasp-rollout claims."""
import copy
from dataclasses import asdict, replace
from pathlib import Path
import time

import mujoco
import numpy as np

from ..catalog.library import ThorLibrary
from ..config import RobotConfig
from ..construction.obstacle_sampling import environment_pool, asset_geometry, sample_placement, surface_points, case_assets, surface_occupied_bounds, sample_surface_point
from ..construction.scene_eligibility import pick_type
from ..construction.obstacle_regions import station_regions, support_regions, approach_points
from ..io import read_json, write_json, append_row, create_run, rows
from ..navigation.astar import task_support_obstacles
from ..recording.edit_views import ViewConfig
from ..recording.obstacle_views import render_obstacle_pair
from ..runtime.obstacle_edit_session import ObstacleEditSession, clone_preparation
from ..runtime.preparation import SettleConfig, settle_scene, preparation_guard
from ..runtime.simulation import Simulation
from ..scenes.graph import build_scene_graph
from ..scenes.initialization import room_triangles, floor_support
from ..scenes.source import SceneSource
from ..stations.no_edit_config import load_no_edit_config
from ..stations.no_edit_sampling import disk_samples, initialize_station, robot_contacts
from ..stations.obstacle_selection import coverage_select
from ..tasks.raw_scene import discover_tasks, robot_footprint
from ..validation.obstacle_augmentation import collision_pairs, validate_augmented_scene


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


def evaluate_stations(sim, task, config, *, frozen_stations=None, static_only=False, stop_after_valid=None):
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


def matched_control(baseline, duration_s, settle_config, deadline):
    control = clone_preparation(baseline)
    try:
        config = replace(settle_config, settle_s=duration_s, max_settle_s=duration_s)
        report = settle_scene(control, config, deadline=deadline, stability_instances=set())
        return control, report
    except BaseException:
        control.close()
        raise





def _variant(session, baseline, task, config, geometry_config, pool, geometry, regions,
             surfaces, baseline_stations, path, variant_id, seed):
    """有界放置、真实窄相/静置检查；放不下就保留负结果。"""
    path.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    deadline = time.monotonic() + config.variant_timeout_s
    policy = config.case_policy
    count = int(rng.integers(*[config.obstacle_count[0], config.obstacle_count[1] + 1]))
    result = {'scene_id': variant_id, 'seed': seed, 'status': 'rejected', 'requested_count': count}
    occupied = surface_occupied_bounds(baseline, task)
    try:
        area = sum((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2])
                   for r in {p['region'].region_id: p['region'] for p in surfaces}.values())
        assets = case_assets(rng, count, pool, geometry, policy, config.mandatory_categories, surface_area_m2=area)
    except ValueError as exc:
        return {**result, 'reason': str(exc)}
    placements = []
    physical_trials = 0
    for candidate_index in range(config.max_candidates):
        if time.monotonic() > deadline:
            return {**result, 'reason': 'variant_timeout'}
        asset = assets[len(placements)]
        ground = asset['category'] in pool['ground']
        points = regions['points'] if ground else surfaces
        if not points:
            return {**result, 'reason': 'no_placement_points'}
        if ground:
            point = points[int(rng.integers(len(points)))]
            yaw = float(rng.uniform(-np.pi, np.pi))
        else:
            point, yaw = sample_surface_point(rng, asset, surfaces, placements, occupied, policy, config.placement_gap_m)
            if point is None:
                continue
        proposal, reason = sample_placement(baseline, asset, point, yaw, config,
                                           region=None if ground else point['region'])
        if proposal is None:
            continue
        proposed = placements + [proposal]
        sim = session.candidate(proposed, deadline=deadline)
        if not collision_pairs(sim, proposed, config)['valid']:
            session.reject()
            continue
        placements = proposed
        if len(placements) < count:
            continue
        physical_trials += 1
        settling = session.settle(deadline=deadline)
        control, _ = matched_control(baseline, settling['simulated_s'], session.config, deadline)
        try:
            checks = validate_augmented_scene(sim, placements, settling, control, config)
            if not checks['valid']:
                placements = []
                session.reject()
                if physical_trials >= config.max_physical_trials:
                    return {**result, 'reason': 'physical_trial_budget_exhausted'}
                continue
            stations = evaluate_stations(sim, task, geometry_config, frozen_stations=baseline_stations)
            selected, _ = coverage_select(stations, task['anchor_world'], policy.max_final_stations if policy else 20)
            if policy and policy.station_selection == 'all_valid':
                selected = [point for point in stations if point['geometry'] == 'valid']
            if len(selected) < (policy.min_final_points if policy else 1):
                return {**result, 'reason': 'too_few_stations'}
            for i, proposal in enumerate(placements):
                body = sim.model.body(f'procedural_obstacle_{i}').id
                proposal['settled_pose'] = {'position': sim.data.xpos[body].tolist(), 'quaternion_wxyz': sim.data.xquat[body].tolist()}
            sim.freeze(path / 'scene')
            if config.record_views:
                before_graph = build_scene_graph(control, config=session.config.graph_config())
                after_graph = build_scene_graph(sim, stage='settled', config=session.config.graph_config())
                render_obstacle_pair(control, sim, before_graph, after_graph,
                    {f'procedural_obstacle_{i}' for i in range(count)}, task['target_body'],
                    {task['target_body']}, path / 'images', sample_id=variant_id,
                    config=ViewConfig(**config.views), min_pixels=geometry_config.min_target_pixels)
            return {**result, 'status': 'accepted', 'task': task, 'stations': selected,
                    'placements': placements, 'case': policy.case if policy else 'procedural',
                    'actual_count': len(placements)}
        finally:
            control.close()
    return {**result, 'reason': 'candidate_budget_exhausted'}


def augment_obstacles(config, output_dir, *, run_id, geometry_config=None, robot_config=None,
                      dataset_root=None, id_prefix=None):
    geometry_config = geometry_config or load_no_edit_config(config.baseline_config)
    root = Path(dataset_root) if dataset_root else create_run(Path(output_dir) / 'scene_datasets' / run_id,
        {'augmentation': asdict(config), 'geometry': asdict(geometry_config), 'seed': config.seed})
    source = SceneSource.procthor(config.dataset_dir, config.house)
    if config.input_snapshot:
        robot = RobotConfig(**read_json(Path(config.input_snapshot) / 'config.json')['robot'])
        baseline = Simulation.from_snapshot(config.input_snapshot, robot, target=config.target_instance)
    else:
        robot = robot_config or RobotConfig(str(Path(config.asset_library).parent.parent / 'robots/rby1m/rby1_v1.2_site_control.xml'))
        baseline = Simulation.from_source(source, robot, target=config.target_instance)
    try:
        tasks, _ = discover_tasks(baseline, read_json(source.metadata_path), geometry_config)
        task = next(t for t in tasks if t['instance_id'] == config.target_instance and t['operation'] == 'pick')
        task = {**task, 'support_obstacles': task_support_obstacles(baseline, task)}
        park_robot(baseline, task['anchor_world'], geometry_config)
        settle_config = SettleConfig(**config.settle)
        settle_scene(baseline, settle_config)
        candidates = evaluate_stations(baseline, task, geometry_config, static_only=True)
        policy = config.case_policy
        if policy and not pick_type(task):
            raise ValueError("case construction requires an eligible pick category")
        supports = support_regions(baseline, task)
        if not supports or sum(s['geometry'] == 'valid' for s in candidates) < (policy.min_prefilter_points if policy else 1):
            append_row(root / 'scenes.jsonl', {'scene_id': id_prefix or run_id, 'status': 'rejected', 'reason': 'invalid_baseline'})
            return root
        regions = station_regions(baseline, candidates, task, geometry_config, robot_footprint(baseline)['radius_m'], config.corridor_band_m)
        if policy and policy.case == 'Case1':
            approach_points(baseline, candidates, task, regions)
        pool = environment_pool(baseline, task, config)
        asset_dir = root / 'scene-files' / ((id_prefix or run_id) + '-assets')
        requests = [{'categories': [category], 'size_constraints_m': {}} for category in pool['ground'] + pool['surface']]
        assets = ThorLibrary(config.asset_library).retrieve(requests, asset_dir, limit=config.assets_per_category,
            seed=config.seed, registered_only=bool(policy and policy.registered_assets_only))
        geometry = asset_geometry(assets)
        surfaces = surface_points(supports, policy.surface_spacing_m if policy else geometry_config.spacing_m)
        if policy and policy.case == 'Case1':
            surfaces = [point for point in surfaces if np.linalg.norm(np.asarray(point['xy']) - task['anchor_world'][:2]) <= policy.near_target_m]
        seeds = np.random.SeedSequence(config.seed).spawn(config.variant_count)
        with ObstacleEditSession(baseline, assets, settle_config) as session:
            for index, seed in enumerate(seeds):
                scene_id = f'{id_prefix or run_id}-v{index:03d}'
                relative = Path('scene-files') / scene_id
                try:
                    result = _variant(session, baseline, task, config, geometry_config, pool, geometry, regions,
                        surfaces, candidates, root / relative, scene_id, int(seed.generate_state(1)[0]))
                except TimeoutError as exc:
                    result = {'scene_id': scene_id, 'status': 'rejected', 'reason': str(exc)}
                finally:
                    session.reject()
                if result['status'] == 'accepted':
                    result['scene'] = str(relative / 'scene')
                append_row(root / 'scenes.jsonl', result)
        if not dataset_root:
            records = rows(root / 'scenes.jsonl')
            write_json(root / 'summary.json', {'status': 'completed', 'accepted': sum(r['status'] == 'accepted' for r in records)})
        return root
    finally:
        baseline.close()
