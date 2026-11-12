"""连续采集：复用或先完成站位标定，再对全部合格 S0/S1 配对执行 A* 导航加 cuRobo 操作。"""

import logging
import math
import os
import time
from collections import Counter
from itertools import product
from pathlib import Path

from lastmile_dataflow.station_map.aggregate import eligible_stations, station_calibration
from .export import finalize_dataset, successful, training_record
from lastmile_dataflow.planning.navigation.astar import navigation_grid
from lastmile_dataflow.planning.navigation.routes import GeometryNavigation, NavigationSettings
from .continuous import run_continuous_attempt
from lastmile_dataflow.scenes.library import open_scene
from lastmile_dataflow.scenes.tasks import grasp_candidates
from lastmile_dataflow.sim.placement import robot_footprint
from lastmile_dataflow.utils.io import append_row, rows, write_rows
from lastmile_dataflow.utils.logging_config import Progress, trial_logging
from lastmile_dataflow.station_map.planner_trials.build import (
    calibrate_stations,
    log_scene_start,
    prepare_run,
    scene_trials,
    shared_failure,
    station_summary,
    trial_record,
    write_summary,
)

logger = logging.getLogger(__name__)

_RESULT_LOG_LEVEL = {
    "initialization_error": logging.ERROR,
    "episode_error": logging.ERROR,
    "recording_error": logging.ERROR,
    "simulation_failure": logging.ERROR,
    "infrastructure_error": logging.ERROR,
    "timeout": logging.WARNING,
}


def _plan_pairs(context, trials):
    """全部合格 S0×S1 组合，按场景顺序编号；不设上限。"""
    config, pairs = context.config, []
    for scene in context.scenes:
        current = scene_trials(trials, scene)
        binding = {"scene_id": scene["scene_id"], "task_id": scene["task"]["task_id"]}
        if any(row["task_id"] != binding["task_id"] for row in current):
            raise ValueError("calibration task mismatch")
        rates = station_calibration(
            scene["stations"], current, config.trials_per_station, binding, config.operation_backend
        )
        points = {station["station_id"]: station for station in scene["stations"]}
        starts, goals = eligible_stations(scene["stations"], rates, config.lastmile_low, config.lastmile_high)
        for start, goal in product(starts, goals):
            pairs.append(
                {
                    **binding,
                    "pair_id": f"p{len(pairs):06d}",
                    "s0": start["station_id"],
                    "s1": goal["station_id"],
                    "s0_success_rate": start["success_rate"],
                    "s1_success_rate": goal["success_rate"],
                    "seed": config.seed + len(pairs),
                    "status": "not_executed",
                    "reason": "pending",
                    "euclidean_distance_m": math.dist(
                        points[start["station_id"]]["xy"], points[goal["station_id"]]["xy"]
                    ),
                    "astar_path_length_m": None,
                    "navigation_steps": 0,
                    "rollout_id": None,
                }
            )
    return pairs


def _plan_collection(context, trials, source):
    """先落盘全部 pair 和已有成功示教（标定里完整的成功 trial），再启动任何连续执行。"""
    pairs = _plan_pairs(context, trials)
    write_rows(context.root / "pair_index.jsonl", pairs)
    training, seen = [], set()
    for scene in context.scenes:
        for row in scene_trials(trials, scene):
            if successful(row, observations=True) and row["rollout_id"] not in seen:
                training.append(training_record(row, source, "s1_direct_operation"))
                seen.add(row["rollout_id"])
    write_rows(context.root / "train_index.jsonl", training)
    return pairs


def _execute(context, sim, scene, start, goal, path, seed, execute):
    """分配本次运行内唯一的试验编号，执行一次连续 S0→S1 试验。"""
    attempt_id = f"r{next(context.attempt_ids):06d}"
    logger.debug(
        "试验开始 %s scene=%s station=%s backend=%s seed=%s",
        attempt_id,
        scene["scene_id"],
        start["station_id"],
        context.config.operation_backend,
        seed,
    )
    started = time.monotonic()
    result = execute(
        sim,
        scene["task"],
        start,
        grasp_candidates(scene["task"], context.assets_dir),
        context.assets_dir,
        context.config,
        context.collection,
        root=context.root,
        attempt_id=attempt_id,
        seed=seed,
        path=path,
        goal=goal,
    )
    logger.log(
        _RESULT_LOG_LEVEL.get(result.get("status"), logging.DEBUG),
        "试验结束 %s status=%s reason=%s steps=%s recording_complete=%s elapsed=%.1fs",
        attempt_id,
        result.get("status"),
        result.get("reason"),
        result.get("executed_steps"),
        result.get("recording_complete"),
        time.monotonic() - started,
    )
    return result


def _navigation(context, sim, scene):
    config = context.config
    footprint = robot_footprint(sim, whole_body=config.navigation_whole_body)
    grid = navigation_grid(sim, scene["task"], config, footprint["radius_m"], footprint["max"][2])
    settings = NavigationSettings.from_config(
        config, speed=config.navigation_speed_m_s, yaw_speed=config.navigation_yaw_speed_rad_s
    )
    return GeometryNavigation(sim, grid, scene["stations"], settings, budget_s=min(120.0, config.attempt_timeout_s))


def _run_pair(context, navigation, sim, scene, pair, execute):
    """规划 S0→S1 的 A* 路径并执行；无路径是不产生 rollout 的负结果。"""
    stations = {station["station_id"]: station for station in scene["stations"]}
    start, goal = stations[pair["s0"]], stations[pair["s1"]]
    navigation.hold(start)
    if navigation.compatible(goal):
        planned = navigation.plan(
            start["base"], [{"xy": goal["base"][:2], "yaw": goal["base"][2], "anchor_station_id": goal["station_id"]}]
        )
    else:
        planned = {"path": None, "reason": "navigation_posture_mismatch"}
    path = planned["path"]
    pair["navigation_planning"] = {key: value for key, value in planned.items() if key != "path"}
    if path is None:
        pair.update(status="path_unavailable", reason=planned["reason"])
        return
    # 使用实际发送给跟踪器的折线，包括站位到栅格中心的连接段。
    pair["astar_path_length_m"] = sum(math.dist(left, right) for left, right in zip(path["xy"], path["xy"][1:]))
    pair.update(status="running", reason="attempt_started", navigation_steps=None)
    result = _execute(context, sim, scene, start, goal, path, pair["seed"], execute)
    pair.update(trial_record(result, scene, start, None, pair["seed"]))
    pair["navigation_steps"] = (result.get("navigation") or {}).get(
        "steps", 0 if result.get("executed_steps") == 0 else None
    )
    if successful(pair, continuous=True, observations=True):
        append_row(context.root / "train_index.jsonl", training_record(pair, ".", "s0_to_s1_continuous"))


def _recovery_trials(context, sim, scene, pairs, execute):
    """执行当前场景的全部 pair；完整计划始终保留在 pair_index。"""
    current = [pair for pair in pairs if pair["scene_id"] == scene["scene_id"]]
    if not current:
        return []
    index_path = context.root / "pair_index.jsonl"
    navigation = _navigation(context, sim, scene)
    progress = Progress(logger)
    processed = []
    try:
        for pair in current:
            with trial_logging(f"scene={scene['scene_id']} S0={pair['s0']} S1={pair['s1']}", progress):
                _run_pair(context, navigation, sim, scene, pair, execute)
            processed.append(pair)
            # 每个 pair 后重写整个索引（O(n²)，有意为之）：中途中断也能看到已完成的进度。
            write_rows(index_path, pairs)
            progress.update(
                "连续采集 scene=%s 已处理=%s/%s 当前pair=%s status=%s",
                scene["scene_id"],
                len(processed),
                len(current),
                pair["pair_id"],
                pair["status"],
            )
            if shared_failure(pair):
                break
    finally:
        navigation.close()
        for pair in current:
            if pair["status"] == "running":
                pair.update(status="unknown", reason="attempt_interrupted")
        write_rows(index_path, pairs)
    return processed


def _collect_scene(context, sim, scene, trials, pairs, execute):
    """标定已在规划前完成；当前场景只展示率表并执行全组合。"""
    summary = station_summary(sim, scene, scene_trials(trials, scene), context.config, context.root)
    summary["eligible_pairs"] = sum(pair["scene_id"] == scene["scene_id"] for pair in pairs)
    episodes = _recovery_trials(context, sim, scene, pairs, execute)
    summary["successes"] = sum(successful(row, continuous=True) for row in episodes)
    summary["shared_failure"] = any(shared_failure(row) for row in episodes)
    return summary


def _finish_pairs(root, pairs, stopped):
    for pair in pairs:
        if pair["status"] == "not_executed":
            pair["reason"] = "shared_failure" if stopped else "run_interrupted"
    write_rows(root / "pair_index.jsonl", pairs)
    finalize_dataset(root)
    training = rows(root / "train_index.jsonl")
    executed = sum((pair.get("executed_steps") or 0) > 0 for pair in pairs)
    return {
        "planned_pairs": len(pairs),
        "processed_pairs": sum(p["status"] != "not_executed" for p in pairs),
        "executed_pairs": executed,
        "execution_coverage": executed / len(pairs) if pairs else 0.0,
        "pair_status_counts": dict(Counter(p["status"] for p in pairs)),
        "train_source_counts": dict(Counter(row["source_type"] for row in training)),
    }


def run_collection(dataset, run, output, *, gpu_ids, calibration=None, samples=None, execute=run_continuous_attempt):
    """复用匹配的标定或先在本 run 内完成标定，再用 A* 导航与 cuRobo V2 连续采集。"""
    context = prepare_run(dataset, run, output, gpu_ids, collect=True, samples=samples, calibration=calibration)
    source = os.path.relpath(Path(calibration).resolve(), context.root.resolve()) if calibration else "."
    summaries, pairs, stopped, calibration_summary = [], [], False, None
    try:
        trials = context.prior
        if trials is None:
            trials, calibration_summary = calibrate_stations(context)
            stopped = bool(calibration_summary["failure"])
        pairs = _plan_collection(context, trials, source)
        for index, scene in enumerate(context.scenes, 1):
            if stopped:
                break
            log_scene_start(index, context.scenes, scene)
            with open_scene(scene) as sim:
                summary = _collect_scene(context, sim, scene, trials, pairs, execute)
            summaries.append(summary)
            stopped = summary["shared_failure"]
            logger.info("场景结束 %s", scene["scene_id"], extra={"progress": True})
    finally:
        # 异常退出也保存已完成场景和完整计划。
        counts = _finish_pairs(context.root, pairs, stopped)
        write_summary(
            context,
            summaries,
            stopped,
            **({"calibration": calibration_summary} if calibration_summary is not None else {}),
            **counts,
        )
    return context.root
