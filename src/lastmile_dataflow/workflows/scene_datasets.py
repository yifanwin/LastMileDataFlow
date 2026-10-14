"""Bounded discovery, physical construction and minimal public scene records.

Never imports a planner/policy implementation or performs robot task execution.
"""
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import time

import numpy as np

from ..scenes.dataset_config import SceneDatasetRecipe, load_scene_recipe, load_scene_robot
from ..config import RobotConfig
from ..scenes.obstacle_config import ObstacleConfig
from ..scenes.obstacle_regions import support_regions, station_regions, approach_points
from ..scenes.eligibility import pick_type, allowed_case1_support
from ..io import read_json, write_json, append_row, create_run, rows
from ..planning.astar import task_support_obstacles
from ..runtime.obstacle_edit_session import clone_preparation
from ..runtime.simulation import Simulation
from ..scenes.source import SceneSource
from ..scenes.dataset import select_stations, robot_descriptor
from ..scenes.tasks import discover_tasks, robot_footprint
from .obstacle_augmentation import augment_obstacles
from ..scenes.sampling import sample_stations


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


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
            version = read_json(Path(cached) / 'config.json')
            cached_robot = RobotConfig(**version['robot'])
            if version['source'] != source.to_dict() or robot_descriptor(cached_robot) != robot_descriptor(robot):
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
                    rows = sample_stations(probe, task, geometry, static_only=True, stop_after_valid=recipe.min_prefilter_points)
                    valid = sum(r['geometry'] == 'valid' for r in rows)
                    if valid < recipe.min_prefilter_points:
                        exclusions.append({'house': house, 'task_id': task['task_id'], 'reason': 'fewer_than_minimum_static_geometry_points', 'count': valid})
                        continue
                    area = sum((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]) for r in supports)
                    eligible[task['operation']].append({'house': house, 'task': task, 'prefilter_count': valid,
                        'prefilter_count_scope': 'early_stop_lower_bound_not_full_candidate_count',
                        'discovery_checked_count': len(rows), 'support_id': supports[0].support if supports else task['instance_id'],
                        'support_area_m2': float(area), 'source': source.to_dict(), 'input_snapshot': str(baseline.resolve())})
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
        'selected': selected, 'houses': houses, 'exclusions': exclusions,
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
                            stations = existing[identity]['stations'] if identity in existing else sample_stations(sim, task, recipe.geometry)
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


def build_scene_dataset(recipe_path, discovery, output_dir, dataset_id, gpu_ids, workers=1,
                        resume=False, allow_partial_targets=False):
    if resume or workers != 1:
        raise ValueError('minimal scene construction uses a fresh run and one worker')
    recipe = load_scene_recipe(recipe_path)
    plan = read_json(discovery)
    if plan['recipe'] != asdict(recipe):
        raise ValueError('discovery recipe mismatch')
    for house in recipe.houses:
        for operation, count in [('pick', recipe.max_pick_targets_per_house), ('open', recipe.max_open_targets_per_house)]:
            requested = operation == 'open' and 'Case3' in recipe.cases or operation == 'pick' and bool(set(recipe.cases) & {'Case1', 'Case2'})
            available = sum(row['house'] == house and row['task']['operation'] == operation for row in plan['selected'])
            if requested and count > 0 and available < count and not allow_partial_targets:
                raise ValueError(f'not enough {operation} targets in house {house}')
    root = create_run(Path(output_dir) / 'scene_datasets' / dataset_id,
                      {'recipe': asdict(recipe), 'robot': asdict(load_scene_robot(recipe.robot_config)),
                       'geometry': asdict(recipe.geometry), 'seed': recipe.seed})
    os.environ['MUJOCO_EGL_DEVICE_ID'] = str(gpu_ids[0])
    for unit_index, unit in enumerate(plan['selected']):
        task = unit['task']
        for case in recipe.cases:
            if (case == 'Case3') != (task['operation'] == 'open'):
                continue
            scene_id = f'val{unit["house"]}-{task["task_id"]}-{case}'
            if case == 'Case3':
                robot = load_scene_robot(recipe.robot_config)
                sim = Simulation.from_snapshot(unit['input_snapshot'], robot, target=task['target_body'])
                try:
                    points, _ = select_stations(sample_stations(sim, task, recipe.geometry),
                        task['anchor_world'], recipe.station_selection, recipe.max_final_stations)
                    if len(points) < recipe.min_final_points:
                        append_row(root / 'scenes.jsonl', {'scene_id': scene_id, 'status': 'rejected', 'reason': 'too_few_stations'})
                        continue
                    location = Path('scene-files') / scene_id
                    sim.freeze(root / location)
                    append_row(root / 'scenes.jsonl', {'scene_id': scene_id, 'status': 'accepted',
                        'scene': str(location), 'task': task, 'stations': points, 'placements': [], 'case': case})
                finally:
                    sim.close()
                continue
            case1 = case == 'Case1'
            bounds = [n + 1 for n in recipe.case1_added_surface_count] if case1 else recipe.case2_added_surface_count
            config = ObstacleConfig(baseline_config=str(recipe_path), dataset_dir=recipe.dataset_dir,
                input_snapshot=unit['input_snapshot'], asset_library=recipe.asset_library,
                target_instance=task['instance_id'], house=unit['house'], schema_version='procedural-obstacles-v3',
                variant_count=recipe.variants_per_case, seed=recipe.seed + unit_index,
                obstacle_count=bounds, mandatory_categories=['Chair'] if case1 else [],
                ground_categories=['Chair'] if case1 else [],
                surface_categories=recipe.case1_surface_categories if case1 else recipe.case2_surface_categories,
                assets_per_category=recipe.assets_per_category, max_candidates=recipe.max_candidates,
                max_physical_trials=recipe.max_physical_trials, variant_timeout_s=recipe.variant_timeout_s,
                settle=recipe.settle, views=recipe.views,
                construction={'case': case, 'rule_version': recipe.rule_version,
                    'min_prefilter_points': recipe.min_prefilter_points, 'min_final_points': recipe.min_final_points,
                    'station_selection': recipe.station_selection, 'max_final_stations': recipe.max_final_stations,
                    'min_substantial': recipe.min_substantial, 'case1_support_types': recipe.case1_support_types})
            # Write into this dataset, not a child publication/diagnostics run.
            augment_obstacles(config, output_dir, run_id=scene_id, geometry_config=recipe.geometry,
                robot_config=load_scene_robot(recipe.robot_config), dataset_root=root, id_prefix=scene_id)
    records = rows(root / 'scenes.jsonl')
    write_json(root / 'summary.json', {'status': 'completed', 'accepted': sum(r['status'] == 'accepted' for r in records),
                                     'rejected': sum(r['status'] != 'accepted' for r in records)})
    return root
