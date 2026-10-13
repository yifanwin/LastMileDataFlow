"""Bounded discovery, physical construction and research scene datasets.

Never imports a planner/policy implementation or performs robot task execution.
"""
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import fcntl
import os
import shutil
import subprocess
import sys
import time

import mujoco
import numpy as np

from ..construction.scene_dataset_config import SceneDatasetRecipe, load_scene_recipe, load_scene_robot
from ..config import RobotConfig
from ..construction.obstacle_config import ObstacleConfig
from ..construction.obstacle_regions import support_regions, station_regions, approach_points
from ..construction.scene_eligibility import pick_type, allowed_case1_support
from ..io import canonical, digest, file_digest, read_json, write_json
from ..recording.protocol import code_identity
from ..navigation.astar import task_support_obstacles
from ..runtime.obstacle_edit_session import clone_preparation
from ..runtime.preparation import SettleConfig, settle_scene
from ..runtime.simulation import Simulation
from ..scenes.source import SceneSource
from ..stations.scene_package import select_stations, robot_descriptor, package_manifest
from ..tasks.raw_scene import discover_tasks, robot_footprint
from .obstacle_augmentation import augment_obstacles, evaluate_stations, park_robot


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def write_dataset_readme(root):
    root = Path(root)
    manifest = read_json(root / 'manifest.json')
    (root / 'README.md').write_text(f"""# {manifest['scene_dataset_id']}

Research scene dataset: {manifest['sample_count']} accepted samples.
Read manifest.json, recipe.json and samples.jsonl. Each sample lists its
robot_sensor_configs; configuration packages hold task, snapshot and stations.
construction.json records placements, source and construction parameters;
validation.json records the construction verdict. Exclusions remain explicit.
Only fixed snapshot identities are checked; no publication seal or physical re-audit.
Legal stations do not imply navigation or task success. Existing samples must not
be edited or overwritten. Model binaries require the recorded MuJoCo version.
""", encoding='utf-8')


def write_lines(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        for row in rows:
            stream.write(canonical(row) + b'\n')
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def spatial_select(rows, count, preferred=(), min_separation_m=0.):
    """Room/support diversity first, max-min world distance second; no prefix."""
    if not rows:
        return []
    if count == -1:
        # Unlimited means include every eligible target; diversity orders them,
        # but a proximity preference must not silently discard valid targets.
        count = len(rows)
        min_separation_m = 0.
    chosen = []
    remaining = sorted(rows, key=lambda r: r['task']['task_id'])
    for selector in preferred:
        match = next((r for r in remaining if selector in (r['task']['instance_id'], r['task']['asset_id'], r['task']['task_id'])), None)
        if match is not None and len(chosen) < count:
            chosen.append(match); remaining.remove(match)
    if not chosen:
        first = max(remaining, key=lambda r: (r['support_area_m2'], r['prefilter_count'], r['task']['task_id']))
        chosen.append(first); remaining.remove(first)
    while remaining and len(chosen) < count:
        remaining = [r for r in remaining if min(float(np.linalg.norm(np.asarray(r['task']['anchor_world'][:2]) - c['task']['anchor_world'][:2]))
                      for c in chosen) >= min_separation_m]
        if not remaining:
            break
        rooms = {r['task']['room_id'] for r in chosen}
        supports = {r['support_id'] for r in chosen}
        def score(row):
            xy = np.asarray(row['task']['anchor_world'][:2])
            distance = min(float(np.linalg.norm(xy - np.asarray(r['task']['anchor_world'][:2]))) for r in chosen)
            return (row['task']['room_id'] not in rooms, row['support_id'] not in supports,
                    distance, row['support_area_m2'], row['prefilter_count'], row['task']['task_id'])
        best = max(remaining, key=score)
        chosen.append(best); remaining.remove(best)
    return chosen


def print_scene_counts(recipe, selected, houses, stage):
    """Console forecast from selected targets, never a successful-build claim."""
    totals = {'Case1': 0, 'Case2': 0, 'Case3': 0}
    print(f'\n[{stage}] 数量汇总（预计构造数，不是已验收样本数）', flush=True)
    for house in houses:
        identity = house['house']
        picks = sum(r['house'] == identity and r['task']['operation'] == 'pick' for r in selected)
        opening = sum(r['house'] == identity and r['task']['operation'] == 'open' for r in selected)
        counts = {case: (picks * recipe.variants_per_case if case != 'Case3' else opening)
                  if case in recipe.cases else 0 for case in totals}
        print(f'val_{identity}: 原始任务={house.get("discovered_task_count", "未记录")}; '
              f'静态条件通过 pick={house.get("eligible_pick", "未记录")} open={house.get("eligible_open", "未记录")}; '
              f'本阶段选中 pick={picks} open={opening}; '
              f'预计 Case1={counts["Case1"]} Case2={counts["Case2"]} Case3={counts["Case3"]}', flush=True)
        for case in totals:
            totals[case] += counts[case]
    print(f'总计: Case1={totals["Case1"]}, Case2={totals["Case2"]}, '
          f'Case3={totals["Case3"]}, 全部={sum(totals.values())}', flush=True)
    print('发现阶段仅为静态预估；资格检查后可能减少，最终以构造及物理审计为准。', flush=True)
    return totals


def discover_scene_dataset(recipe, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    recipe = load_scene_recipe(recipe)
    robot = load_scene_robot(recipe.robot_config); geometry = recipe.geometry
    started_at = now()
    started = time.monotonic(); selected = []; exclusions = []; houses = []
    for house in recipe.houses:
        stage = time.monotonic()
        source = SceneSource.procthor(recipe.dataset_dir, house)
        print(f'val_{house}: loading original baseline', flush=True)
        cached = recipe.input_snapshots.get(str(house))
        if cached:
            version = read_json(Path(cached) / 'version.json')
            cached_robot = RobotConfig(**version['robot'])
            if version['restoration'] is not None or version['source'] != source.provenance() or digest(robot_descriptor(cached_robot)) != digest(robot_descriptor(robot)):
                raise ValueError('cached original baseline source/robot mismatch')
            sim = Simulation.from_snapshot(cached, cached_robot)
            sim.robot.config = robot  # identical physical config; canonicalize only locator/tuple representation
        else:
            sim = Simulation.from_source(source, robot)
        baseline = output.parent / (output.stem + '-baselines') / f'val_{house}' / 'scene'
        sim.freeze(baseline)
        print(f'val_{house}: baseline compiled/frozen; discovering tasks', flush=True)
        try:
            tasks, skipped = discover_tasks(sim, read_json(source.metadata_path), geometry)
            exclusions.extend({'house': house, **r} for r in skipped)
            eligible = {'pick': [], 'open': []}
            selectors = recipe.selectors(house)
            for task in tasks:
                if (task['operation'] == 'open' and 'Case3' not in recipe.cases) or (task['operation'] == 'pick' and not set(recipe.cases) & {'Case1', 'Case2'}):
                    continue
                print(f'val_{house}: probe {task["operation"]} {task["asset_id"]} {task["task_id"]}', flush=True)
                if recipe.categories and task['category'] not in recipe.categories:
                    continue
                if task['operation'] == 'pick' and recipe.pick_target_types and pick_type(task) not in recipe.pick_target_types:
                    exclusions.append({'house': house, 'task_id': task['task_id'], 'category': task['category'], 'reason': 'pick_target_not_in_assumed_high_success_whitelist'})
                    continue
                # Explicit selectors restrict pick targets; open has its own eligibility.
                if selectors and task['operation'] == 'pick' and not any(s in (task['instance_id'], task['asset_id'], task['task_id']) for s in selectors):
                    continue
                probe = clone_preparation(sim)
                probe.target_id = probe.model.body(task['target_body']).id
                try:
                    task = {**task, 'support_obstacles': task_support_obstacles(probe, task)}
                    supports = support_regions(probe, task) if task['operation'] == 'pick' else []
                    if task['operation'] == 'pick' and not supports:
                        exclusions.append({'house': house, 'task_id': task['task_id'], 'reason': 'no_qualified_target_support_plane'})
                        continue
                    if task['operation'] == 'pick' and recipe.rule_version == 'scene-cases-v3' and 'Case1' in recipe.cases and not allowed_case1_support(probe, supports[0].support, recipe.case1_support_types):
                        exclusions.append({'house': house, 'task_id': task['task_id'], 'support_id': supports[0].support, 'reason': 'case1_support_not_countertop_or_table'})
                        continue
                    rows = evaluate_stations(probe, task, geometry, static_only=True, stop_after_valid=recipe.min_prefilter_points)
                    valid = sum(r['geometry'] == 'valid' for r in rows)
                    if valid < recipe.min_prefilter_points:
                        exclusions.append({'house': house, 'task_id': task['task_id'], 'reason': 'fewer_than_minimum_static_geometry_points', 'count': valid})
                        continue
                    area = sum((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]) for r in supports)
                    eligible[task['operation']].append({'house': house, 'task': task, 'prefilter_count': valid,
                        'prefilter_count_scope': 'early_stop_lower_bound_not_full_candidate_count',
                        'discovery_checked_count': len(rows), 'support_id': supports[0].support if supports else task['instance_id'],
                        'support_area_m2': float(area), 'source': source.provenance(), 'input_snapshot': str(baseline.resolve())})
                finally:
                    probe.close()
            pick = spatial_select(eligible['pick'], recipe.max_pick_targets_per_house, min_separation_m=recipe.min_target_separation_m)
            opening = spatial_select(eligible['open'], recipe.max_open_targets_per_house)
            chosen = (pick if set(recipe.cases) & {'Case1', 'Case2'} else []) + (opening if 'Case3' in recipe.cases else [])
            selected.extend(chosen)
            chosen_ids = {r['task']['task_id'] for r in chosen}
            exclusions.extend({'house': house, 'task_id': r['task']['task_id'], 'reason': 'eligible_not_selected_by_spatial_coverage'}
                for rows in eligible.values() for r in rows if r['task']['task_id'] not in chosen_ids)
            houses.append({'house': house, 'discovered_task_count': len(tasks), 'eligible_pick': len(eligible['pick']),
                'eligible_open': len(eligible['open']), 'selected_pick': len(pick), 'selected_open': len(opening),
                'selection_method': 'room_support_diversity_then_max_min_world_distance',
                'pick_pairwise_distances_m': [float(np.linalg.norm(np.asarray(a['task']['anchor_world'][:2]) - b['task']['anchor_world'][:2]))
                    for i, a in enumerate(pick) for b in pick[i+1:]], 'elapsed_s': time.monotonic() - stage})
            print(f'val_{house}: {len(tasks)} tasks; picks {[r["task"]["asset_id"] for r in pick]}; open {[r["task"]["asset_id"] for r in opening]}', flush=True)
        finally:
            sim.close()
    print_scene_counts(recipe, selected, houses, '发现')
    write_json(output, {'schema_version': 'scene-dataset-discovery-v1', 'recipe': asdict(recipe),
        'recipe_sha256': digest(asdict(recipe)), 'selected': selected, 'houses': houses, 'exclusions': exclusions,
        'started_at': started_at, 'finished_at': now(), 'elapsed_s': time.monotonic() - started})
    return output


def _binding(robot, geometry):
    keys = ('radius_m', 'spacing_m', 'width', 'height', 'min_target_pixels', 'map_resolution_m', 'navigation_margin_m')
    return {'robot': robot_descriptor(robot), 'station_geometry': {k: getattr(geometry, k) for k in keys},
        'initialization_method': 'target_facing_actual_head_optical_axis_v1',
        'eligibility': 'physical_initialization_and_head_visibility_no_planner_or_policy'}


def qualify_scene_discovery(discovery, output):
    """Refine static discovery with complete independent visible initialization.

    Failed spatial choices are removed, never replaced with unqualified targets.
    The original static discovery and all qualification failures remain evidence.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    plan = read_json(discovery)
    recipe = SceneDatasetRecipe(**plan['recipe'])
    robot = load_scene_robot(recipe.robot_config)
    start = time.monotonic(); selected = []; qualifications = []
    existing = {r['task_id']: r for r in plan.get('qualification', [])}
    for house in recipe.houses:
        seed = next((r for r in plan['selected'] if r['house'] == house), None)
        if seed is None:
            # Static discovery found no eligible target. Keep its exclusions;
            # unlimited recipes impose no minimum quota for this house.
            continue
        source = SceneSource.procthor(recipe.dataset_dir, house)
        sim = Simulation.from_snapshot(seed['input_snapshot'], robot)
        try:
            tasks, _ = discover_tasks(sim, read_json(source.metadata_path), recipe.geometry)
            eligible_ids = {r['task']['task_id'] for r in plan['selected'] if r['house'] == house}
            eligible_ids.update(r['task_id'] for r in plan['exclusions'] if r.get('house') == house
                and r.get('reason') == 'eligible_not_selected_by_spatial_coverage')
            candidates = []
            for task in tasks:
                if task['task_id'] not in eligible_ids:
                    continue
                task = {**task, 'support_obstacles': task_support_obstacles(sim, task)}
                supports = support_regions(sim, task) if task['operation'] == 'pick' else []
                if task['operation'] == 'pick' and recipe.pick_target_types and pick_type(task) not in recipe.pick_target_types:
                    continue
                if task['operation'] == 'pick' and recipe.rule_version == 'scene-cases-v3' and 'Case1' in recipe.cases and (not supports or not allowed_case1_support(sim, supports[0].support, recipe.case1_support_types)):
                    continue
                candidates.append({**seed, 'task': task, 'prefilter_count': recipe.min_prefilter_points,
                    'support_id': supports[0].support if supports else task['instance_id'],
                    'support_area_m2': float(sum((r.bounds[1]-r.bounds[0])*(r.bounds[3]-r.bounds[2]) for r in supports))})
            cache = {}
            for operation, count in [('pick', recipe.max_pick_targets_per_house), ('open', recipe.max_open_targets_per_house)]:
                if (operation == 'pick' and not set(recipe.cases) & {'Case1', 'Case2'}) or (operation == 'open' and 'Case3' not in recipe.cases):
                    continue
                pool = [r for r in candidates if r['task']['operation'] == operation]
                while pool:
                    choices = spatial_select(pool, count, min_separation_m=recipe.min_target_separation_m if operation == 'pick' else 0.)
                    rejected = []
                    for row in choices:
                        task = row['task']; identity = task['task_id']
                        if identity not in cache:
                            sim.target_id = sim.model.body(task['target_body']).id
                            stage = time.monotonic()
                            stations = existing[identity]['stations'] if identity in existing else evaluate_stations(sim, task, recipe.geometry)
                            valid = sum(s['geometry'] == 'valid' for s in stations)
                            evidence = {'house': house, 'task_id': identity, 'asset_id': task['asset_id'],
                                'valid_count': valid, 'candidate_count': len(stations), 'elapsed_s': time.monotonic()-stage,
                                'stations': stations, 'valid': valid >= recipe.min_final_points}
                            if evidence['valid'] and operation == 'pick' and 'Case1' in recipe.cases:
                                regions = station_regions(sim, stations, task, recipe.geometry, robot_footprint(sim)['radius_m'], .12)
                                approach_points(sim, stations, task, regions)
                                points = [p for p in regions['points'] if np.linalg.norm(np.asarray(p['xy'])-task['anchor_world'][:2]) <= .85]
                                evidence['case1_near_ground_candidate_count'] = len(points)
                                if not points:
                                    evidence.update(valid=False, reason='no_measured_near_ground_points_for_mandatory_chair')
                            cache[identity] = evidence; qualifications.append(evidence)
                            print(f'val_{house} {task["asset_id"]}: {valid} visible stations', flush=True)
                        if not cache[identity]['valid']:
                            rejected.append(identity)
                        else:
                            row['baseline_visible_count'] = cache[identity]['valid_count']
                    if not rejected:
                        selected.extend(choices)
                        break
                    pool = [r for r in pool if r['task']['task_id'] not in rejected]
        finally:
            sim.close()
    plan['selected'] = selected
    plan['exclusions'].extend({k: v for k, v in r.items() if k != 'stations'} | {'reason': r.get('reason', 'fewer_than_minimum_baseline_visible_points')}
        for r in qualifications if not r['valid'])
    for h in plan['houses']:
        picks = [r for r in selected if r['house'] == h['house'] and r['task']['operation'] == 'pick']
        h['selected_pick'] = len(picks)
        h['selected_open'] = sum(r['house'] == h['house'] and r['task']['operation'] == 'open' for r in selected)
        h['pick_pairwise_distances_m'] = [float(np.linalg.norm(np.asarray(a['task']['anchor_world'][:2])-b['task']['anchor_world'][:2]))
            for i,a in enumerate(picks) for b in picks[i+1:]]
    plan.update(static_discovery=str(Path(discovery).resolve()), qualification_wall_s=time.monotonic()-start,
        qualification=qualifications, elapsed_s=plan['elapsed_s']+time.monotonic()-start, finished_at=now())
    print_scene_counts(recipe, selected, plan['houses'], '资格检查')
    write_json(output, plan)
    return output


def _save_sample(root, recipe, unit, task, snapshot, candidates, rows, checks, evidence, seed, elapsed, provenance):
    robot = load_scene_robot(recipe.robot_config); geometry = recipe.geometry
    selected, selection = select_stations(rows, task['anchor_world'], recipe.station_selection, recipe.max_final_stations)
    if len(selected) < recipe.min_final_points or not checks['valid']:
        raise ValueError('sample fails construction checks/minimum stations')
    environment = {'source': unit['source'], 'case': unit['case'], 'rule_version': recipe.rule_version,
        'target_instance': task['instance_id'], 'task_id': task['task_id'], 'seed': seed,
        'placements': read_json(evidence / 'edits.json') if unit['case'] != 'Case3' else []}
    sample_id = f'val_{unit["house"]}-{unit["case"].lower()}-{digest(environment)[:16]}'
    destination = root / 'samples' / sample_id
    if destination.exists():
        if (destination / 'sample.json').is_file():
            return read_json(destination / 'sample.json')
        import uuid
        sample_id += '-retry-' + uuid.uuid4().hex[:8]
        destination = root / 'samples' / sample_id
    binding = _binding(robot, geometry); config_id = 'rs-' + digest(binding)
    package = destination / 'robot_sensor_configs' / config_id
    package.mkdir(parents=True)
    shutil.copytree(snapshot, package / 'scene')
    write_json(destination / 'task.json', task)
    write_json(destination / 'construction.json', {**environment, 'provenance': provenance,
        'construction_config': read_json(evidence.parents[1] / 'frozen_config.json')['augmentation']
            if unit['case'] != 'Case3' else None,
        'settling': checks.get('settling'), 'elapsed_s': elapsed,
        'capacity': read_json(evidence / 'capacity.json') if (evidence / 'capacity.json').is_file() else None})
    write_json(destination / 'validation.json', checks)
    write_json(package / 'task.json', task)
    write_json(package / 'stations.json', {
        'candidates': [{k: r[k] for k in ('station_id', 'xy', 'grid_index')} for r in candidates],
        'validated': rows, 'selected_ids': [r['station_id'] for r in selected], 'selection': selection})
    write_json(package / 'initialization.json', {'parked_robot': 'exact state stored in scene/initial.npz',
        'execution': 'use each selected station initial verbatim before Simulation.begin; no post-begin resets'})
    manifest = {'schema_version': 'scene-station-package-research-v2', 'status': 'complete', 'scene_dataset_id': root.name,
        'sample_id': sample_id, 'robot_sensor_config_id': config_id, 'binding': binding, 'scene_id': unit['source']['scene_id'],
        'task_id': task['task_id'], 'case': unit['case'], 'rule_version': recipe.rule_version,
        'selection_mode': recipe.station_selection, 'max_final_stations': recipe.max_final_stations,
        'min_final_points': recipe.min_final_points, 'station_count': len(selected),
        'construction_valid': checks['valid'],
        'prefilter_count': unit['prefilter_count'], 'candidate_count': len(candidates),
        'validated_count': sum(r['geometry'] == 'valid' for r in rows), 'task_completion': 'unknown'}
    from ..runtime.snapshot_identity import snapshot_identity
    manifest['scene_identity'] = snapshot_identity(package / 'scene')
    manifest['stations_sha256'] = digest(selected)
    write_json(package / 'manifest.json', manifest)
    package_manifest(package)
    sample = {'schema_version': 'scene-sample-research-v2', 'status': 'complete', 'scene_dataset_id': root.name,
        'sample_id': sample_id, 'house': unit['house'], 'scene_id': unit['source']['scene_id'],
        'task_id': task['task_id'], 'instance_id': task['instance_id'], 'asset_id': task['asset_id'],
        'operation': task['operation'], 'case': unit['case'], 'rule_version': recipe.rule_version, 'seed': seed,
        'path': str(destination.relative_to(root)), 'robot_sensor_config_ids': [config_id],
        'added_ground_count': sum(p['kind'] == 'ground' for p in environment['placements']),
        'added_surface_count': sum(p['kind'] == 'surface' for p in environment['placements']),
        'capacity_shortfall': checks.get('case_construction', {}).get('capacity_shortfall', False),
        'surface_obstacle_bounds': recipe.case1_added_surface_count if unit['case'] == 'Case1' else recipe.case2_added_surface_count if unit['case'] == 'Case2' else [0, 0],
        'candidate_count': len(candidates), 'validated_count': manifest['validated_count'],
        'station_count': len(selected), 'construction_wall_s': elapsed, 'task_completion': 'unknown',
        'construction_unit': unit['construction_unit']}
    write_json(destination / 'sample.json', sample)
    return sample


def _open_sample(root, recipe, unit, diagnostic):
    import imageio.v2 as imageio
    from ..recording.head_views import head_frame
    from ..recording.edit_views import ViewConfig
    from ..stations.no_edit_sampling import place_frozen_station
    robot = load_scene_robot(recipe.robot_config); task = unit['task']; geometry = recipe.geometry
    sim = Simulation.from_snapshot(unit['input_snapshot'], robot, target=task['target_body'])
    started = time.monotonic()
    try:
        # Case3 preserves original object/joint state: no settling or scene edits.
        original = sim.data.qpos.copy()
        parking = park_robot(sim, np.asarray(task['anchor_world']), geometry)
        candidates = evaluate_stations(sim, task, geometry, static_only=True)
        rows = evaluate_stations(sim, task, geometry, frozen_stations=candidates)
        selected, _ = select_stations(rows, task['anchor_world'], recipe.station_selection, recipe.max_final_stations)
        if sum(r['geometry'] == 'valid' for r in candidates) < recipe.min_prefilter_points or len(selected) < recipe.min_final_points:
            raise ValueError('Case3 insufficient stations')
        mask = np.ones(sim.model.nq, bool); mask[list(sim.robot.addresses.values())] = False
        j = sim.model.joint(task['joint_name']).id
        checks = {'valid': bool(np.isfinite(sim.state_vector()).all() and np.array_equal(original[mask], sim.data.qpos[mask])
            and abs(float(sim.data.qpos[sim.model.jnt_qposadr[j]]) - task['joint_initial']) < 1e-12),
            'case_construction': {'valid': True, 'case': 'Case3', 'rule_version': recipe.rule_version, 'object_count': 0},
            'scope': 'unedited_source_state_joint_eligibility_and_station_initialization_not_new_settle',
            'no_scene_edits': True, 'no_physics_stepping': True, 'task_completion': 'unknown'}
        sim.freeze(diagnostic / 'scene')
        view = clone_preparation(sim)
        try:
            place_frozen_station(view, selected[0])
            rgb, segmentation, visibility = head_frame(view, task['target_body'], ViewConfig(width=geometry.width, height=geometry.height))
            (diagnostic / 'media').mkdir()
            imageio.imwrite(diagnostic / 'media/head.png', rgb)
            imageio.imwrite(diagnostic / 'media/target_mask.png', segmentation.astype(np.uint8) * 255)
            write_json(diagnostic / 'media/observation.json', {'station_id': selected[0]['station_id'], 'visibility': visibility,
                'not_task_execution': True, 'robot_initial': selected[0]['initial']})
        finally:
            view.close()
        write_json(diagnostic / 'parking.json', parking)
        return _save_sample(root, recipe, unit, task, diagnostic / 'scene', candidates, rows, checks,
            diagnostic, recipe.seed, time.monotonic() - started, {'source': unit['source'], 'no_edits': True, 'parking': parking})
    finally:
        sim.close()


def run_scene_dataset_unit(root, index):
    """One immutable unit, retries in distinct runs; individually commit good variants."""
    root = Path(root).resolve(); build = read_json(root / 'build.json')
    recipe = SceneDatasetRecipe(**build['recipe']); unit = {**build['units'][index], 'construction_unit': index}
    out = Path(build['output_dir'])
    base_diagnostic = out / 'diagnostics' / f'{root.name}-unit-{index:03d}'
    diagnostic = base_diagnostic / ('attempt-' + str(time.time_ns()))
    diagnostic.mkdir(parents=True, exist_ok=False)
    prior = read_json(base_diagnostic / 'result.json') if (base_diagnostic / 'result.json').is_file() else {}
    samples = list(prior.get('samples', []))
    for path in sorted((root / 'samples').glob('*/sample.json')):
        saved = read_json(path)
        if saved.get('construction_unit') == index and saved['sample_id'] not in {r['sample_id'] for r in samples}:
            samples.append(saved)
    started = time.monotonic(); exclusions = list(prior.get('exclusions', []))
    result = {'unit_index': index, 'house': unit['house'], 'case': unit['case'], 'task_id': unit['task']['task_id'],
        'started_at': now(), 'gpu_id': os.environ.get('MUJOCO_EGL_DEVICE_ID'), 'status': 'building',
        'samples': samples, 'next_retry': prior.get('next_retry', 0)}
    write_json(diagnostic / 'progress.json', result)
    try:
        if unit['case'] == 'Case3' and not samples:
            samples.append(_open_sample(root, recipe, unit, diagnostic))
        elif unit['case'] != 'Case3':
            retry_base = int(prior.get('next_retry', 0))
            for retry in range(retry_base, retry_base + 3):
                result['next_retry'] = retry + 1
                missing = recipe.variants_per_case - len(samples)
                if missing <= 0:
                    break
                case1 = unit['case'] == 'Case1'
                count = [n + 1 for n in recipe.case1_added_surface_count] if case1 else recipe.case2_added_surface_count
                config = ObstacleConfig(baseline_config=build['recipe_path'], dataset_dir=recipe.dataset_dir,
                    input_snapshot=unit['input_snapshot'],
                    asset_library=recipe.asset_library, target_instance=unit['task']['instance_id'], house=unit['house'],
                    schema_version='procedural-obstacles-v3' if recipe.rule_version == 'scene-cases-v3' else 'procedural-obstacles-v2', variant_count=missing,
                    seed=int(digest([recipe.seed, unit['house'], unit['task']['task_id'], unit['case'], retry])[:8], 16),
                    obstacle_count=count, mandatory_categories=['Chair'] if case1 else [], ground_categories=['Chair'] if case1 else [],
                    surface_categories=recipe.case1_surface_categories if case1 else recipe.case2_surface_categories,
                    assets_per_category=recipe.assets_per_category, max_candidates=recipe.max_candidates,
                    max_physical_trials=recipe.max_physical_trials, variant_timeout_s=recipe.variant_timeout_s,
                    settle=recipe.settle, views=recipe.views, construction={'case': unit['case'], 'rule_version': recipe.rule_version,
                        'min_prefilter_points': recipe.min_prefilter_points, 'min_final_points': recipe.min_final_points,
                        'station_selection': recipe.station_selection, 'max_final_stations': recipe.max_final_stations,
                        'min_substantial': recipe.min_substantial,
                        'allow_surface_capacity_shortfall': recipe.allow_surface_capacity_shortfall,
                        'capacity_probe_budget': recipe.capacity_probe_budget, 'case1_support_types': recipe.case1_support_types})
                run_id = f'{root.name}-u{index:03d}-r{retry:02d}'
                write_json(base_diagnostic / 'result.json', result)
                path = augment_obstacles(config, out, run_id=run_id, geometry_config=recipe.geometry,
                                         robot_config=load_scene_robot(recipe.robot_config))
                summary = read_json(path / 'summary.json')
                if not summary['variants']:
                    exclusions.append({'unit_index': index, 'run': str(path), 'status': summary['status'], 'reason': summary.get('reason')})
                    break
                for variant in summary['variants']:
                    evidence = path / 'variants' / variant['variant_id']
                    if variant['status'] != 'accepted':
                        exclusions.append({'unit_index': index, 'run': str(path), **variant})
                        continue
                    task = read_json(path / 'baseline/task.json')
                    rows = read_json(evidence / 'navigation/comparison.json')['stations_after']
                    samples.append(_save_sample(root, recipe, unit, task, evidence / 'scene', read_json(path / 'baseline/stations.json'),
                        rows, read_json(evidence / 'checks.json'), evidence, variant['seed'], variant['elapsed_s'],
                        {'construction_run': str(path), 'variant_id': variant['variant_id'], 'timings': variant.get('timings', {}),
                         'construction_provenance': read_json(path / 'provenance.json')}))
                result.update(samples=samples, exclusions=exclusions)
                write_json(diagnostic / 'progress.json', result)
        expected = 1 if unit['case'] == 'Case3' else recipe.variants_per_case
        result.update(status='completed' if len(samples) == expected else 'partial', expected_samples=expected)
    except Exception as exc:
        result.update(status='infrastructure_error', reason=type(exc).__name__ + ':' + str(exc))
        import traceback
        traceback.print_exc()
    finally:
        result.update(samples=samples, exclusions=exclusions, elapsed_s=time.monotonic() - started, finished_at=now())
        write_json(base_diagnostic / 'result.json', result)
    return result



def target_quota_met(recipe, rows, house):
    for operation, quota, requested in (
            ('pick', recipe.max_pick_targets_per_house, bool(set(recipe.cases) & {'Case1', 'Case2'})),
            ('open', recipe.max_open_targets_per_house, 'Case3' in recipe.cases)):
        count = sum(r['house'] == house and r['task']['operation'] == operation for r in rows)
        if requested and quota != -1 and count != quota:
            return False
    return True


def expected_scene_samples(recipe, rows):
    """Fixed quotas retain missing targets; unlimited quotas use qualified targets."""
    total = 0
    for house in recipe.houses:
        picks = sum(r['house'] == house and r['task']['operation'] == 'pick' for r in rows)
        opening = sum(r['house'] == house and r['task']['operation'] == 'open' for r in rows)
        if set(recipe.cases) & {'Case1', 'Case2'}:
            total += (picks if recipe.max_pick_targets_per_house == -1 else recipe.max_pick_targets_per_house) * recipe.variants_per_case * len(set(recipe.cases) & {'Case1', 'Case2'})
        if 'Case3' in recipe.cases:
            total += opening if recipe.max_open_targets_per_house == -1 else recipe.max_open_targets_per_house
    return total


def validate_dataset_records(root):
    root = Path(root)
    records = [json.loads(line) for line in (root / 'samples.jsonl').read_text().splitlines() if line.strip()]
    manifest = read_json(root / 'manifest.json')
    if len(records) != manifest['sample_count'] or len({s['sample_id'] for s in records}) != len(records):
        raise ValueError('sample count/identity mismatch')
    from ..recording.protocol import safe_path
    for sample in records:
        destination = safe_path(root, sample['path'])
        if read_json(destination / 'sample.json') != sample:
            raise ValueError('sample index identity mismatch')
        for config_id in sample['robot_sensor_config_ids']:
            package = package_manifest(safe_path(destination, 'robot_sensor_configs/' + config_id))
            if package['sample_id'] != sample['sample_id'] or package['scene_dataset_id'] != manifest['scene_dataset_id']:
                raise ValueError('foreign sample configuration')
    return records


def build_scene_dataset(recipe_path, discovery, output_dir, dataset_id, gpu_ids, workers=2, resume=False, allow_partial_targets=False):
    recipe_path = Path(recipe_path).resolve(); output_dir = Path(output_dir).resolve()
    recipe = load_scene_recipe(recipe_path); plan = read_json(discovery)
    if plan['recipe_sha256'] != digest(asdict(recipe)):
        raise ValueError('discovery recipe mismatch')
    for house in recipe.houses:
        if not target_quota_met(recipe, plan['selected'], house):
            if not allow_partial_targets:
                raise ValueError('discovery target quota not met for val_' + str(house))
    if not dataset_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in dataset_id):
        raise ValueError('unsafe dataset id')
    if workers <= 0 or not gpu_ids or any(type(g) is not int or not 0 <= g <= 7 for g in gpu_ids):
        raise ValueError('explicit GPU 0..7 list and positive workers required')
    root = output_dir / 'scene_datasets' / dataset_id
    if root.exists() and not resume:
        raise FileExistsError(root)
    root.mkdir(parents=True, exist_ok=resume)
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        units = [{**row, 'case': case} for row in plan['selected'] for case in recipe.cases
                 if (case == 'Case3') == (row['task']['operation'] == 'open')]
        from ..runtime.snapshot_identity import snapshot_identity
        snapshots = {path: snapshot_identity(path) for path in sorted({u['input_snapshot'] for u in units})}
        payload = {'input_snapshots': snapshots, 'recipe_path': str(recipe_path), 'recipe': asdict(recipe), 'recipe_sha256': plan['recipe_sha256'],
                   'units': units, 'output_dir': str(output_dir), 'discovery': plan, 'allow_partial_targets': allow_partial_targets}
        if resume:
            old = read_json(root / 'build.json')
            if digest(payload) != old['input_sha256']:
                raise ValueError('build resume inputs changed')
            if (root / 'manifest.json').exists() and read_json(root / 'manifest.json')['status'] == 'complete':
                validate_dataset_records(root)
                return root
        else:
            write_json(root / 'build.json', {**payload, 'input_sha256': digest(payload), 'started_at': now()})
            write_json(root / 'discovery.json', plan)
        write_json(root / 'recipe.json', asdict(recipe))
        write_json(root / 'manifest.json', {'schema_version': 'scene-datasets-research-v2',
            'scene_dataset_id': dataset_id, 'status': 'building'})
        expected = expected_scene_samples(recipe, plan['selected'])
        qualified_expected = sum(1 if u['case'] == 'Case3' else recipe.variants_per_case for u in units)
        started = time.monotonic(); completed = {}; active = {}; pending = []
        for index in range(len(units)):
            result = output_dir / 'diagnostics' / f'{dataset_id}-unit-{index:03d}' / 'result.json'
            if result.exists() and read_json(result)['status'] == 'completed':
                completed[index] = read_json(result)
                for sample in completed[index]['samples']:
                    for config_id in sample['robot_sensor_config_ids']:
                        package_manifest(root / sample['path'] / 'robot_sensor_configs' / config_id)
            else:
                pending.append(index)
        logs = output_dir / 'logs'; logs.mkdir(parents=True, exist_ok=True)
        try:
            while pending or active:
                for gpu in gpu_ids:
                    if not pending or len(active) >= min(workers, len(gpu_ids)):
                        break
                    if gpu in active:
                        continue
                    index = pending.pop(0); env = dict(os.environ, MUJOCO_EGL_DEVICE_ID=str(gpu), PYTHONUNBUFFERED='1',
                        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
                    stream = (logs / f'{dataset_id}-unit-{index:03d}.log').open('a')
                    process = subprocess.Popen([sys.executable, '-m', 'lastmile_dataflow.cli', 'scene-dataset-unit',
                        '--dataset', str(root), '--unit-index', str(index)], env=env, stdout=stream, stderr=subprocess.STDOUT)
                    active[gpu] = (process, index, stream)
                    print(f'GPU {gpu}: unit {index} val_{units[index]["house"]} {units[index]["task"]["asset_id"]} {units[index]["case"]}', flush=True)
                for gpu, (process, index, stream) in list(active.items()):
                    if process.poll() is None:
                        continue
                    stream.close(); path = output_dir / 'diagnostics' / f'{dataset_id}-unit-{index:03d}' / 'result.json'
                    completed[index] = read_json(path) if path.exists() else {'status': 'infrastructure_error', 'unit_index': index,
                        'reason': f'worker_exit_{process.returncode}', 'samples': [], 'exclusions': []}
                    print(f'unit {index}: {completed[index]["status"]} ({len(completed[index]["samples"])} samples)', flush=True)
                    del active[gpu]
                write_json(root / 'progress.json', {'status': 'building', 'expected_samples': expected,
                    'accepted_samples': sum(len(r['samples']) for r in completed.values()), 'completed_units': len(completed),
                    'pending_units': pending, 'active_units': {str(g): r[1] for g, r in active.items()}, 'wall_s': time.monotonic() - started})
                if active:
                    time.sleep(2)
        except BaseException:
            write_json(root / 'manifest.json', {'schema_version': 'scene-datasets-research-v2',
                'scene_dataset_id': dataset_id, 'status': 'failed'})
            raise
        finally:
            for process, _, stream in active.values():
                process.terminate(); process.wait(); stream.close()
        samples = [s for i in sorted(completed) for s in completed[i]['samples']]
        exclusions = list(plan['exclusions']) + [e for r in completed.values() for e in r.get('exclusions', [])]
        exclusions += [{k: v for k, v in r.items() if k not in ('samples', 'exclusions')} for r in completed.values() if r['status'] != 'completed']
        write_lines(root / 'samples.jsonl', samples)
        write_lines(root / 'exclusions.jsonl', exclusions)
        write_json(root / 'timing.json', {'wall_s': time.monotonic() - started, 'discovery_wall_s': plan['elapsed_s'],
            'unit_wall_sum_s': sum(r.get('elapsed_s', 0.) for r in completed.values()), 'units': list(completed.values()),
            'note': 'parallel wall time differs from sum of unit times; variant timings exclude shared setup'})
        complete = len(samples) == expected and all(r['status'] == 'completed' for r in completed.values())
        manifest = {'schema_version': 'scene-datasets-research-v2', 'scene_dataset_id': dataset_id,
            'status': 'complete' if complete else 'failed',
            'source_dataset': Path(recipe.dataset_dir).name, 'houses': recipe.houses, 'rule_version': recipe.rule_version,
            'sample_count': len(samples), 'expected_samples': expected, 'qualified_target_capacity': qualified_expected,
            'missing_samples': expected-len(samples), 'package_count': len(samples), 'created_at': now(),
            'task_execution': 'not_performed', 'benchmark': 'external_reference_not_embedded', 'recipe_sha256': plan['recipe_sha256'],
            'code': code_identity(), 'target_selection_scope': plan.get('target_selection_scope', 'recipe_discovery_qualification')}
        write_json(root / 'manifest.json', manifest)
        write_dataset_readme(root)
        return root
