"""Bounded procedural obstacle construction. No agents or grasp-rollout claims."""
import copy
from collections import Counter
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
import os
import re
import subprocess
import time
from zoneinfo import ZoneInfo

import mujoco
import numpy as np

from ..catalog.library import ThorLibrary
from ..config import RobotConfig
from ..construction.obstacle_sampling import environment_pool, asset_geometry, sample_placement, surface_points, case_assets, substantial_asset, surface_occupied_bounds, sample_surface_point
from ..construction.obstacle_regions import station_regions, support_regions, augmented_grid, connectivity, approach_points
from ..exporting.obstacle_augmentation import save_grid, region_plot, export_browser
from ..io import canonical, digest, file_digest, read_json, write_json
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


def evaluate_stations(sim, task, config, *, frozen_stations=None, static_only=False):
    stations = disk_samples(task['anchor_world'], config.radius_m, config.spacing_m)
    if frozen_stations is not None:
        stations = [{'station_id': s['station_id'], 'xy': s['xy'], 'grid_index': s['grid_index']} for s in frozen_stations]
    renderer = None if static_only else mujoco.Renderer(sim.model, height=config.height, width=config.width)
    rows = []
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


def _variant(session, baseline, task, config, no_edit, pool, geometry, regions, surfaces,
             baseline_stations, footprint, path, variant_id, seed, prior_layouts):
    rng = np.random.default_rng(seed)
    count = int(rng.integers(config.obstacle_count[0], config.obstacle_count[1] + 1))
    deadline = time.monotonic() + config.variant_timeout_s
    policy = config.case_policy
    result = {'variant_id': variant_id, 'seed': seed, 'requested_count': count,
              'case': policy.case if policy else 'legacy',
              'prefilter_count_before': sum(s['geometry'] == 'valid' for s in baseline_stations),
              'dense_candidate_count': len(baseline_stations),
              'requested_categories': [], 'mandatory_category_attempted': bool(config.mandatory_categories),
              'status': 'no_feasible_placement', 'physical_trials': 0,
              'valid_stations_after': None, 'final_station_count': 0}
    path.mkdir(parents=True, exist_ok=False)
    try:
        selected_assets = case_assets(rng, count, pool, geometry, policy, config.mandatory_categories)
    except ValueError as exc:
        result['reason'] = str(exc)
        write_json(path / 'result.json', result)
        return result
    categories = [a['category'] for a in selected_assets]
    result['requested_categories'] = categories
    placements = []
    last_progress = 0
    resamples = Counter()
    occupied = surface_occupied_bounds(baseline, task) if policy else None
    with (path / 'attempts.jsonl').open('xb') as log:
        def record(row):
            log.write(canonical(row) + b'\n')
            log.flush()
        for attempt in range(config.max_candidates):
            if time.monotonic() >= deadline:
                result.update(status='incomplete', reason='variant_timeout')
                break
            if result['physical_trials'] >= config.max_physical_trials:
                result['reason'] = 'physical_trial_budget_exhausted'
                break
            if policy and policy.case == 'Case2' and attempt - last_progress >= 48:
                index = len(placements)
                asset = selected_assets[index]
                require_substantial = substantial_asset(asset, policy)
                alternatives = [g for g in geometry.values() if g['category'] == asset['category']
                                and g['asset_id'] != asset['asset_id']
                                and (not require_substantial or substantial_asset(g, policy))]
                if alternatives:
                    selected_assets[index] = alternatives[int(rng.integers(len(alternatives)))]
                resamples[index] += 1
                record({'candidate': attempt, 'status': 'asset_resampled', 'reason': 'stalled_pose_sampling',
                        'category': asset['category'], 'old_asset': asset['asset_id'], 'new_asset': selected_assets[index]['asset_id']})
                if resamples[index] % 3 == 0 and placements:
                    placements.pop()
                    record({'candidate': attempt, 'status': 'packing_backtrack', 'reason': 'stalled_surface_packing',
                            'remaining_count': len(placements)})
                last_progress = attempt
            row = {'candidate': attempt, 'status': 'sampling', 'category': categories[len(placements)]}
            category = row['category']
            asset = selected_assets[len(placements)]
            ground = category in pool['ground']
            points = regions['points'] if ground else surfaces
            row.update(asset_id=asset['asset_id'], kind='ground' if ground else 'surface')
            if not points:
                row.update(status='geometry_rejected', reason='no_candidate_points')
                record(row)
                continue
            if ground and policy:
                weights = np.array([5. if any(s['kind'] in ('arithmetic_midpoint', 'station_target_approach') for s in p['sources']) else 1. for p in points])
                point = points[int(rng.choice(len(points), p=weights / weights.sum()))]
            else:
                point = points[int(rng.integers(len(points)))]
            support_center = np.mean([np.asarray(o['min'])[:2] + np.asarray(o['max'])[:2] for o in task['support_obstacles']], axis=0) / 2
            facing = np.arctan2(*(support_center - point['xy'])[::-1])
            yaw = float(facing + rng.uniform(-config.yaw_jitter_rad, config.yaw_jitter_rad))
            if not ground and policy:
                point, yaw = sample_surface_point(rng, asset, surfaces, placements, occupied, policy, config.placement_gap_m)
                if point is None:
                    row.update(status='geometry_rejected', reason='no_footprint_conditioned_surface_point')
                    record(row)
                    continue
            proposal, reason = sample_placement(baseline, asset, point, yaw, config, region=None if ground else point['region'])
            if proposal is None:
                row.update(status='geometry_rejected', reason=reason, point_id=point['point_id'])
                record(row)
                continue
            # Conservative footprint packing is only a cheap rejection stage.
            # Every remaining proposal still receives full MuJoCo narrowphase.
            if proposal['kind'] == 'surface' and any(
                    p['kind'] == 'surface' and np.all(np.asarray(proposal['world_bounds']['min'])[:2] < np.asarray(p['world_bounds']['max'])[:2] + .005)
                    and np.all(np.asarray(p['world_bounds']['min'])[:2] < np.asarray(proposal['world_bounds']['max'])[:2] + .005)
                    for p in placements):
                row.update(status='geometry_rejected', reason='conservative_surface_footprint_overlap')
                record(row)
                continue
            proposed = placements + [proposal]
            signature = digest([{k: p[k] for k in ('asset_id', 'point_id', 'yaw_rad')} for p in proposed])
            if signature in prior_layouts:
                row.update(status='geometry_rejected', reason='duplicate_layout')
                record(row)
                continue
            sim = session.candidate(proposed, deadline=deadline)
            geometry_check = collision_pairs(sim, proposed, config)
            row.update(placements=proposed, geometry_check=geometry_check)
            if not geometry_check['valid']:
                row.update(status='geometry_rejected', reason='full_height_collision')
                record(row)
                session.reject()
                continue
            if len(proposed) < count:
                placements = proposed
                last_progress = attempt + 1
                row['status'] = 'partial_geometry_pass'
                record(row)
                continue
            result['physical_trials'] += 1
            settling = session.settle(deadline=deadline)
            control, control_settling = matched_control(baseline, settling['simulated_s'], session.config, deadline)
            try:
                checks = validate_augmented_scene(sim, proposed, settling, control, config)
                row.update(status='physical_pass' if checks['valid'] else 'physical_rejected', checks=checks)
                record(row)
                if not checks['valid']:
                    placements = []
                    last_progress = attempt + 1
                    session.reject()
                    continue
                for i, proposal in enumerate(proposed):
                    body = sim.model.body(f'procedural_obstacle_{i}').id
                    proposal['settled_pose'] = {'position': sim.data.xpos[body].tolist(),
                                               'quaternion_wxyz': sim.data.xquat[body].tolist()}
                before_graph = build_scene_graph(control, stage='observed', config=session.config.graph_config())
                after_graph = build_scene_graph(sim, stage='settled', revision=1, config=session.config.graph_config())
                names = {f'procedural_obstacle_{i}' for i in range(count)}
                context = {task['target_body']} | {o['body'] for o in task['support_obstacles']}
                packet = render_obstacle_pair(control, sim, before_graph, after_graph, names,
                                 task['target_body'], context, path / 'rgb', sample_id=variant_id,
                                 config=ViewConfig(**config.views), min_pixels=no_edit.min_target_pixels,
                                 coverage_union=bool(policy and policy.case == 'Case2'))
                before_stations = evaluate_stations(control, task, no_edit, frozen_stations=baseline_stations)
                after_stations = evaluate_stations(sim, task, no_edit, frozen_stations=baseline_stations)
                final_stations, selection = coverage_select(after_stations, task['anchor_world'], policy.max_final_stations if policy else 20)
                write_json(path / 'stations_final.json', final_stations)
                write_json(path / 'station_selection.json', selection)
                before_grid = augmented_grid(control, task, no_edit, footprint, [])
                ground_names = [f'procedural_obstacle_{i}' for i, p in enumerate(proposed) if p['kind'] == 'ground']
                after_grid = augmented_grid(sim, task, no_edit, footprint, ground_names)
                comparison = {'before': connectivity(before_grid, before_stations), 'after': connectivity(after_grid, after_stations),
                              'stations_before': before_stations, 'stations_after': after_stations,
                              'changed_cells': int(np.count_nonzero(before_grid.free != after_grid.free)),
                              'note': 'conservative_grid_and_initialization_checks_not_executed_navigation'}
                save_grid(path / 'navigation/before_grid.npz', before_grid)
                save_grid(path / 'navigation/after_grid.npz', after_grid)
                region_plot(path / 'navigation/regions', task, after_stations, regions, after_grid, proposed)
                write_json(path / 'navigation/comparison.json', comparison)
                sim.freeze(path / 'scene')
                control.freeze(path / 'before_scene')
                write_json(path / 'checks.json', checks)
                write_json(path / 'edits.json', proposed)
                write_json(path / 'control_settling.json', control_settling)
                write_json(path / 'renderer.json', packet['renderer_info'])
                result.update(status='accepted', placements=proposed, candidate_index=attempt,
                              raw_rgb_count=6, physical_checks_passed=True,
                              navigation_free_cells_before=comparison['before']['free_cells'],
                              navigation_free_cells_after=comparison['after']['free_cells'],
                              valid_stations_before=sum(s['geometry'] == 'valid' for s in before_stations),
                              valid_stations_after=sum(s['geometry'] == 'valid' for s in after_stations),
                              final_station_count=len(final_stations),
                              station_failures=[{'station_id': s['station_id'], 'reason': s.get('reason', s['geometry'])} for s in after_stations if s['geometry'] != 'valid'],
                              substantial_object_count=sum(substantial_asset(a, policy) for a in selected_assets) if policy else None,
                              case_condition='construction_satisfied', task_completion='unknown')
                prior_layouts.add(signature)
                break
            finally:
                control.close()
        if result['status'] != 'accepted' and 'reason' not in result:
            result['reason'] = 'candidate_budget_exhausted'
    session.reject()
    attempts = [json.loads(line) for line in (path / 'attempts.jsonl').read_text().splitlines()]
    result['failure_reasons'] = dict(Counter(row.get('reason', 'physical_checks_failed') for row in attempts
                                           if row['status'] in ('geometry_rejected', 'physical_rejected')))
    result['asset_resamples'] = sum(row['status'] == 'asset_resampled' for row in attempts)
    result['packing_backtracks'] = sum(row['status'] == 'packing_backtrack' for row in attempts)
    write_json(path / 'result.json', result)
    return result


def augment_obstacles(config, output_dir, *, run_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('unsafe run ID')
    path = Path(output_dir).resolve() / 'diagnostics' / run_id
    path.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    no_edit = load_no_edit_config(config.baseline_config)
    settle_config = SettleConfig(**config.settle)
    source = SceneSource.procthor(config.dataset_dir, config.house)
    initial_source_provenance = source.provenance()
    if config.input_snapshot:
        version = read_json(Path(config.input_snapshot) / 'version.json')
        if version['restoration'] is not None:
            raise ValueError('augmented/restored input is not an original baseline')
        robot = RobotConfig(**version['robot'])
        baseline = Simulation.from_snapshot(config.input_snapshot, robot, target=config.target_instance)
        baseline.spec = None
        if baseline.source.provenance() != source.provenance():
            baseline.close()
            raise ValueError('cached baseline source mismatch')
    else:
        robot = RobotConfig(str(Path(config.asset_library).parent.parent / 'robots/rby1m/rby1_v1.2_site_control.xml'))
        baseline = Simulation.from_source(source, robot, target=config.target_instance)
    summary = {'schema_version': config.schema_version, 'run_id': run_id, 'status': 'incomplete',
               'variants': [], 'agent_calls': 0, 'case_condition': 'unknown', 'task_completion': 'unknown',
               'seed': config.seed, 'started_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
    try:
        write_json(path / 'frozen_config.json', {'augmentation': asdict(config), 'baseline': asdict(no_edit), 'robot': asdict(robot)})
        code = {str(p.relative_to(Path(__file__).parents[1])): file_digest(p) for p in Path(__file__).parents[1].rglob('*.py')}
        write_json(path / 'provenance.json', {'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   'source': source.provenance(), 'input_snapshot': config.input_snapshot,
                   'input_snapshot_checksums': read_json(Path(config.input_snapshot) / 'checksums.json') if config.input_snapshot else None,
                   'code_sha256': digest(code), 'source_files': code, 'mujoco': mujoco.__version__,
                   'MUJOCO_GL': os.environ.get('MUJOCO_GL'), 'MUJOCO_EGL_DEVICE_ID': os.environ.get('MUJOCO_EGL_DEVICE_ID')})
        metadata = read_json(source.metadata_path)
        tasks, _ = discover_tasks(baseline, metadata, no_edit)
        tasks = [t for t in tasks if t['instance_id'] == config.target_instance and t['operation'] == 'pick']
        if len(tasks) != 1:
            raise ValueError('target must resolve to one eligible pick task')
        task = tasks[0]
        parking = park_robot(baseline, np.asarray(task['anchor_world']), no_edit)
        initial_settle = settle_scene(baseline, settle_config, require_stability=False)
        if initial_settle['new_warnings'] or not np.isfinite(baseline.state_vector()).all():
            raise ValueError('baseline solver health failed')
        # Recompute target anchor after real original-scene settling; preserve raw task as evidence.
        write_json(path / 'baseline/raw_task.json', task)
        tasks, _ = discover_tasks(baseline, metadata, no_edit)
        task = next(t for t in tasks if t['instance_id'] == config.target_instance and t['operation'] == 'pick')
        task['support_obstacles'] = task_support_obstacles(baseline, task)
        if not task['support_obstacles']:
            raise ValueError('target support not geometrically identified')
        write_json(path / 'baseline/task.json', task)
        write_json(path / 'baseline/parking.json', parking)
        write_json(path / 'baseline/settling.json', initial_settle)
        baseline.freeze(path / 'baseline/scene')
        footprint = robot_footprint(baseline)['radius_m']
        policy = config.case_policy
        stations = evaluate_stations(baseline, task, no_edit, static_only=True)
        prescreen_count = sum(s['geometry'] == 'valid' for s in stations)
        summary.update(case=policy.case if policy else 'legacy', dense_candidate_count=len(stations),
                       prefilter_count_before=prescreen_count)
        write_json(path / 'baseline/stations.json', stations)
        if prescreen_count < (policy.min_prefilter_points if policy else 5):
            summary.update(status='skipped', reason='fewer_than_minimum_static_geometry_points', accepted_count=0)
            return path
        regions = station_regions(baseline, stations, task, no_edit, footprint, config.corridor_band_m)
        if policy and policy.case == 'Case1':
            approach_points(baseline, stations, task, regions)
        supports = support_regions(baseline, task)
        if not supports:
            summary.update(status='skipped', reason='no_qualified_target_support_plane', accepted_count=0)
            return path
        write_json(path / 'baseline/regions.json', {k: v for k, v in regions.items() if k not in ('navigation_grid', 'placement_grid', 'corridor')})
        write_json(path / 'baseline/support_regions.json', [s.to_dict() for s in supports])
        save_grid(path / 'baseline/navigation_grid.npz', regions['navigation_grid'])
        save_grid(path / 'baseline/placement_grid.npz', regions['placement_grid'])
        np.save(path / 'baseline/corridor_mask.npy', regions['corridor'])
        region_plot(path / 'baseline/regions', task, stations, regions)
        pool = environment_pool(baseline, task, config)
        write_json(path / 'obstacle_pool.json', pool)
        categories = pool['ground'] + pool['surface']
        requests = [{'categories': [c], 'size_constraints_m':
                     {'width': [.01, .65], 'depth': [.01, .65], 'height': [.005, .75]} if policy and c in pool['surface'] else {}}
                    for c in categories]
        assets = ThorLibrary(config.asset_library).retrieve(requests, path / 'assets', limit=config.assets_per_category, seed=config.seed,
                         registered_only=bool(policy and policy.registered_assets_only))
        geometry = asset_geometry(assets)
        print(f'Baseline: {sum(s["geometry"] == "valid" for s in stations)}/{len(stations)} stations; {len(regions["points"])} points; {len(geometry)} assets', flush=True)
        surfaces = surface_points(supports, policy.surface_spacing_m if policy else no_edit.spacing_m)
        if policy and policy.case == 'Case1':
            surfaces = [p for p in surfaces if np.linalg.norm(np.asarray(p['xy']) - task['anchor_world'][:2]) <= policy.near_target_m]
        prior_layouts = set()
        seeds = [int(s.generate_state(1)[0]) for s in np.random.SeedSequence(config.seed).spawn(config.variant_count)]
        with ObstacleEditSession(baseline, assets, settle_config) as session:
            for i, seed in enumerate(seeds):
                ident = f'v{i:03d}'
                print(f'{ident}: {summary["case"]}, seed={seed}', flush=True)
                try:
                    result = _variant(session, baseline, task, config, no_edit, pool, geometry, regions,
                                      surfaces, stations, footprint, path / 'variants' / ident, ident, seed, prior_layouts)
                except TimeoutError as exc:
                    session.reject()
                    result = {'variant_id': ident, 'seed': seed, 'status': 'incomplete', 'reason': str(exc)}
                except Exception as exc:
                    session.reject()
                    result = {'variant_id': ident, 'seed': seed, 'status': 'infrastructure_error',
                              'reason': type(exc).__name__ + ':' + str(exc)}
                result.update(case=summary['case'], prefilter_count_before=prescreen_count, dense_candidate_count=len(stations))
                summary['variants'].append(result)
                write_json(path / 'summary.json', summary)
                print(f'{ident}: {result["status"]} {result.get("reason", "")}', flush=True)
        summary.update(status='completed' if all(v['status'] == 'accepted' for v in summary['variants']) else 'partial',
                       accepted_count=sum(v['status'] == 'accepted' for v in summary['variants']),
                       source_unchanged=source.provenance() == initial_source_provenance,
                       asset_source_xmls_unchanged=all(file_digest(r['source_xml']) == r['source_sha256']
                           for r in read_json(path / 'assets/retrieval.json')['records'] if r['status'] == 'loadable'))
        export_browser(path, summary)
    except Exception as exc:
        summary.update(status='infrastructure_error', reason=type(exc).__name__ + ':' + str(exc))
        raise
    finally:
        baseline.close()
        summary['elapsed_s'] = time.monotonic() - start
        write_json(path / 'summary.json', summary)
    return path
