"""Build natural and obstacle-edited scenes with shared task preparation.

House workers reuse their read-only source model. Only the parent writes scenes.jsonl;
actual stepping and edits remain in the shared runtime construction session.
"""

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing, contextmanager
from dataclasses import asdict
from functools import partial
import logging
import multiprocessing
import os
from pathlib import Path
import signal
import time

import numpy as np

from ..gpu import bind_device
from ..config import load_robot_config
from ..runtime.simulation import Simulation
from ..data.visualization.scene_preview import export_scene_previews
from ..scenes.edits.search import BuildContext, build_task
from ..scenes.edits.placement import support_regions
from ..scenes.dataset import load_scene_recipe
from ..scenes.loading.source import SceneSource
from ..scenes.stations.initialization import inside_triangles, room_triangles
from ..scenes.stations.sampling import disk_samples
from ..scenes.tasks.discovery import discover_tasks, grasp_candidates
from ..planning.navigation.astar import task_support_obstacles
from ..utils.io import append_row, create_run, read_json, rows, write_json
from ..utils.logging_config import Progress

logger = logging.getLogger(__name__)


def _target_distance(left, right):
    """按世界坐标 XY 距离选目标，不把支撑面的高度差算作空间分散。"""
    return float(np.linalg.norm(np.asarray(left["task"]["anchor_world"][:2]) - right["task"]["anchor_world"][:2]))


def _spread_key(rooms, supports, candidate):
    row, distance = candidate
    return (
        row["task"]["room_id"] not in rooms,
        row["support_id"] not in supports,
        distance,
        row["support_area_m2"],
        row["prefilter_count"],
        row["task"]["task_id"],
    )


def spatial_select(candidates, count):
    """优先覆盖不同房间和支撑面，再让目标间的最小距离尽量大。

    ID 排序和评分末项使并列结果可复现。count=-1 表示全选，此时距离只影响顺序。
    """
    if not candidates:
        return []
    if count == -1:
        count = len(candidates)
    remaining = sorted(candidates, key=lambda row: row["task"]["task_id"])
    first = max(remaining, key=lambda row: (row["support_area_m2"], row["prefilter_count"], row["task"]["task_id"]))
    chosen = [first]
    remaining.remove(first)
    while remaining and len(chosen) < count:
        distances = [(row, min(_target_distance(row, selected) for selected in chosen)) for row in remaining]
        rooms = {row["task"]["room_id"] for row in chosen}
        supports = {row["support_id"] for row in chosen}
        best, _ = max(distances, key=partial(_spread_key, rooms, supports))
        chosen.append(best)
        remaining = [row for row, _ in distances if row is not best]
    return chosen


def _physical_rejection(sim, task, recipe, floor):
    """Return (reason, None) or (None, candidate) from grasps, support surfaces and source floor."""
    if not grasp_candidates(task, recipe.assets_dir, max_rows=16, allow_fallback=False):
        return "missing_valid_data_grasps", None
    surfaces = []
    if task["operation"] == "pick":
        surfaces = support_regions(sim, {**task, "support_obstacles": task_support_obstacles(sim, task)})
        if not surfaces:
            return "missing_valid_support", None
    coarse = [
        station
        for station in disk_samples(task["anchor_world"], 1.0, 0.25)
        if len(floor) and inside_triangles(np.asarray(station["xy"]), floor)
    ]
    if not coarse:
        return "no_source_floor_in_sampling_disk", None
    area = max(((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]) for r in surfaces), default=0.0)
    return None, {
        "task": task,
        "support_id": surfaces[0].support if surfaces else task["target_body"],
        "support_area_m2": area,
        "prefilter_count": len(coarse),
    }


def _select_tasks(sim, recipe, house):
    tasks, skipped = discover_tasks(sim, sim.source_metadata, recipe.geometry)
    floor = room_triangles(sim)
    candidates = []
    for task in tasks:
        if not recipe.selects(task, house):
            continue
        reason = recipe.rejection(task, house)
        if reason is None:
            reason, candidate = _physical_rejection(sim, task, recipe, floor)
        if reason is not None:
            skipped.append({"task_id": task["task_id"], "reason": reason})
            continue
        candidates.append(candidate)
    selected = []
    for operation in recipe.operations:
        count = getattr(recipe, f"max_{operation}_targets_per_house")
        selected.extend(spatial_select([row for row in candidates if row["task"]["operation"] == operation], count))
    return [row["task"] for row in selected], {
        "house": house,
        "discovered_task_count": len(tasks),
        "eligible_task_count": len(candidates),
        "selected_task_ids": [row["task"]["task_id"] for row in selected],
        "exclusions": skipped,
        "prefilter_scope": "source_floor_and_grasp_support_only_not_station_rollout",
    }


def _rejection(house, task, intent, reason, detail):
    return {
        "house": house,
        "task_id": task["task_id"],
        "proposal_intent": intent,
        "status": "rejected",
        "reason": reason,
        "detail": detail,
    }


class _TaskTimeout(Exception):
    pass


def _raise_task_timeout(seconds, signum, frame):
    raise _TaskTimeout(f"task exceeded {seconds:g}s")


@contextmanager
def _task_deadline(seconds):
    if not seconds:
        yield
        return
    previous = signal.signal(signal.SIGALRM, partial(_raise_task_timeout, seconds))
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _build_task(sim, task, task_index, recipe, house, root, baseline_cache):
    """Records and the house-report entry of one task (preparation, edits, timeout)."""
    logger.info("构建 house=%s task=%s 共享准备", house, task["task_id"])
    try:
        with _task_deadline(recipe.task_timeout_s):
            try:
                ctx = BuildContext.prepare(sim, task, recipe)
            except ValueError as exc:
                intents = ("no_edit", "edit") if recipe.edits.enabled else ("no_edit",)
                records = [_rejection(house, task, intent, "task_preparation_failed", str(exc)) for intent in intents]
                return records, {"task_id": task["task_id"], "status": "preparation_rejected", "reason": str(exc)}
            with ctx:
                records = list(
                    build_task(
                        ctx,
                        root,
                        house=house,
                        seed=recipe.seed + house * 10000 + task_index,
                        baseline_cache=baseline_cache,
                    )
                )
                return records, {
                    "task_id": task["task_id"],
                    "preparation_s": ctx.timings,
                    "grasp_evidence": ctx.grasp_evidence,
                    "asset_compiles": ctx.assets.compile_count,
                    "station_initializations": sum(
                        r.get("build_metrics", {}).get("station_initializations", 0) for r in records
                    ),
                }
    except _TaskTimeout as exc:
        return (
            [_rejection(house, task, "no_edit", "task_timeout", str(exc))],
            {"task_id": task["task_id"], "status": "rejected", "reason": "task_timeout", "detail": str(exc)},
        )


def _open_house(recipe, house, robot):
    source = SceneSource.procthor(recipe.dataset_dir, house)
    snapshot = recipe.input_snapshots.get(str(house))
    sim = Simulation.from_snapshot(snapshot, robot) if snapshot else Simulation.from_source(source, robot)
    if sim.source.scene_id != source.scene_id:
        sim.close()
        raise ValueError("input snapshot source does not match requested house")
    if sim.source_metadata is None:
        sim.source_metadata = read_json(source.metadata_path)
    return sim


def _build_house(job):
    recipe, house, robot, root, gpu = job
    bind_device(gpu)
    os.environ.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    start = time.monotonic()
    records = []
    report = {"house": house, "dataset_split": recipe.house_splits[str(house)], "tasks": []}
    try:
        with closing(_open_house(recipe, house, robot)) as sim:
            tasks, discovery = _select_tasks(sim, recipe, house)
            report.update(discovery)
            report["status"] = "selected" if tasks else "no_eligible_tasks"
            baseline_cache = {}
            for task_index, task in enumerate(tasks):
                task_records, task_report = _build_task(sim, task, task_index, recipe, house, root, baseline_cache)
                records.extend(task_records)
                report["tasks"].append(task_report)
    except Exception as exc:
        # A worker boundary preserves finished records before the parent stops
        # on shared render/model/I/O faults. Ordinary candidates reject locally.
        report["error"] = {"type": type(exc).__name__, "reason": str(exc)}
    report["elapsed_s"] = time.monotonic() - start
    return {"records": records, "source": report}


def _save_house(result, root, summary):
    for record in result["records"]:
        append_row(root / "scenes.jsonl", record)
        summary[record["status"]] += 1
    summary["sources"].append(result["source"])


def _late_results(pending, summary):
    """Results of workers that finished while shutdown waited; canceled houses are never faked."""
    recorded = {source["house"] for source in summary["sources"]}
    for future in pending:
        if future.cancelled() or not future.done() or future.exception() is not None:
            continue
        result = future.result()
        if result["source"]["house"] not in recorded:
            recorded.add(result["source"]["house"])
            yield result


def _build_into_root(recipe, robot, root, gpu_ids):
    (root / "scenes.jsonl").touch()
    start = time.monotonic()
    workers = recipe.build_workers
    jobs = [(recipe, house, robot, root, gpu_ids[i % len(gpu_ids)]) for i, house in enumerate(recipe.houses)]
    summary = {
        "status": "running",
        "sources": [],
        "accepted": 0,
        "rejected": 0,
        "skipped": 0,
        "isolated_errors": 0,
        "build_workers": workers,
        "gpu_ids": list(gpu_ids),
    }
    progress = Progress(logger)
    error, pool, pending, finished = None, None, [], False
    try:
        if workers > 1:
            pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
            pending = [pool.submit(_build_house, job) for job in jobs]
            results = (future.result() for future in as_completed(pending))
        else:
            results = map(_build_house, jobs)
        for result in results:
            _save_house(result, root, summary)
            progress.update(
                "场景准备 来源=%s/%s 接受=%s 拒绝=%s 跳过=%s",
                len(summary["sources"]),
                len(jobs),
                summary["accepted"],
                summary["rejected"],
                summary["skipped"],
            )
            error = result["source"].get("error")
            if error:
                break
        finished = True
    finally:
        if pool:
            for future in pending:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
            for result in _late_results(pending, summary):
                _save_house(result, root, summary)
        summary.update(
            status="completed" if finished and not error else "infrastructure_error",
            wall_elapsed_s=time.monotonic() - start,
        )
        if error:
            summary["error"] = error
        summary["intent_counts"] = dict(Counter(row["proposal_intent"] for row in rows(root / "scenes.jsonl")))
        write_json(root / "summary.json", summary)
    if error:
        raise RuntimeError("scene build stopped: " + error["type"] + ":" + error["reason"])
    return root


def build_scenes(config, output, *, gpu_ids):
    recipe = load_scene_recipe(config)
    limit = min(64, max(1, os.cpu_count() or 1))
    if not 1 <= recipe.build_workers <= limit:
        raise ValueError(f"build workers must be between 1 and {limit}")
    robot = load_robot_config(recipe.robot_config)
    root = create_run(
        Path(output),
        {"recipe": asdict(recipe), "robot": asdict(robot), "geometry": asdict(recipe.geometry), "seed": recipe.seed},
    )
    _build_into_root(recipe, robot, root, gpu_ids)
    export_scene_previews(root)
    return root
