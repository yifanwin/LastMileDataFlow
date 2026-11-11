"""统一编排站位标定与连续采集，共用运行准备、场景遍历和资源收尾。"""

import logging
from collections import Counter
from dataclasses import asdict, replace
import os
from itertools import count
from pathlib import Path
import time

from ..gpu import bind_device, select_devices
from ..config import CollectionConfig, load_local_config, load_station_run, read_config
from ..utils.io import append_row, create_run, rows, write_json, write_rows
from ..utils.logging_config import Progress, trial_logging
from ..runtime.simulation import Simulation
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import execution_profile, calibration_execution
from ..data.evaluation.selection import (
    station_calibration,
    eligible_stations,
    generate_pairs,
)
from ..data.export.lastmile_dataset import successful, training_record, finalize_dataset
from ..scenes.tasks.discovery import robot_footprint
from ..planning.navigation.astar import scene_grid, task_support_obstacles
from ..planning.navigation.geometry import (
    GeometryNavigation,
    NavigationSettings,
    NAVIGATION_PROTOCOL,
)

from .station_map import (
    _execute,
    _trial_record,
    _shared_failure,
    _station_trials,
    _station_summary,
    evaluate_stations,
)

logger = logging.getLogger(__name__)


def _plan_collection(scenes, trials, config, root, source):
    """先落盘全部 pair 和已有成功示教，再启动任何连续执行。"""
    pairs, direct_rows, seen = [], [], set()
    for scene in scenes:
        current = [row for row in trials if row["scene_id"] == scene["scene_id"]]
        binding = {"scene_id": scene["scene_id"], "task_id": scene["task"]["task_id"]}
        if any(row["task_id"] != binding["task_id"] for row in current):
            raise ValueError("calibration task mismatch")
        rates = station_calibration(
            scene["stations"], current, config.trials_per_station, binding, config
        )
        candidates = [
            {
                "s0": start["station_id"],
                "s1": goal["station_id"],
                "s0_success_rate": start["success_rate"],
                "s1_success_rate": goal["success_rate"],
            }
            for start, goal in generate_pairs(
                *eligible_stations(scene["stations"], rates, config)
            )
        ]
        points = {r["station_id"]: r for r in scene["stations"]}
        for candidate in candidates:
            start, goal = points[candidate["s0"]], points[candidate["s1"]]
            distance = sum((a - b) ** 2 for a, b in zip(start["xy"], goal["xy"])) ** 0.5
            pairs.append(
                {
                    **binding,
                    "pair_id": f"p{len(pairs):06d}",
                    "s0": start["station_id"],
                    "s1": goal["station_id"],
                    **candidate,
                    "seed": config.seed + len(pairs),
                    "status": "not_executed",
                    "reason": "pending",
                    "euclidean_distance_m": distance,
                    "astar_path_length_m": None,
                    "navigation_steps": 0,
                    "rollout_id": None,
                }
            )
        for row in current:
            if successful(row) and row["rollout_id"] not in seen:
                direct_rows.append((row, scene))
                seen.add(row["rollout_id"])
    write_rows(root / "pair_index.jsonl", pairs)
    write_rows(root / "train_index.jsonl", [])
    for row, scene in direct_rows:
        direct = dict(row)
        if successful(direct, observations=True):
            append_row(
                root / "train_index.jsonl",
                training_record(direct, source, "s1_direct_operation"),
            )

    return pairs


def _recovery_trials(
    baseline, scene, pairs, assets_dir, config, collection, root, *, attempt_ids
):
    """执行当前场景的全部 pair；完整计划始终保留在 pair_index。"""
    current = [pair for pair in pairs if pair["scene_id"] == scene["scene_id"]]
    if not current:
        return []
    footprint = robot_footprint(baseline, whole_body=config.navigation_whole_body)
    grid = scene_grid(
        baseline,
        scene["task"]["anchor_world"],
        None,
        config.map_resolution_m,
        footprint["radius_m"],
        config.navigation_margin_m,
        robot_height=footprint["max"][2] if config.navigation_whole_body else 0.4,
        support_obstacles=task_support_obstacles(baseline, scene["task"]),
    )
    stations = {station["station_id"]: station for station in scene["stations"]}
    navigation = GeometryNavigation(
        baseline,
        grid,
        list(stations.values()),
        NavigationSettings.from_config(config),
        budget_s=min(120.0, config.attempt_timeout_s),
    )
    progress = Progress(logger)
    processed = []
    try:
        for pair in current:
            start, goal = stations[pair["s0"]], stations[pair["s1"]]
            label = f"scene={scene['scene_id']} S0={pair['s0']} S1={pair['s1']}"
            with trial_logging(label, progress):
                navigation.hold(start)
                if navigation.compatible(goal):
                    planned = navigation.plan(
                        start["base"],
                        [
                            {
                                "xy": goal["base"][:2],
                                "yaw": goal["base"][2],
                                "anchor_station_id": goal["station_id"],
                            }
                        ],
                    )
                else:
                    planned = {"path": None, "reason": "navigation_posture_mismatch"}
                path = planned["path"]
                pair["navigation_planning"] = {
                    k: v for k, v in planned.items() if k != "path"
                }
                if path is None:
                    pair.update(status="path_unavailable", reason=planned["reason"])
                else:
                    # 使用实际发送给跟踪器的折线，包括站位到栅格中心的连接段。
                    pair["astar_path_length_m"] = sum(
                        sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5
                        for left, right in zip(path["xy"], path["xy"][1:])
                    )
                    pair.update(
                        status="running",
                        reason="attempt_started",
                        navigation_steps=None,
                    )
                    result = _execute(
                        baseline,
                        scene,
                        start,
                        assets_dir,
                        config,
                        collection,
                        root,
                        pair["seed"],
                        path=path,
                        goal=goal,
                        attempt_ids=attempt_ids,
                    )
                    pair.update(_trial_record(result, scene, start, None, pair["seed"]))
                    pair["navigation_steps"] = (result.get("navigation") or {}).get(
                        "steps", 0 if result.get("executed_steps") == 0 else None
                    )
                    if successful(pair, continuous=True, observations=True):
                        append_row(
                            root / "train_index.jsonl",
                            training_record(pair, ".", "s0_to_s1_continuous"),
                        )
            processed.append(pair)
            write_rows(root / "pair_index.jsonl", pairs)
            progress.update(
                "连续采集 scene=%s 已处理=%s/%s 当前pair=%s status=%s",
                scene["scene_id"],
                len(processed),
                len(current),
                pair["pair_id"],
                pair["status"],
            )
            if _shared_failure(pair):
                break
    finally:
        navigation.close()
        for pair in current:
            if pair["status"] == "running":
                pair.update(status="unknown", reason="attempt_interrupted")
        write_rows(root / "pair_index.jsonl", pairs)
    return processed


def _collect_scene(
    sim,
    scene,
    assets_dir,
    config,
    collection,
    root,
    policy,
    prior,
    max_trials,
    *,
    attempt_ids,
    pairs,
):
    """标定已在规划前完成；当前场景只展示率表并执行全组合。"""
    trials = [row for row in prior if row["scene_id"] == scene["scene_id"]]
    summary = _station_summary(sim, scene, trials, config, root)
    summary["eligible_pairs"] = sum(
        pair["scene_id"] == scene["scene_id"] for pair in pairs
    )
    episodes = _recovery_trials(
        sim, scene, pairs, assets_dir, config, collection, root, attempt_ids=attempt_ids
    )
    summary["successes"] = sum(successful(row, continuous=True) for row in episodes)
    summary["shared_failure"] = any(_shared_failure(row) for row in episodes)
    return summary


def _prepare_run(
    dataset,
    config,
    collection,
    output_dir,
    run_id,
    gpu,
    samples,
    calibration,
    *,
    resources=None,
    dataset_split=None,
):
    """对齐场景与执行配置，检查标定复用条件并创建运行目录。"""
    # 复用采样间距和观测尺寸；操作工作区和导航碰撞参数由执行配置决定。
    geometry = read_config(Path(dataset) / "config.json").get("geometry", {})
    keys = ("spacing_m", "width", "height", "min_target_pixels")
    config = replace(config, **{key: geometry[key] for key in keys if key in geometry})
    collection = replace(
        collection,
        width=config.width,
        height=config.height,
        seed=config.seed,
        output_dir=str(Path(output_dir).resolve()),
    )
    scenes = load_scenes(dataset, samples)
    if dataset_split is not None:
        scenes = [scene for scene in scenes if scene["dataset_split"] == dataset_split]
    if not scenes:
        raise ValueError("no accepted scenes")
    # 检查场景控制模式，并生成各场景的执行配置档案。
    profiles = {}
    for scene in scenes:
        from ..config import robot_config

        robot = robot_config(
            read_config(Path(scene["scene_path"]) / "config.json")["robot"]
        )
        from ..policies.config import check_profile

        check_profile(robot, config.operation_backend, config.policy_config)
        scene["robot_config"] = robot
        profiles[scene["scene_id"]] = execution_profile(robot, config)
    prior = None
    # 仅复用执行配置及各场景执行档案均匹配的标定。
    if calibration:
        logger.info("复用已有标定=%s，不重复 rollout", calibration)
        saved = read_config(Path(calibration) / "config.json")
        if (
            Path(saved["dataset"]).resolve() != Path(dataset).resolve()
            or any(
                saved["profiles"].get(sid) != profile
                for sid, profile in profiles.items()
            )
            or calibration_execution(saved["config"])
            != calibration_execution(asdict(config))
        ):
            raise ValueError(
                "calibration scene/backend/robot/sensor/policy configuration mismatch"
            )
        prior = rows(Path(calibration) / "trials.jsonl")
    # 保存本次运行实际使用的配置及输入引用。
    root = create_run(
        Path(output_dir) / run_id,
        {
            "dataset": str(Path(dataset).resolve()),
            "config": asdict(config),
            "collection": asdict(collection),
            "navigation_protocol": NAVIGATION_PROTOCOL,
            "navigation_settings": asdict(NavigationSettings.from_config(config)),
            "profiles": profiles,
            "gpu": gpu,
            "seed": config.seed,
            "calibration": str(Path(calibration).resolve()) if calibration else None,
            "resources": resources,
        },
        prepared=True,
    )
    if resources and resources.get("split_planning"):
        from .station_map import calibration_recording

        saved = read_config(root / "config.json")
        saved["calibration_collection"] = asdict(
            calibration_recording(collection, resources["recording_mode"])
        )
        write_json(root / "config.json", saved)
    return root, scenes, config, collection, prior


def _run_dataset(
    dataset,
    assets_dir,
    config,
    collection,
    output_dir,
    process_scene,
    *,
    run_id,
    gpu_ids,
    samples=None,
    calibration=None,
    max_trials=None,
    station_workers=1,
    egl_devices=None,
    cpu_workers=60,
    preparation_workers=12,
    recording_mode="numerical",
    planning_workers_per_gpu=1,
):
    """统一准备运行、遍历场景，并关闭资源和保存汇总结果。"""
    # 设置计算与渲染设备，再准备场景和运行目录。
    started = time.monotonic()
    split = config.operation_backend == "curobo" and config.shared_station_preparation
    if station_workers < 1:
        raise ValueError("station_workers must be positive")
    if station_workers > 1 and config.operation_backend != "curobo":
        raise ValueError("parallel station evaluation currently uses cuRobo")
    egl_devices = select_devices(gpu_ids, egl_devices)
    if len(egl_devices) != len(gpu_ids):
        raise ValueError("provide one EGL device index per GPU")
    resources = {
        "station_workers": station_workers,
        "gpu_ids": list(gpu_ids),
        "egl_devices": list(egl_devices),
    }
    split_resources = None
    if split:
        from .station_map import RunResources

        split_resources = RunResources(
            tuple(gpu_ids),
            cpu_workers,
            preparation_workers,
            recording_mode,
            planning_workers_per_gpu,
        )
        resources.update(asdict(split_resources), split_planning=True)
    station_options = dict(
        workers=station_workers, gpu_ids=gpu_ids, egl_devices=egl_devices
    )
    bind_device(gpu_ids[0], egl_devices[0])
    root, scenes, config, collection, prior = _prepare_run(
        dataset,
        config,
        collection,
        output_dir,
        run_id,
        gpu_ids[0],
        samples,
        calibration,
        resources=resources,
    )
    logger.info(
        "运行=%s scene数=%s backend=%s GPU=%s 每点重复=%s 每场景预算=%s 标定来源=%s",
        root,
        len(scenes),
        config.operation_backend,
        gpu_ids[0],
        config.trials_per_station,
        max_trials if max_trials is not None else "完整标定",
        calibration or "当前 run 内执行",
    )
    summaries = []
    attempt_ids = count()
    policy = None
    collecting = process_scene is _collect_scene
    pairs = []
    stopped = False
    calibration_summary = None
    try:
        if split and prior is None:
            from .station_map import calibrate_stations
            from .station_map import calibration_recording

            prior, calibration_summary = calibrate_stations(
                scenes,
                assets_dir,
                config,
                calibration_recording(collection, recording_mode),
                root,
                split_resources,
                attempt_ids=attempt_ids,
                max_trials=max_trials,
            )
            stopped = bool(calibration_summary["failure"])
        # 无外部标定时，先完成全部场景的标定，再生成全量计划。
        if collecting:
            if prior is None:
                prior = []
                for scene in scenes:
                    sim = Simulation.from_snapshot(
                        scene["scene_path"],
                        scene["robot_config"],
                        target=scene["task"]["target_body"],
                    )
                    sim.scene_id = scene["scene_id"]
                    try:
                        current = _station_trials(
                            sim,
                            scene,
                            assets_dir,
                            config,
                            collection,
                            root,
                            budget=max_trials,
                            attempt_ids=attempt_ids,
                            **station_options,
                        )
                        prior.extend(current)
                    finally:
                        sim.close()
                    if any(_shared_failure(row) for row in current):
                        stopped = True
                        break
            pairs = _plan_collection(
                scenes,
                prior,
                config,
                root,
                os.path.relpath(Path(calibration).resolve(), root.resolve())
                if calibration
                else ".",
            )
        # 策略客户端跨场景复用，仿真实例逐场景创建并关闭。
        for index, scene in enumerate(scenes, 1):
            if stopped and (collecting or not split):
                break
            logger.info(
                "场景 %s/%s 开始 scene=%s target=%s station数=%s",
                index,
                len(scenes),
                scene["scene_id"],
                scene["task"].get("asset_id", scene["task"]["task_id"]),
                len(scene["stations"]),
                extra={"progress": True},
            )
            robot = scene["robot_config"]
            sim = Simulation.from_snapshot(
                scene["scene_path"], robot, target=scene["task"]["target_body"]
            )
            sim.scene_id = scene["scene_id"]
            try:
                summary = process_scene(
                    sim,
                    scene,
                    assets_dir,
                    config,
                    collection,
                    root,
                    policy,
                    prior,
                    max_trials,
                    attempt_ids=attempt_ids,
                    **(
                        {"pairs": pairs}
                        if collecting
                        else {"station_options": station_options}
                    ),
                )
                summaries.append(summary)
                stopped = stopped or summary.get("shared_failure", False)
                logger.info("场景结束 %s", scene["scene_id"], extra={"progress": True})
            finally:
                sim.close()
    finally:
        # 异常退出也关闭策略服务并保存已完成场景；运行完整不等于任务成功。
        if collecting:
            if not pairs:
                pairs = rows(root / "pair_index.jsonl")
            for pair in pairs:
                if pair["status"] == "not_executed":
                    pair["reason"] = "shared_failure" if stopped else "run_interrupted"
            write_rows(root / "pair_index.jsonl", pairs)
            finalize_dataset(root)
        training = rows(root / "train_index.jsonl")
        executed_pairs = sum((pair.get("executed_steps") or 0) > 0 for pair in pairs)
        write_json(
            root / "summary.json",
            {
                "status": "completed"
                if len(summaries) == len(scenes) and not stopped
                else "incomplete",
                "scenes": summaries,
                "elapsed_s": time.monotonic() - started,
                **(
                    {"calibration": calibration_summary}
                    if calibration_summary is not None
                    else {}
                ),
                **(
                    {
                        "planned_pairs": len(pairs),
                        "processed_pairs": sum(
                            p["status"] != "not_executed" for p in pairs
                        ),
                        "executed_pairs": executed_pairs,
                        "execution_coverage": executed_pairs / len(pairs)
                        if pairs
                        else 0.0,
                        "pair_status_counts": dict(Counter(p["status"] for p in pairs)),
                        "train_source_counts": dict(
                            Counter(row["source_type"] for row in training)
                        ),
                    }
                    if collecting
                    else {}
                ),
            },
        )
        logger.info(
            "运行收尾 输出=%s 已处理场景=%s/%s 耗时=%.1fs",
            root,
            len(summaries),
            len(scenes),
            time.monotonic() - started,
        )
        if len(summaries) != len(scenes) or stopped:
            logger.warning(
                "运行未完整结束：已处理场景=%s/%s", len(summaries), len(scenes)
            )
    return root


def collect_lastmile(
    dataset,
    assets_dir,
    config,
    collection,
    output_dir,
    *,
    run_id,
    gpu_ids,
    samples=None,
    calibration=None,
    max_trials=None,
    station_workers=1,
    egl_devices=None,
    cpu_workers=60,
    preparation_workers=12,
    recording_mode="numerical",
    planning_workers_per_gpu=1,
):
    """复用匹配的标定或先完成标定，再用 A* 导航与 cuRobo V2 连续采集。"""
    if config.operation_backend != "curobo":
        raise ValueError("continuous collection currently uses cuRobo")
    return _run_dataset(
        dataset,
        assets_dir,
        config,
        collection,
        output_dir,
        _collect_scene,
        run_id=run_id,
        gpu_ids=gpu_ids,
        samples=samples,
        calibration=calibration,
        max_trials=max_trials,
        station_workers=station_workers,
        egl_devices=egl_devices,
        cpu_workers=cpu_workers,
        preparation_workers=preparation_workers,
        recording_mode=recording_mode,
        planning_workers_per_gpu=planning_workers_per_gpu,
    )


def run_stations(
    dataset,
    run_config,
    output,
    *,
    gpu_ids,
    collect,
    calibration=None,
    samples=None,
    egl_devices=None,
):
    """Script entry: calibrate every station, or collect S0→S1 on top of a calibration."""
    from ..runtime.execution_config import load_execution_config

    run = load_station_run(run_config)
    config = load_execution_config(run.execution)
    recording = CollectionConfig(
        record_video=run.record_video,
        record_depth=run.record_depth,
        record_physics=run.record_physics,
        record_images=collect
        or not config.shared_station_preparation
        or run.record_depth,
    )
    mode = run.recording_mode or (
        "visual" if run.record_video or run.record_depth else "numerical"
    )
    assets_dir = run.assets_dir or load_local_config().assets_dir
    if assets_dir is None:
        raise ValueError(
            "assets_dir must be set in the run config or configs/local.json"
        )
    output = Path(output)
    options = dict(
        run_id=output.name,
        gpu_ids=gpu_ids,
        samples=samples,
        max_trials=run.max_trials,
        station_workers=run.station_workers,
        egl_devices=egl_devices,
        planning_workers_per_gpu=run.planning_workers_per_gpu,
        cpu_workers=run.cpu_workers,
        preparation_workers=run.preparation_workers,
        recording_mode=mode,
    )
    if collect:
        return collect_lastmile(
            dataset,
            assets_dir,
            config,
            recording,
            output.parent,
            calibration=calibration,
            **options,
        )
    return evaluate_stations(
        dataset, assets_dir, config, recording, output.parent, **options
    )
