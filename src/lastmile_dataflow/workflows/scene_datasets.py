"""Bounded discovery, physical construction and sealed public scene datasets.

Never imports a planner/policy implementation or performs robot task execution.
"""
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
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
    """Document each delivered dataset directory without changing scene payloads."""
    root = Path(root)
    manifest = read_json(root / 'manifest.json')
    (root / 'README.md').write_text(f'''# {manifest['scene_dataset_id']}

本目录是 scene_datasets，包含 {manifest['sample_count']} 个场景样本。
场景快照与站位绑定机器人、传感器和物理控制配置，与具体规划器或策略无关。
不包含 cuRobo / MolmoBot 执行数据，不代表导航或抓取成功。

## 目录结构

```text
./
├── manifest.json       数据集身份、规则版本、样本数及封存状态
├── samples.jsonl       样本索引：目标、case、数量、站位数及相对路径
├── packages.jsonl      机器人/传感器配置包索引
├── checksums.json      已登记文件的 SHA256 校验和
├── build.json          冻结构建配方与构建单元
├── discovery.json      场景/目标筛选及预检查记录
├── exclusions.jsonl    排除与失败记录
├── timing.json         发现、单元及并行构造时间（详见计时范围）
├── progress.json       构建期历史检查点；最终状态以 manifest 为准
└── samples/<sample_id>/
    ├── manifest.json / checksums.json  样本身份与校验和
    ├── task.json / objects.json / environment.json  任务、实例与环境
    ├── construction/   编辑记录、来源、时间及冻结对照场景
    ├── validation/     物理与 case 检查
    ├── media/          构造前后真实 RGB 与配对信息
    └── robot_sensor_configs/<config_id>/
        ├── manifest.json / checksums.json  配置绑定与校验和
        ├── scene/      model.mjb、initial.npz、版本与实例描述
        ├── stations/   候选、合格、选定站位及选择记录
        ├── validation/ 该配置下的检查结果
        └── task.json / initialization.json  任务与初始化描述
```

## 读取顺序与边界

先读取 manifest，再按 samples.jsonl / packages.jsonl 的相对路径读取样本与配置包。
障碍物数量为新增数量，不含目标与原有物品；站位合格仅表示物理合法及目标可见。
下游执行 datasets 和 benchmarks 应引用 dataset/sample/config 三层 ID，不能修改冻结输入或静默重采样。
MJB 受 MuJoCo 版本兼容约束；重新构造仍需配方所引用的外部资产。
时间字段有不同计时范围，不能把并行墙钟与单元耗时相加混用。
''', encoding='utf-8')


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


def _seal_sample(root, recipe, unit, task, snapshot, candidates, rows, checks, evidence, seed, elapsed, provenance):
    dataset_id = read_json(root/'build.json').get('scene_dataset_id', root.name) if (root/'build.json').exists() else root.name
    robot = load_scene_robot(recipe.robot_config); geometry = recipe.geometry
    selected, selection = select_stations(rows, task['anchor_world'], recipe.station_selection, recipe.max_final_stations)
    if len(selected) < recipe.min_final_points or not checks['valid']:
        raise ValueError('sample fails publication checks/minimum stations')
    environment = {'source': unit['source'], 'case': unit['case'], 'rule_version': recipe.rule_version,
        'target_instance': task['instance_id'], 'task_id': task['task_id'], 'seed': seed,
        'placements': read_json(evidence / 'edits.json') if unit['case'] != 'Case3' else []}
    sample_id = f'val_{unit["house"]}-{unit["case"].lower()}-{digest(environment)[:16]}'
    destination = root / 'samples' / sample_id
    if destination.exists():
        raise FileExistsError(destination)
    binding = _binding(robot, geometry); config_id = 'rs-' + digest(binding)
    package = destination / 'robot_sensor_configs' / config_id
    package.mkdir(parents=True)
    shutil.copytree(snapshot, package / 'scene')
    write_json(destination / 'environment.json', environment)
    write_json(destination / 'task.json', task)
    write_json(destination / 'objects.json', read_json(snapshot / 'instances.json'))
    write_json(destination / 'construction/provenance.json', provenance)
    write_json(destination / 'construction/timing.json', {'sample_wall_s': elapsed, 'scope': 'variant_not_shared_baseline_or_asset_retrieval',
        'timings': provenance.get('timings', {})})
    write_json(destination / 'validation/checks.json', checks)
    if unit['case'] != 'Case3':
        shutil.copy2(evidence / 'edits.json', destination / 'construction/edits.json')
        shutil.copy2(evidence / 'control_settling.json', destination / 'construction/control_settling.json')
        if (evidence / 'capacity.json').exists():
            shutil.copy2(evidence / 'capacity.json', destination / 'construction/capacity.json')
        shutil.copytree(evidence / 'before_scene', destination / 'construction/control_scene')
        shutil.copytree(evidence / 'rgb', destination / 'media')
        packet = read_json(destination / 'media/pair.json')
        for record in packet['images']:
            record['source_path'] = record['path']; record['path'] = Path(record['path']).name
        write_json(destination / 'media/pair.json', packet)
    else:
        shutil.copytree(evidence / 'media', destination / 'media')
    write_json(package / 'task.json', task)
    write_json(package / 'stations/candidates.json', [{k: r[k] for k in ('station_id', 'xy', 'grid_index')} for r in candidates])
    write_json(package / 'stations/validated.json', rows)
    write_json(package / 'stations/selected.json', selected)
    write_json(package / 'stations/selection.json', selection)
    write_json(package / 'validation/checks.json', checks)
    write_json(package / 'initialization.json', {'parked_robot': 'exact state stored in scene/initial.npz',
        'execution': 'use each selected station initial verbatim before Simulation.begin; no post-begin resets'})
    manifest = {'schema_version': 'scene-station-package-v1', 'status': 'sealed', 'scene_dataset_id': dataset_id,
        'sample_id': sample_id, 'robot_sensor_config_id': config_id, 'binding': binding, 'scene_id': unit['source']['scene_id'],
        'task_id': task['task_id'], 'case': unit['case'], 'rule_version': recipe.rule_version,
        'selection_mode': recipe.station_selection, 'max_final_stations': recipe.max_final_stations,
        'min_final_points': recipe.min_final_points, 'station_count': len(selected),
        'prefilter_count': unit['prefilter_count'], 'candidate_count': len(candidates),
        'validated_count': sum(r['geometry'] == 'valid' for r in rows), 'task_completion': 'unknown'}
    write_json(package / 'manifest.json', manifest)
    write_json(package / 'checksums.json', {str(p.relative_to(package)): file_digest(p) for p in package.rglob('*') if p.is_file()})
    package_manifest(package)
    sample = {'schema_version': 'scene-sample-v1', 'status': 'sealed', 'scene_dataset_id': dataset_id,
        'sample_id': sample_id, 'house': unit['house'], 'scene_id': unit['source']['scene_id'],
        'task_id': task['task_id'], 'instance_id': task['instance_id'], 'asset_id': task['asset_id'],
        'operation': task['operation'], 'case': unit['case'], 'rule_version': recipe.rule_version, 'seed': seed,
        'path': str(destination.relative_to(root)), 'robot_sensor_config_ids': [config_id],
        'added_ground_count': sum(p['kind'] == 'ground' for p in environment['placements']),
        'added_surface_count': sum(p['kind'] == 'surface' for p in environment['placements']),
        'capacity_shortfall': checks.get('case_construction', {}).get('capacity_shortfall', False),
        'surface_obstacle_bounds': recipe.case1_added_surface_count if unit['case'] == 'Case1' else recipe.case2_added_surface_count if unit['case'] == 'Case2' else [0, 0],
        'candidate_count': len(candidates), 'validated_count': manifest['validated_count'],
        'station_count': len(selected), 'construction_wall_s': elapsed, 'task_completion': 'unknown'}
    write_json(destination / 'manifest.json', sample)
    write_json(destination / 'checksums.json', {str(p.relative_to(destination)): file_digest(p) for p in destination.rglob('*') if p.is_file() and p != destination / 'checksums.json'})
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
        return _seal_sample(root, recipe, unit, task, diagnostic / 'scene', candidates, rows, checks,
            diagnostic, recipe.seed, time.monotonic() - started, {'source': unit['source'], 'no_edits': True, 'parking': parking})
    finally:
        sim.close()


def run_scene_dataset_unit(root, index):
    """One immutable unit, retries in distinct runs; individually publish good variants."""
    root = Path(root).resolve(); build = read_json(root / 'build.json')
    recipe = load_scene_recipe(build['recipe_path']); unit = build['units'][index]
    dataset_id = build.get('scene_dataset_id', root.name)
    out = Path(build['output_dir']); diagnostic = out / 'diagnostics' / f'{dataset_id}-unit-{index:03d}'
    diagnostic.mkdir(parents=True, exist_ok=False)
    started = time.monotonic(); samples = []; exclusions = []
    result = {'unit_index': index, 'house': unit['house'], 'case': unit['case'], 'task_id': unit['task']['task_id'],
        'started_at': now(), 'gpu_id': os.environ.get('MUJOCO_EGL_DEVICE_ID'), 'status': 'running', 'samples': []}
    write_json(diagnostic / 'progress.json', result)
    try:
        if unit['case'] == 'Case3':
            samples.append(_open_sample(root, recipe, unit, diagnostic))
        else:
            for retry in range(3):
                missing = recipe.variants_per_case - len(samples)
                if missing <= 0:
                    break
                case1 = unit['case'] == 'Case1'
                count = [n + 1 for n in recipe.case1_added_surface_count] if case1 else recipe.case2_added_surface_count
                config = ObstacleConfig(baseline_config=build['recipe_path'], dataset_dir=recipe.dataset_dir,
                    input_snapshot=unit['input_snapshot'],
                    fixed_chair=recipe.fixed_chair,
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
                run_id = f'{dataset_id}-u{index:03d}-r{retry:02d}'
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
                    samples.append(_seal_sample(root, recipe, unit, task, evidence / 'scene', read_json(path / 'baseline/stations.json'),
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
        write_json(diagnostic / 'result.json', result)
    return result


def audit_scene_dataset(root, physical=False, audit_inputs=None):
    """Checksum/identity/selection audit, optionally reload physical frozen scenes."""
    root = Path(root).resolve(); manifest = read_json(root / 'manifest.json'); errors = []; records = []
    portable_inputs = None
    if audit_inputs is not None:
        audit_inputs = Path(audit_inputs).resolve()
        portable_inputs = read_json(audit_inputs / 'manifest.json')
        if (portable_inputs.get('schema_version') != 'scene-audit-inputs-v1'
                or portable_inputs.get('scene_dataset_id') != manifest['scene_dataset_id']
                or portable_inputs.get('dataset_manifest_sha256') != file_digest(root / 'manifest.json')):
            raise ValueError('portable audit inputs dataset identity mismatch')
    expected = read_json(root / 'checksums.json')
    for name, checksum in expected.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_digest(path) != checksum:
            errors.append('dataset checksum mismatch: ' + name)
    samples = [json.loads(line) for line in (root / 'samples.jsonl').read_text().splitlines()]
    for sample in samples:
        try:
            destination = root / sample['path']
            if read_json(destination / 'manifest.json') != sample:
                raise ValueError('sample index mismatch')
            for name, checksum in read_json(destination / 'checksums.json').items():
                target = (destination / name).resolve()
                if not target.is_relative_to(destination) or file_digest(target) != checksum:
                    raise ValueError('sample checksum mismatch: ' + name)
            packages = list((destination / 'robot_sensor_configs').iterdir())
            for package in packages:
                pm = package_manifest(package)
                if pm['sample_id'] != sample['sample_id']:
                    raise ValueError('package sample identity mismatch')
                if physical:
                    robot = RobotConfig(**read_json(package / 'scene/version.json')['robot'])
                    task = read_json(package / 'task.json')
                    sim = Simulation.from_snapshot(package / 'scene', robot, target=task['target_body'])
                    try:
                        if not np.isfinite(sim.state_vector()).all():
                            raise ValueError('nonfinite physical snapshot')
                        if sample['case'] != 'Case3':
                            from ..validation.obstacle_augmentation import validate_augmented_scene
                            p = read_json(destination / 'construction/provenance.json')
                            if portable_inputs is None:
                                config_values = read_json(Path(p['construction_run']) / 'frozen_config.json')['augmentation']
                            else:
                                record = portable_inputs['configs'][sample['sample_id']]
                                location = (audit_inputs / record['file']).resolve()
                                if not location.is_relative_to(audit_inputs) or file_digest(location) != record['sha256']:
                                    raise ValueError('portable augmentation config checksum/path mismatch')
                                config_values = read_json(location)
                            config = ObstacleConfig(**config_values)
                            control = Simulation.from_snapshot(destination / 'construction/control_scene', robot, target=task['target_body'])
                            try:
                                stored = read_json(package / 'validation/checks.json')
                                placements = read_json(destination / 'construction/edits.json')
                                capacity = None
                                if (destination / 'construction/capacity.json').exists():
                                    from ..catalog.edit_assets import EditAssetCatalog
                                    from ..construction.obstacle_sampling import asset_geometry, surface_points
                                    from ..construction.surface_capacity import valid_certificate
                                    from ..runtime.obstacle_edit_session import ObstacleEditSession
                                    from .obstacle_augmentation import _capacity_probe
                                    capacity = read_json(destination / 'construction/capacity.json')
                                    if not valid_certificate(sim, placements, capacity):
                                        raise ValueError('capacity certificate state/domain mismatch')
                                    run = Path(p['construction_run'])
                                    baseline = Simulation.from_snapshot(run / 'baseline/scene', robot, target=task['target_body'])
                                    try:
                                        assets = EditAssetCatalog(run / 'assets/catalog.json')
                                        geometry = asset_geometry(assets)
                                        footprint_filter = read_json(run / 'assets/footprint_filter.json')
                                        excluded = set(footprint_filter['excluded']) if footprint_filter.get('applied', True) else set()
                                        geometry = {k:g for k,g in geometry.items() if k not in excluded}
                                        points = surface_points(support_regions(baseline, task), config.case_policy.surface_spacing_m)
                                        if config.case_policy.case == 'Case1':
                                            points = [q for q in points if np.linalg.norm(np.asarray(q['xy'])-task['anchor_world'][:2]) <= config.case_policy.near_target_m]
                                        with ObstacleEditSession(baseline, assets, SettleConfig(**config.settle)) as probe:
                                            replay, _, _ = _capacity_probe(probe, sim, placements, geometry, points, config, None)
                                        if not valid_certificate(sim, placements, replay) or replay['domain_sha256'] != capacity['domain_sha256']:
                                            raise ValueError('capacity exhaustive replay failed')
                                    finally:
                                        baseline.close()
                                checks = validate_augmented_scene(sim, placements, stored['settling'], control, config, capacity_evidence=capacity)
                                if not checks['valid']:
                                    raise ValueError('physical collision/support/case/source-effect audit failed')
                            finally:
                                control.close()
                        else:
                            j = sim.model.joint(task['joint_name']).id
                            if abs(float(sim.data.qpos[sim.model.jnt_qposadr[j]]) - task['joint_initial']) > 1e-12:
                                raise ValueError('Case3 articulation changed')
                    finally:
                        sim.close()
            if sample['case'] != 'Case3':
                import imageio.v2 as imageio
                from ..recording.obstacle_views import visibility_pass
                packet = read_json(destination / 'media/pair.json')
                edits = read_json(destination / 'construction/edits.json')
                names = [f'procedural_obstacle_{i}' for i in range(len(edits))]
                threshold = read_json(packages[0] / 'manifest.json')['binding']['station_geometry']['min_target_pixels']
                if not visibility_pass(packet['visible_pixels_by_instance'], names, sample['instance_id'], threshold, True):
                    raise ValueError('paired media segmentation coverage failed')
                for view in ('top', 'oblique_a', 'oblique_b'):
                    before = imageio.imread(destination / f'media/before_{view}.png')
                    after = imageio.imread(destination / f'media/after_{view}.png')
                    if before.shape != after.shape or not np.any(before != after) or float(before.std()) <= 1 or float(after.std()) <= 1:
                        raise ValueError('paired RGB empty/unchanged')
                if not all(r['pair_id'] == packet['pair_id'] and (destination / 'media' / r['path']).is_file() for r in packet['images']):
                    raise ValueError('paired image identity/path mismatch')
            records.append({'sample_id': sample['sample_id'], 'valid': True})
        except Exception as exc:
            records.append({'sample_id': sample['sample_id'], 'valid': False, 'reason': str(exc)})
    if len(samples) != manifest['sample_count'] or len({s['sample_id'] for s in samples}) != len(samples):
        errors.append('sample count/identity mismatch')
    return {'schema_version': 'scene-dataset-audit-v1', 'valid': not errors and all(r['valid'] for r in records),
        'dataset': str(root), 'scope': 'physical_frozen_scene_and_checksums' if physical else 'checksums_identity_station_selection',
        'errors': errors, 'samples': records, 'sample_count': len(samples), 'task_execution': 'not_performed'}


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


def build_scene_dataset(recipe_path, discovery, output_dir, dataset_id, gpu_ids, workers=2, resume=False, allow_partial_targets=False, dataset_root=None):
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
    root = Path(dataset_root).resolve() if dataset_root else output_dir / 'scene_datasets' / dataset_id
    if root.exists() and not resume:
        raise FileExistsError(root)
    root.mkdir(parents=True, exist_ok=resume)
    units = [{**row, 'case': case} for row in plan['selected'] for case in recipe.cases
             if (case == 'Case3') == (row['task']['operation'] == 'open')]
    payload = {'recipe_path': str(recipe_path), 'recipe': asdict(recipe), 'recipe_sha256': plan['recipe_sha256'],
               'units': units, 'output_dir': str(output_dir), 'discovery': plan, 'allow_partial_targets': allow_partial_targets}
    if dataset_root:
        payload['scene_dataset_id'] = dataset_id
    if resume:
        old = read_json(root / 'build.json')
        if digest(payload) != old['input_sha256']:
            raise ValueError('build resume inputs changed')
        if (root / 'manifest.json').exists() and read_json(root / 'manifest.json')['status'] == 'sealed':
            if not audit_scene_dataset(root)['valid']:
                raise ValueError('sealed dataset audit failed')
            return root
    else:
        write_json(root / 'build.json', {**payload, 'input_sha256': digest(payload), 'started_at': now()})
        write_json(root / 'discovery.json', plan)
    expected = expected_scene_samples(recipe, plan['selected'])
    qualified_expected = sum(1 if u['case'] == 'Case3' else recipe.variants_per_case for u in units)
    started = time.monotonic(); completed = {}; active = {}; pending = []
    for index in range(len(units)):
        result = output_dir / 'diagnostics' / f'{dataset_id}-unit-{index:03d}' / 'result.json'
        if result.exists():
            completed[index] = read_json(result)
        else:
            diagnostic = result.parent
            if diagnostic.exists():
                raise ValueError('interrupted unit requires a new dataset id; original evidence retained')
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
            write_json(root / 'progress.json', {'status': 'running', 'expected_samples': expected,
                'sealed_samples': sum(len(r['samples']) for r in completed.values()), 'completed_units': len(completed),
                'pending_units': pending, 'active_units': {str(g): r[1] for g, r in active.items()}, 'wall_s': time.monotonic() - started})
            if active:
                time.sleep(2)
    finally:
        for process, _, stream in active.values():
            process.terminate(); process.wait(); stream.close()
    samples = [s for i in sorted(completed) for s in completed[i]['samples']]
    exclusions = list(plan['exclusions']) + [e for r in completed.values() for e in r.get('exclusions', [])]
    exclusions += [{k: v for k, v in r.items() if k not in ('samples', 'exclusions')} for r in completed.values() if r['status'] != 'completed']
    write_lines(root / 'samples.jsonl', samples)
    write_lines(root / 'packages.jsonl', [{'sample_id': s['sample_id'], 'robot_sensor_config_id': c,
        'path': f'{s["path"]}/robot_sensor_configs/{c}'} for s in samples for c in s['robot_sensor_config_ids']])
    write_lines(root / 'exclusions.jsonl', exclusions)
    write_json(root / 'timing.json', {'wall_s': time.monotonic() - started, 'discovery_wall_s': plan['elapsed_s'],
        'unit_wall_sum_s': sum(r.get('elapsed_s', 0.) for r in completed.values()), 'units': list(completed.values()),
        'note': 'parallel wall time differs from sum of unit times; variant timings exclude shared setup'})
    complete = len(samples) == expected and all(r['status'] == 'completed' for r in completed.values())
    manifest = {'schema_version': 'scene-datasets-v1', 'scene_dataset_id': dataset_id,
        'status': 'awaiting_audit' if complete else 'partial',
        'source_dataset': Path(recipe.dataset_dir).name, 'houses': recipe.houses, 'rule_version': recipe.rule_version,
        'sample_count': len(samples), 'expected_samples': expected, 'qualified_target_capacity': qualified_expected,
        'missing_samples': expected-len(samples), 'package_count': len(samples), 'created_at': now(),
        'task_execution': 'not_performed', 'benchmark': 'external_reference_not_embedded', 'recipe_sha256': plan['recipe_sha256'],
        'target_selection_scope': plan.get('target_selection_scope', 'recipe_discovery_qualification')}
    write_json(root / 'manifest.json', manifest)
    write_dataset_readme(root)
    write_json(root / 'checksums.json', {str(p.relative_to(root)): file_digest(p) for p in root.rglob('*') if p.is_file() and p != root / 'checksums.json'})
    report = audit_scene_dataset(root, physical=True)
    write_json(output_dir / 'diagnostics' / f'{dataset_id}-audit.json', report)
    if not report['valid']:
        manifest['status'] = 'audit_failed'
        write_json(root / 'manifest.json', manifest)
        checksums = read_json(root / 'checksums.json'); checksums['manifest.json'] = file_digest(root / 'manifest.json')
        write_json(root / 'checksums.json', checksums)
        raise ValueError('final scene dataset audit failed')
    manifest['status'] = 'sealed' if complete else 'partial'
    manifest['physical_audit_sha256'] = file_digest(output_dir / 'diagnostics' / f'{dataset_id}-audit.json')
    write_json(root / 'manifest.json', manifest)
    checksums = read_json(root / 'checksums.json'); checksums['manifest.json'] = file_digest(root / 'manifest.json')
    write_json(root / 'checksums.json', checksums)
    return root
