"""构建场景库：静态发现 → 可见站位资格检查 → 场景保存或障碍物增强。

本模块只编排构建流程，不加载规划器或策略，也不执行机器人任务。
发现阶段用少量静态站位快速筛选；资格阶段检查完整候选站位，不能把两者混为实测成功率。
"""

import os
import tempfile
import time
from contextlib import closing
from dataclasses import asdict
from itertools import combinations
from pathlib import Path

import numpy as np

from ..config import RobotConfig, read_config
from ..io import append_row, create_run, read_json, rows, write_json
from ..planning.astar import task_support_obstacles
from ..runtime.construction.session import clone_preparation
from ..runtime.simulation import Simulation
from ..scenes.augmentation.config import ObstacleConfig
from ..scenes.augmentation.regions import approach_points, station_regions, support_regions
from ..scenes.dataset import select_stations
from ..scenes.dataset_config import SceneDatasetRecipe, load_scene_recipe, load_scene_robot
from ..scenes.loading.source import SceneSource
from ..scenes.stations.sampling import sample_stations
from ..scenes.tasks.discovery import discover_tasks, robot_footprint
from ..scenes.tasks.eligibility import allowed_case1_support, pick_type
from .obstacle_augmentation import augment_obstacles


def _target_limits(recipe):
    """只列出本配方启用的任务类型，统一三个阶段的 Case 分流。"""
    limits = {}
    if {"Case1", "Case2"} & set(recipe.cases):
        limits["pick"] = recipe.max_pick_targets_per_house
    if "Case3" in recipe.cases:
        limits["open"] = recipe.max_open_targets_per_house
    return limits


def _target_distance(left, right):
    """按世界坐标 XY 距离选目标，不把支撑面的高度差算作空间分散。"""
    return float(
        np.linalg.norm(np.asarray(left["task"]["anchor_world"][:2]) - right["task"]["anchor_world"][:2])
    )


def _selection_summary(selected):
    """发现和资格检查共用同一组选择统计。"""
    picks = [row for row in selected if row["task"]["operation"] == "pick"]
    return {
        "selected_pick": len(picks),
        "selected_open": len(selected) - len(picks),
        "pick_pairwise_distances_m": [
            _target_distance(a, b) for a, b in combinations(picks, 2)
        ],
    }


def spatial_select(rows, count, preferred=(), min_separation_m=0.0):
    """优先覆盖不同房间和支撑面，再让目标间的最小距离尽量大。

    ID 排序和评分末项使并列结果可复现；显式偏好先入选，不受后续距离过滤影响。
    count=-1 表示全选，此时距离只影响顺序，不能据此丢弃静态合格目标。
    """
    if not rows:
        return []
    if count == -1:
        count = len(rows)
        min_separation_m = 0.0
    chosen = []
    remaining = sorted(rows, key=lambda row: row["task"]["task_id"])
    for selector in preferred:
        match = next(
            (
                row
                for row in remaining
                if selector in (row["task"]["instance_id"], row["task"]["asset_id"], row["task"]["task_id"])
            ),
            None,
        )
        if match is not None and len(chosen) < count:
            chosen.append(match)
            remaining.remove(match)
    if not chosen:
        first = max(
            remaining,
            key=lambda row: (row["support_area_m2"], row["prefilter_count"], row["task"]["task_id"]),
        )
        chosen.append(first)
        remaining.remove(first)
    while remaining and len(chosen) < count:
        # 同一轮的最近距离只算一次，同时用于间距过滤和最远点评分。
        distances = [(row, min(_target_distance(row, selected) for selected in chosen)) for row in remaining]
        candidates = [(row, distance) for row, distance in distances if distance >= min_separation_m]
        if not candidates:
            break
        rooms = {row["task"]["room_id"] for row in chosen}
        supports = {row["support_id"] for row in chosen}

        def score(candidate):
            row, distance = candidate
            return (
                row["task"]["room_id"] not in rooms,
                row["support_id"] not in supports,
                distance,
                row["support_area_m2"],
                row["prefilter_count"],
                row["task"]["task_id"],
            )

        best, _ = max(candidates, key=score)
        chosen.append(best)
        remaining = [row for row, _ in candidates if row is not best]
    return chosen


def print_scene_counts(recipe, houses, stage):
    """打印按目标与变体数推算的数量，不把预估数当作已验收场景数。"""
    totals = {"Case1": 0, "Case2": 0, "Case3": 0}
    print(f"\n[{stage}] 数量汇总（预计构造数，不是已验收样本数）", flush=True)
    for house in houses:
        identity = house["house"]
        picks, opening = house["selected_pick"], house["selected_open"]
        counts = dict.fromkeys(totals, 0)
        for case in recipe.cases:
            counts[case] = opening if case == "Case3" else picks * recipe.variants_per_case
        print(
            f"val_{identity}: 原始任务={house.get('discovered_task_count', '未记录')}; "
            f"静态条件通过 pick={house.get('eligible_pick', '未记录')} open={house.get('eligible_open', '未记录')}; "
            f"本阶段选中 pick={picks} open={opening}; "
            f"预计 Case1={counts['Case1']} Case2={counts['Case2']} Case3={counts['Case3']}",
            flush=True,
        )
        for case in totals:
            totals[case] += counts[case]
    print(
        f"总计: Case1={totals['Case1']}, Case2={totals['Case2']}, "
        f"Case3={totals['Case3']}, 全部={sum(totals.values())}",
        flush=True,
    )
    print("发现阶段仅为静态预估；资格检查后可能减少，最终以实际构造与物理检查为准。", flush=True)
    return totals


def _load_original_baseline(source, robot, snapshot):
    """加载原始场景或匹配的缓存基线；返回值由调用方负责关闭。"""
    if not snapshot:
        return Simulation.from_source(source, robot)
    saved = read_config(Path(snapshot) / "config.json")
    cached_robot = RobotConfig(**saved["robot"])
    if saved["source"] != source.to_dict() or asdict(cached_robot) != asdict(robot):
        raise ValueError("cached original baseline source/robot mismatch")
    sim = Simulation.from_snapshot(snapshot, cached_robot)
    sim.robot.config = robot  # 物理配置一致，仅统一路径和元组的表示。
    return sim


def _filter_tasks(tasks, recipe, house, exclusions):
    """先用配方筛选任务；显式目标选择只限制 pick，不限制 open。"""
    limits = _target_limits(recipe)
    selectors = recipe.selectors(house)
    for task in tasks:
        if task["operation"] not in limits:
            continue
        print(f"val_{house}: 检查 {task['operation']} {task['asset_id']} {task['task_id']}", flush=True)
        if recipe.categories and task["category"] not in recipe.categories:
            continue
        if (
            task["operation"] == "pick"
            and recipe.pick_target_types
            and pick_type(task) not in recipe.pick_target_types
        ):
            exclusions.append(
                {
                    "house": house,
                    "task_id": task["task_id"],
                    "category": task["category"],
                    "reason": "pick_target_not_in_assumed_high_success_whitelist",
                }
            )
            continue
        identities = (task["instance_id"], task["asset_id"], task["task_id"])
        if selectors and task["operation"] == "pick" and not any(s in identities for s in selectors):
            continue
        yield task


def _target_geometry(sim, task):
    """发现与资格检查复用支撑面信息；这里只计算，不决定目标是否合格。"""
    task = {**task, "support_obstacles": task_support_obstacles(sim, task)}
    supports = support_regions(sim, task) if task["operation"] == "pick" else []
    return {
        "task": task,
        "support_id": supports[0].support if supports else task["instance_id"],
        "support_area_m2": float(
            sum((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]) for r in supports)
        ),
    }, supports


def _probe_target(sim, task, recipe, geometry):
    """独立检查一个目标，返回候选记录或排除原因，不修改房屋基线。"""
    with closing(clone_preparation(sim)) as probe:
        probe.target_id = probe.model.body(task["target_body"]).id
        candidate, supports = _target_geometry(probe, task)
        task = candidate["task"]
        if task["operation"] == "pick" and not supports:
            return None, {"reason": "no_qualified_target_support_plane"}
        if (
            task["operation"] == "pick"
            and recipe.rule_version == "scene-cases-v3"
            and "Case1" in recipe.cases
            and not allowed_case1_support(probe, supports[0].support, recipe.case1_support_types)
        ):
            return None, {
                "support_id": supports[0].support,
                "reason": "case1_support_not_countertop_or_table",
            }
        # 达到下限即停止；这里的有效点数只是下界，不是完整可见站位数。
        stations = sample_stations(
            probe, task, geometry, static_only=True, stop_after_valid=recipe.min_prefilter_points
        )
        valid = sum(row["geometry"] == "valid" for row in stations)
        if valid < recipe.min_prefilter_points:
            return None, {"reason": "fewer_than_minimum_static_geometry_points", "count": valid}
        return {
            **candidate,
            "prefilter_count": valid,
            "prefilter_count_scope": "early_stop_lower_bound_not_full_candidate_count",
            "discovery_checked_count": len(stations),
        }, None


def _discover_house(recipe, robot, geometry, house, baseline):
    """完成一个房屋的基线冻结、静态筛选和空间选优。"""
    started = time.monotonic()
    source = SceneSource.procthor(recipe.dataset_dir, house)
    print(f"val_{house}: 加载原始基线", flush=True)
    eligible = {"pick": [], "open": []}
    with closing(_load_original_baseline(source, robot, recipe.input_snapshots.get(str(house)))) as sim:
        sim.freeze(baseline)
        print(f"val_{house}: 基线已冻结，开始发现任务", flush=True)
        tasks, skipped = discover_tasks(sim, read_json(source.metadata_path), geometry)
        exclusions = [{"house": house, **row} for row in skipped]
        for task in _filter_tasks(tasks, recipe, house, exclusions):
            candidate, rejection = _probe_target(sim, task, recipe, geometry)
            if rejection is not None:
                exclusions.append({"house": house, "task_id": task["task_id"], **rejection})
                continue
            eligible[task["operation"]].append(
                {
                    "house": house,
                    **candidate,
                    "source": source.to_dict(),
                    "input_snapshot": str(baseline.resolve()),
                }
            )
    # 后续只读候选记录，选优和统计不再占用仿真资源。
    picks = spatial_select(
        eligible["pick"], recipe.max_pick_targets_per_house, min_separation_m=recipe.min_target_separation_m
    )
    opening = spatial_select(eligible["open"], recipe.max_open_targets_per_house)
    chosen = picks + opening
    chosen_ids = {row["task"]["task_id"] for row in chosen}
    exclusions.extend(
        {
            "house": house,
            "task_id": row["task"]["task_id"],
            "reason": "eligible_not_selected_by_spatial_coverage",
        }
        for pool in eligible.values()
        for row in pool
        if row["task"]["task_id"] not in chosen_ids
    )
    summary = {
        "house": house,
        "discovered_task_count": len(tasks),
        "eligible_pick": len(eligible["pick"]),
        "eligible_open": len(eligible["open"]),
        **_selection_summary(chosen),
        "selection_method": "room_support_diversity_then_max_min_world_distance",
        "elapsed_s": time.monotonic() - started,
    }
    print(
        f"val_{house}: 原始任务={len(tasks)}; pick={[r['task']['asset_id'] for r in picks]}; "
        f"open={[r['task']['asset_id'] for r in opening]}",
        flush=True,
    )
    return chosen, exclusions, summary


def discover_scene_dataset(recipe, output):
    """逐房屋发现目标，汇总候选、排除原因和数量预估。"""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    recipe = load_scene_recipe(recipe)
    robot = load_scene_robot(recipe.robot_config)
    geometry = recipe.geometry
    started = time.monotonic()
    selected, exclusions, houses = [], [], []
    for house in recipe.houses:
        baseline = output.parent / (output.stem + "-baselines") / f"val_{house}" / "scene"
        chosen, rejected, summary = _discover_house(recipe, robot, geometry, house, baseline)
        selected.extend(chosen)
        exclusions.extend(rejected)
        houses.append(summary)
    print_scene_counts(recipe, houses, "发现")
    write_json(
        output,
        {
            "schema_version": "scene-dataset-discovery-v1",
            "recipe": asdict(recipe),
            "selected": selected,
            "houses": houses,
            "exclusions": exclusions,
            "elapsed_s": time.monotonic() - started,
        },
    )
    return output


def _qualification_candidates(sim, tasks, recipe, seed, eligible_ids):
    """只重建发现阶段已通过的目标，不重复审核同一冻结基线的静态条件。"""
    candidates = []
    for task in tasks:
        if task["task_id"] not in eligible_ids:
            continue
        candidate, _ = _target_geometry(sim, task)
        candidates.append({**seed, **candidate, "prefilter_count": recipe.min_prefilter_points})
    return candidates


def _qualify_target(sim, row, recipe, geometry, existing):
    """完整检查一个目标；站位不足时立即返回，不进入 Case1 地面点检查。"""
    task = row["task"]
    identity = task["task_id"]
    sim.target_id = sim.model.body(task["target_body"]).id
    started = time.monotonic()
    stations = (
        existing[identity]["stations"] if identity in existing else sample_stations(sim, task, geometry)
    )
    valid = sum(station["geometry"] == "valid" for station in stations)
    evidence = {
        "house": row["house"],
        "task_id": identity,
        "asset_id": task["asset_id"],
        "valid_count": valid,
        "candidate_count": len(stations),
        "elapsed_s": time.monotonic() - started,
        "stations": stations,
        "valid": valid >= recipe.min_final_points,
    }
    print(f"val_{row['house']} {task['asset_id']}: 可见站位={valid}", flush=True)
    if not evidence["valid"] or task["operation"] != "pick" or "Case1" not in recipe.cases:
        return evidence
    # Case1 还必须有实测近地面放置点，不能仅靠可见站位数量通过。
    regions = station_regions(sim, stations, task, geometry, robot_footprint(sim)["radius_m"], 0.12)
    approach_points(sim, stations, task, regions)
    near_count = sum(
        np.linalg.norm(np.asarray(point["xy"]) - task["anchor_world"][:2]) <= 0.85
        for point in regions["points"]
    )
    evidence["case1_near_ground_candidate_count"] = int(near_count)
    if not near_count:
        evidence.update(valid=False, reason="no_measured_near_ground_points_for_mandatory_chair")
    return evidence


def _select_qualified(sim, candidates, recipe, geometry, existing):
    """空间选优后检查资格；失败只剔除失败目标，再从合格池补选。"""
    selected, cache = [], {}
    for operation, count in _target_limits(recipe).items():
        pool = [row for row in candidates if row["task"]["operation"] == operation]
        separation = recipe.min_target_separation_m if operation == "pick" else 0.0
        while pool:
            choices = spatial_select(pool, count, min_separation_m=separation)
            rejected = set()
            for row in choices:
                identity = row["task"]["task_id"]
                # 补选时复用已检查目标，避免重复渲染和采样。
                if identity not in cache:
                    cache[identity] = _qualify_target(sim, row, recipe, geometry, existing)
                evidence = cache[identity]
                if evidence["valid"]:
                    row["baseline_visible_count"] = evidence["valid_count"]
                else:
                    rejected.add(identity)
            if not rejected:
                selected.extend(choices)
                break
            pool = [row for row in pool if row["task"]["task_id"] not in rejected]
    return selected, list(cache.values())


def qualify_scene_discovery(discovery, output):
    """逐房屋检查完整可见站位；只从静态合格池补选，不放宽物理条件。"""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    plan = read_config(discovery)
    recipe = SceneDatasetRecipe(**plan["recipe"])
    robot = load_scene_robot(recipe.robot_config)
    geometry = recipe.geometry
    started = time.monotonic()
    selected, qualifications = [], []
    existing = {row["task_id"]: row for row in plan.get("qualification", [])}
    for house in recipe.houses:
        house_selected = [row for row in plan["selected"] if row["house"] == house]
        if not house_selected:
            continue  # 没有静态合格目标时保留排除原因，不为房屋凑数。
        seed = house_selected[0]
        eligible_ids = {row["task"]["task_id"] for row in house_selected}
        eligible_ids.update(
            row["task_id"]
            for row in plan["exclusions"]
            if row.get("house") == house and row.get("reason") == "eligible_not_selected_by_spatial_coverage"
        )
        source = SceneSource.procthor(recipe.dataset_dir, house)
        with closing(Simulation.from_snapshot(seed["input_snapshot"], robot)) as sim:
            tasks, _ = discover_tasks(sim, read_json(source.metadata_path), geometry)
            candidates = _qualification_candidates(sim, tasks, recipe, seed, eligible_ids)
            chosen, evidence = _select_qualified(sim, candidates, recipe, geometry, existing)
        selected.extend(chosen)
        qualifications.extend(evidence)
    plan["selected"] = selected
    plan["exclusions"].extend(
        {key: value for key, value in row.items() if key != "stations"}
        | {"reason": row.get("reason", "fewer_than_minimum_baseline_visible_points")}
        for row in qualifications
        if not row["valid"]
    )
    for house in plan["houses"]:
        house_rows = [row for row in selected if row["house"] == house["house"]]
        house.update(_selection_summary(house_rows))
    elapsed = time.monotonic() - started
    plan.update(
        static_discovery=str(Path(discovery).resolve()),
        qualification_wall_s=elapsed,
        qualification=qualifications,
        elapsed_s=plan["elapsed_s"] + elapsed,
    )
    print_scene_counts(recipe, plan["houses"], "资格检查")
    write_json(output, plan)
    return output


def _check_target_quotas(recipe, selected):
    """创建输出目录前检查配额；-1 是全选，不要求最小数量。"""
    for house in recipe.houses:
        for operation, count in _target_limits(recipe).items():
            available = sum(
                row["house"] == house and row["task"]["operation"] == operation for row in selected
            )
            if count > 0 and available < count:
                raise ValueError(f"not enough {operation} targets in house {house}")


def _save_open_scene(unit, scene_id, root, recipe, robot, geometry):
    """open 不加障碍物；采样和压缩站位后保存场景或返回拒绝记录。"""
    task = unit["task"]
    with closing(Simulation.from_snapshot(unit["input_snapshot"], robot, target=task["target_body"])) as sim:
        points, _ = select_stations(
            sample_stations(sim, task, geometry),
            task["anchor_world"],
            recipe.station_selection,
            recipe.max_final_stations,
        )
        if len(points) < recipe.min_final_points:
            return {"scene_id": scene_id, "status": "rejected", "reason": "too_few_stations"}
        location = Path("scene-files") / scene_id
        sim.freeze(root / location)
    return {
        "scene_id": scene_id,
        "status": "accepted",
        "scene": str(location),
        "task": task,
        "stations": points,
        "placements": [],
        "case": "Case3",
    }


def _obstacle_config(recipe, recipe_path, unit, case, seed):
    """把场景配方显式转为增强参数，不引入字段扫描或另一套配置协议。"""
    case1 = case == "Case1"
    # Case1 的总数量还包含一把地面椅子；Case2 只有台面物体。
    bounds = [n + 1 for n in recipe.case1_added_surface_count] if case1 else recipe.case2_added_surface_count
    return ObstacleConfig(
        baseline_config=str(recipe_path),
        dataset_dir=recipe.dataset_dir,
        input_snapshot=unit["input_snapshot"],
        asset_library=recipe.asset_library,
        target_instance=unit["task"]["instance_id"],
        house=unit["house"],
        schema_version="procedural-obstacles-v3",
        variant_count=recipe.variants_per_case,
        seed=seed,
        obstacle_count=bounds,
        mandatory_categories=["Chair"] if case1 else [],
        ground_categories=["Chair"] if case1 else [],
        surface_categories=recipe.case1_surface_categories if case1 else recipe.case2_surface_categories,
        assets_per_category=recipe.assets_per_category,
        max_candidates=recipe.max_candidates,
        max_physical_trials=recipe.max_physical_trials,
        variant_timeout_s=recipe.variant_timeout_s,
        settle=recipe.settle,
        views=recipe.views,
        construction={
            "case": case,
            "rule_version": recipe.rule_version,
            "min_prefilter_points": recipe.min_prefilter_points,
            "min_final_points": recipe.min_final_points,
            "station_selection": recipe.station_selection,
            "max_final_stations": recipe.max_final_stations,
            "min_substantial": recipe.min_substantial,
            "case1_support_types": recipe.case1_support_types,
        },
    )


def build_scene_dataset(recipe_path, discovery, output_dir, dataset_id, gpu_ids, allow_partial_targets=False):
    """消费同配方的资格结果，按 Case 分发保存或增强，不展开底层构造过程。"""
    recipe = load_scene_recipe(recipe_path)
    robot = load_scene_robot(recipe.robot_config)
    geometry = recipe.geometry
    plan = read_config(discovery)
    if plan["recipe"] != asdict(recipe):
        raise ValueError("discovery recipe mismatch")
    if not allow_partial_targets:
        _check_target_quotas(recipe, plan["selected"])
    root = create_run(
        Path(output_dir) / "scene_datasets" / dataset_id,
        {"recipe": asdict(recipe), "robot": asdict(robot), "geometry": asdict(geometry), "seed": recipe.seed},
    )
    os.environ["MUJOCO_EGL_DEVICE_ID"] = str(gpu_ids[0])
    for unit_index, unit in enumerate(plan["selected"]):
        for case in recipe.cases:
            if (case == "Case3") != (unit["task"]["operation"] == "open"):
                continue
            scene_id = f"val{unit['house']}-{unit['task']['task_id']}-{case}"
            if case == "Case3":
                append_row(
                    root / "scenes.jsonl", _save_open_scene(unit, scene_id, root, recipe, robot, geometry)
                )
                continue
            config = _obstacle_config(recipe, recipe_path, unit, case, recipe.seed + unit_index)
            # 增强器直接写父场景库，不创建第二套子 run 或发布记录。
            augment_obstacles(
                config,
                output_dir,
                run_id=scene_id,
                geometry_config=geometry,
                robot_config=robot,
                dataset_root=root,
            )
    records = rows(root / "scenes.jsonl")
    write_json(
        root / "summary.json",
        {
            "status": "completed",
            "accepted": sum(row["status"] == "accepted" for row in records),
            "rejected": sum(row["status"] != "accepted" for row in records),
        },
    )
    return root


def build_scenes(config, output_dir, run_id, *, gpu_ids, allow_partial_targets=False):
    """依次执行发现、资格检查和构建；退出时清理中间基线及计划文件。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update(MUJOCO_GL="egl", MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]))
    with tempfile.TemporaryDirectory(prefix="scene-build-", dir=output_dir) as temporary:
        discovery = discover_scene_dataset(config, Path(temporary) / "discovery.json")
        qualified = qualify_scene_discovery(discovery, Path(temporary) / "qualified.json")
        return build_scene_dataset(
            config, qualified, output_dir, run_id, gpu_ids, allow_partial_targets=allow_partial_targets
        )
