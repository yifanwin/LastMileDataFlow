"""Station calibration, worker scheduling and measured success maps."""

import logging
import multiprocessing
import os
import time
from collections import Counter, deque
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty

from ..config import CollectionConfig
from ..data.evaluation.selection import (
    eligible_stations,
    evaluable,
    station_calibration,
)
from ..data.evaluation.statistics import aggregate
from ..data.visualization.heatmap import export_heatmap
from ..gpu import bind_device
from ..planning.navigation.astar import scene_grid, task_support_obstacles
from ..planning.pool import PlanningPool
from ..runtime.execution.curobo import run_curobo_attempt
from ..runtime.execution.protocol import (
    PlanningRequest,
    PlanningResponse,
    TrialBudget,
    TrialIdentity,
)
from ..runtime.execution.workers import (
    execution_worker,
    initialize_preparation_worker,
    prepare_job,
    record_unexecuted,
)
from ..runtime.execution_config import ExecutionConfig
from ..runtime.simulation import Simulation
from ..scenes.tasks.discovery import grasp_candidates
from ..utils.io import append_row, write_json
from ..utils.logging_config import Progress, trial_logging


def calibration_recording(collection, mode):
    visual = mode == "visual"
    return replace(
        collection,
        record_images=visual,
        record_video=visual and collection.record_video,
    )


@dataclass(frozen=True)
class RunResources:
    gpu_ids: tuple[int, ...]
    cpu_workers: int = 60
    preparation_workers: int = 12
    recording_mode: str = "numerical"
    planning_workers_per_gpu: int | list[int] | tuple[int, ...] = 1

    def __post_init__(self):
        if (
            not self.gpu_ids
            or len(set(self.gpu_ids)) != len(self.gpu_ids)
            or any(g < 0 for g in self.gpu_ids)
        ):
            raise ValueError("select one or more distinct GPU indices")
        if not 1 <= self.cpu_workers <= max(1, os.cpu_count() or 1):
            raise ValueError(
                f"cpu_workers must be between 1 and {max(1, os.cpu_count() or 1)}"
            )
        if not 1 <= self.preparation_workers <= min(64, max(1, os.cpu_count() or 1)):
            raise ValueError(
                f"preparation_workers must be between 1 and {min(64, max(1, os.cpu_count() or 1))}"
            )
        values = (
            self.planning_workers_per_gpu
            if isinstance(self.planning_workers_per_gpu, (list, tuple))
            else [self.planning_workers_per_gpu]
        )
        if len(values) == 1:
            values = list(values) * len(self.gpu_ids)
        if len(values) != len(self.gpu_ids) or any(
            not isinstance(value, int) or not 1 <= value <= 4 for value in values
        ):
            raise ValueError(
                "planning workers per GPU must be one value or one 1-4 value per GPU"
            )
        object.__setattr__(self, "planning_workers_per_gpu", tuple(values))
        if self.recording_mode not in ("numerical", "visual"):
            raise ValueError("recording_mode must be numerical or visual")


logger = logging.getLogger(__name__)


def _trial_record(result, scene, station, trial, seed):
    """提取执行结果，补齐场景、站位、试验序号和随机种子。"""
    record = {
        key: result.get(key)
        for key in (
            "status",
            "reason",
            "execution_termination",
            "task_outcome",
            "recording_complete",
            "replay_complete",
            "observation_complete",
            "executed_steps",
            "navigation",
            "error",
            "failure_scope",
            "planning_ref",
            "attribution",
            "retry_history",
            "exception_detail",
        )
    }
    record.update(
        scene_id=scene["scene_id"],
        station_id=station["station_id"],
        trial=trial,
        seed=seed,
        rollout_id=result.get("rollout_id"),
        task_id=scene["task"]["task_id"],
    )
    return record


def _execute(
    baseline,
    scene,
    station,
    assets_dir,
    config,
    collection,
    root,
    seed,
    policy=None,
    path=None,
    goal=None,
    *,
    attempt_ids,
    prepared=None,
):
    # 分配本次运行内唯一的试验编号。
    """调用指定后端执行一次试验，共用试验编号和记录链路。"""
    attempt_id = f"r{next(attempt_ids):06d}"
    task = scene["task"]
    logger.debug(
        "试验开始 %s scene=%s station=%s backend=%s seed=%s",
        attempt_id,
        scene["scene_id"],
        station["station_id"],
        config.operation_backend,
        seed,
    )
    started = time.monotonic()
    # 按后端选择执行器，导航路径和目标站位沿用同一接口。
    result = run_curobo_attempt(
        baseline,
        task,
        station,
        grasp_candidates(task, assets_dir),
        assets_dir,
        config,
        collection,
        root=root,
        attempt_id=attempt_id,
        seed=seed,
        path=path,
        goal=goal,
        prepared=prepared,
    )
    # 根据执行状态选择日志级别，不改变原始结果。
    log_result = logger.debug
    if result.get("status") in (
        "initialization_error",
        "episode_error",
        "recording_error",
        "simulation_failure",
        "infrastructure_error",
    ):
        log_result = logger.error
    elif result.get("status") == "timeout":
        log_result = logger.warning
    log_result(
        "试验结束 %s status=%s reason=%s steps=%s recording_complete=%s elapsed=%.1fs",
        attempt_id,
        result.get("status"),
        result.get("reason"),
        result.get("executed_steps"),
        result.get("recording_complete"),
        time.monotonic() - started,
    )
    return result


def _station_records(
    baseline,
    scene,
    index,
    trial_count,
    assets_dir,
    config,
    collection,
    root,
    policy=None,
    *,
    attempt_ids,
    stop=None,
):
    station = scene["stations"][index]
    prepared = None
    if (
        getattr(config, "shared_station_preparation", False)
        and config.operation_backend == "curobo"
    ):
        from ..runtime.execution.lifecycle import classify_episode_exception
        from ..runtime.execution.preparation import prepare_station

        directory = (
            Path(root) / "preparations" / scene["scene_id"] / station["station_id"]
        )
        try:
            prepared = prepare_station(baseline, station, config, directory)
        except Exception as exc:
            failure = classify_episode_exception(exc, "initialize")
            write_json(directory / "preparation.json", failure)
            if failure["failure_scope"] == "shared":
                raise
            return  # Preparation failure is one station result, never three trial failures.
    for trial in range(trial_count):
        if stop is not None and stop.is_set():
            break
        seed = config.seed + index * config.trials_per_station + trial
        label = f"scene={scene['scene_id']} station={station['station_id']} trial={trial + 1}/{config.trials_per_station}"
        with trial_logging(label, Progress(logger)):
            result = _execute(
                baseline,
                scene,
                station,
                assets_dir,
                config,
                collection,
                root,
                seed,
                policy,
                attempt_ids=attempt_ids,
                prepared=prepared,
            )
        record = _trial_record(result, scene, station, trial, seed)
        record["station_index"] = index
        if _shared_failure(record) and stop is not None:
            stop.set()
        yield record
        if _shared_failure(record):
            break


def _station_worker(
    worker,
    gpu,
    egl_device,
    requests,
    results,
    stop,
    scene,
    assets_dir,
    config,
    collection,
    root,
):
    from ..utils.logging_config import setup_logging

    setup_logging()
    bind_device(gpu, egl_device)
    sim = None
    try:
        sim = Simulation.from_snapshot(
            scene["scene_path"],
            scene["robot_config"],
            target=scene["task"]["target_body"],
        )
        sim.scene_id = scene["scene_id"]
        while True:
            job = requests.get()
            if job is None:
                break
            index, ids = job
            for record in _station_records(
                sim,
                scene,
                index,
                len(ids),
                assets_dir,
                config,
                collection,
                root,
                attempt_ids=iter(ids),
                stop=stop,
            ):
                results.put(("trial", worker, record))
            results.put(("done", worker, None))
    except Exception as exc:
        stop.set()
        results.put(("error", worker, f"{type(exc).__name__}: {exc}"))
    finally:
        if sim is not None:
            sim.close()


def _parallel_station_records(
    scene,
    assets_dir,
    config,
    collection,
    root,
    planned,
    *,
    attempt_ids,
    workers,
    gpu_ids,
    egl_devices,
):
    """Bounded station queue; only the parent writes the canonical trials.jsonl."""
    jobs = []
    for index in range(len(scene["stations"])):
        size = min(
            config.trials_per_station, planned - index * config.trials_per_station
        )
        if size <= 0:
            break
        jobs.append((index, [next(attempt_ids) for _ in range(size)]))
    if not jobs:
        return
    ctx = multiprocessing.get_context("spawn")
    stop, results = ctx.Event(), ctx.Queue()
    processes, queues = [], []
    pending = iter(jobs)
    active = set()
    failure = None
    try:
        for worker in range(min(workers, len(jobs))):
            requests = ctx.Queue()
            queues.append(requests)
            slot = worker % len(gpu_ids)
            process = ctx.Process(
                target=_station_worker,
                args=(
                    worker,
                    gpu_ids[slot],
                    egl_devices[slot],
                    requests,
                    results,
                    stop,
                    scene,
                    assets_dir,
                    config,
                    collection,
                    root,
                ),
            )
            process.start()
            processes.append(process)
            requests.put(next(pending))
            active.add(worker)
        while active:
            try:
                kind, worker, payload = results.get(timeout=1.0)
            except Empty:
                for worker in list(active):
                    code = processes[worker].exitcode
                    if code is not None:
                        stop.set()
                        active.remove(worker)
                        failure = failure or f"station worker exited with code {code}"
                continue
            if kind == "error":
                stop.set()
                active.remove(worker)
                failure = failure or f"station worker {worker}: {payload}"
            elif kind == "trial":
                yield payload
            else:
                active.remove(worker)
                job = next(pending, None) if not stop.is_set() else None
                if job is not None:
                    queues[worker].put(job)
                    active.add(worker)
    finally:
        stop.set()
        for requests in queues:
            requests.put(None)
        for process in processes:
            process.join()
        for queue in [*queues, results]:
            queue.close()
            queue.join_thread()
    if failure:
        raise RuntimeError(failure)
    for process in processes:
        if process.exitcode != 0:
            raise RuntimeError(f"station worker exited with code {process.exitcode}")


def _station_trials(
    baseline,
    scene,
    assets_dir,
    config,
    collection,
    root,
    policy=None,
    budget=None,
    *,
    attempt_ids,
    workers=1,
    gpu_ids=None,
    egl_devices=None,
):
    """逐站位重复标定，达到预算或发生共享资源故障时停止。"""
    trials = []
    total = len(scene["stations"]) * config.trials_per_station
    planned = total if budget is None else min(total, budget)
    started = time.monotonic()
    progress = Progress(logger)
    outcomes, valid_points, reasons = Counter(), Counter(), Counter()

    def serial():
        for index in range(len(scene["stations"])):
            size = min(config.trials_per_station, planned - len(trials))
            if size <= 0:
                break
            yield from _station_records(
                baseline,
                scene,
                index,
                size,
                assets_dir,
                config,
                collection,
                root,
                policy,
                attempt_ids=attempt_ids,
            )

    records = (
        serial()
        if workers == 1
        else _parallel_station_records(
            scene,
            assets_dir,
            config,
            collection,
            root,
            planned,
            attempt_ids=attempt_ids,
            workers=workers,
            gpu_ids=gpu_ids,
            egl_devices=egl_devices,
        )
    )
    try:
        for record in records:
            append_row(root / "trials.jsonl", record)
            trials.append(record)
            outcomes[record["status"]] += 1
            if evaluable(record):
                valid_points[record["station_id"]] += 1
            if record["status"] != "success":
                reasons[record["reason"]] += 1
            progress.update(
                "scene=%s trial=%s/%s 当前站位=%s 重复=%s/%s 成功=%s 耗时=%.1fs",
                scene["scene_id"],
                len(trials),
                planned,
                record["station_id"],
                record["trial"] + 1,
                config.trials_per_station,
                outcomes["success"],
                time.monotonic() - started,
            )
            if _shared_failure(record) and workers == 1:
                break
    finally:
        records.close()
        # 试验记录数不等于有效试验数；仅有效次数齐全的站位计为有效完成。
        valid = sum(valid_points.values())
        progress.update(
            "标定收尾 scene=%s 已处理站位=%s/%s 有效完成站位=%s/%s trial=%s/%s 完整计划=%s "
            "有效=%s 成功=%s 异常或未知=%s 原因=%s 耗时=%.1fs",
            scene["scene_id"],
            len(trials) // config.trials_per_station,
            len(scene["stations"]),
            sum(n == config.trials_per_station for n in valid_points.values()),
            len(scene["stations"]),
            len(trials),
            planned,
            total,
            valid,
            outcomes["success"],
            len(trials) - valid,
            reasons.most_common(5),
            time.monotonic() - started,
            force=True,
        )
    return trials


def _shared_failure(record):
    return (
        record.get("failure_scope") == "shared"
        or record.get("status") == "infrastructure_error"
    )


def _station_summary(sim, scene, trials, config, root):
    """汇总原始标定结果、导出展示热图，并筛选合格的 S0/S1 配对。"""
    binding = {"scene_id": scene["scene_id"], "task_id": scene["task"]["task_id"]}
    rates = station_calibration(
        scene["stations"], trials, config.trials_per_station, binding, config
    )
    if not rates["complete"]:
        logger.warning("scene=%s 标定不完整，不能用于正式困难配对", scene["scene_id"])
    # 热图仅用于展示；困难配对仍由原始标定结果决定。直接标定可关闭以节省时间。
    if config.export_figures:
        grid = scene_grid(
            sim,
            scene["task"]["anchor_world"],
            config.radius_m,
            config.map_resolution_m,
            0.0,
            0.0,
            support_obstacles=task_support_obstacles(sim, scene["task"]),
        )
        display = [{**row, "attempt": row.get("rollout_id")} for row in trials]
        export_heatmap(
            root / "figures" / scene["scene_id"],
            grid,
            aggregate(scene["stations"], display, config.trials_per_station),
            scene["task"]["anchor_world"],
            config.smoothing_sigma_m or config.spacing_m or 0.1,
            title=scene["scene_id"],
        )
    starts, goals = eligible_stations(scene["stations"], rates, config)
    pair_count = len(starts) * len(goals)
    logger.info(
        "scene=%s 标定 trial=%s complete=%s 合格配对=%s 热图=%s",
        scene["scene_id"],
        len(trials),
        rates["complete"],
        pair_count,
        root / "figures" / scene["scene_id"] if config.export_figures else "disabled",
    )
    summary = {
        "scene_id": scene["scene_id"],
        "calibration_complete": rates["complete"],
        "eligible_pairs": pair_count,
        "successes": sum(r["successes"] for r in rates["stations"]),
    }
    return summary


def _evaluate_scene(
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
    station_options=None,
):
    """完成单个场景的站位标定和汇总，不执行连续采集。"""
    trials = (
        [r for r in prior if r["scene_id"] == scene["scene_id"]]
        if prior is not None
        else _station_trials(
            sim,
            scene,
            assets_dir,
            config,
            collection,
            root,
            policy,
            max_trials,
            attempt_ids=attempt_ids,
            **(station_options or {}),
        )
    )
    summary = _station_summary(sim, scene, trials, config, root)
    summary["shared_failure"] = any(_shared_failure(row) for row in trials)
    return summary


def evaluate_stations(
    dataset,
    assets_dir,
    config,
    collection,
    output_dir,
    *,
    run_id,
    gpu_ids,
    samples=None,
    max_trials=None,
    station_workers=1,
    egl_devices=None,
    cpu_workers=60,
    preparation_workers=12,
    recording_mode="numerical",
    planning_workers_per_gpu=1,
):
    """执行逐站位试验并导出热图，不执行 S0→S1 连续采集。"""
    if config.operation_backend != "curobo":
        raise ValueError("station calibration requires cuRobo")
    from .collect import _run_dataset

    return _run_dataset(
        dataset,
        assets_dir,
        config,
        collection,
        output_dir,
        _evaluate_scene,
        run_id=run_id,
        gpu_ids=gpu_ids,
        samples=samples,
        max_trials=max_trials,
        station_workers=station_workers,
        egl_devices=egl_devices,
        cpu_workers=cpu_workers,
        preparation_workers=preparation_workers,
        recording_mode=recording_mode,
        planning_workers_per_gpu=planning_workers_per_gpu,
    )


@dataclass
class CalibrationInputs:
    scenes: list[dict]
    assets_dir: str | Path
    config: ExecutionConfig
    collection: CollectionConfig
    root: Path
    resources: RunResources
    attempt_ids: Iterator[int]
    max_trials: int | None = None


class _CalibrationScheduler:
    def __init__(self, inputs, service_type, trial_worker):
        self.inputs = inputs
        self.started = time.monotonic()
        self.context = multiprocessing.get_context("spawn")
        self.planned = sum(
            (
                min(
                    len(s["stations"]) * self.inputs.config.trials_per_station,
                    self.inputs.max_trials
                    if self.inputs.max_trials is not None
                    else len(s["stations"]) * self.inputs.config.trials_per_station,
                )
                for s in self.inputs.scenes
            )
        )
        self.cpu_count = min(self.inputs.resources.cpu_workers, self.planned)
        self.original_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
        self.stop = self.context.Event()
        (self.requests, self.results) = (self.context.Queue(), self.context.Queue())
        self.jobs = [self.context.Queue() for _ in range(self.cpu_count)]
        self.replies = [self.context.Queue() for _ in range(self.cpu_count)]
        self.cpus = [
            self.context.Process(
                target=trial_worker,
                args=(
                    index,
                    self.jobs[index],
                    self.requests,
                    self.replies[index],
                    self.results,
                    self.stop,
                    self.inputs.config,
                    self.inputs.collection,
                    self.inputs.assets_dir,
                    self.inputs.root,
                ),
            )
            for index in range(self.cpu_count)
        ]
        self.prep = ProcessPoolExecutor(
            max_workers=self.inputs.resources.preparation_workers,
            mp_context=self.context,
            initializer=initialize_preparation_worker,
        )
        self.pool = PlanningPool(
            self.inputs.resources.gpu_ids,
            workers_per_gpu=self.inputs.resources.planning_workers_per_gpu,
            **{"service_type": service_type} if service_type else {},
        )
        for process in self.cpus:
            process.start()
        (self.preparations, self.batches, self.recordings, self.routes) = (
            {},
            {},
            {},
            {},
        )
        (self.ready, self.active, self.idle) = (
            deque(),
            {},
            deque(range(self.cpu_count)),
        )
        (self.trials, self.preparation_failures) = ([], [])
        self.failure = None
        self.peaks = {"cpu_trials": 0, "gpu_queries": 0, "preparations": 0}
        self.gpu_active_s = 0.0
        self.units = iter(self._stations())
        self.exhausted = False
        self.capacity = (
            self.inputs.resources.cpu_workers
            + self.inputs.config.trials_per_station * len(self.inputs.resources.gpu_ids)
        )

    def _stations(self):
        for scene in self.inputs.scenes:
            remaining = min(
                len(scene["stations"]) * self.inputs.config.trials_per_station,
                self.inputs.max_trials
                if self.inputs.max_trials is not None
                else len(scene["stations"]) * self.inputs.config.trials_per_station,
            )
            for index in range(len(scene["stations"])):
                size = min(self.inputs.config.trials_per_station, remaining)
                if size <= 0:
                    break
                identities = [
                    TrialIdentity(
                        scene["scene_id"],
                        index,
                        trial,
                        f"r{next(self.inputs.attempt_ids):06d}",
                        self.inputs.config.seed
                        + index * self.inputs.config.trials_per_station
                        + trial,
                    )
                    for trial in range(size)
                ]
                yield (
                    scene,
                    index,
                    identities,
                    [
                        TrialBudget(self.inputs.config.attempt_timeout_s)
                        for _ in identities
                    ],
                )
                remaining -= size

    def _save(self, scene, index, identity, result):
        record = _trial_record(
            result, scene, scene["stations"][index], identity.trial, identity.seed
        )
        record["station_index"] = index
        append_row(Path(self.inputs.root) / "trials.jsonl", record)
        self.trials.append(record)
        if _shared_failure(record):
            self.failure = result
            self.stop.set()

    def _receive_cpu(self, message):
        (worker, scene, index, identity, result) = message
        self.active.pop(worker)
        self.idle.append(worker)
        self._save(scene, index, identity, result)

    def _record_later(self, scene, index, prepared, response, budget):
        future = self.prep.submit(
            record_unexecuted,
            scene,
            index,
            prepared,
            response,
            budget,
            self.inputs.config,
            self.inputs.collection,
            self.inputs.root,
        )
        self.recordings[future] = (scene, index, response.identity)

    def _prepare_completed(self):
        for future in list(self.preparations):
            if not future.done():
                continue
            (scene, index, identities, budgets) = self.preparations.pop(future)
            result = future.result()
            if result["status"] != "ready":
                self.preparation_failures.append(
                    {"scene_id": scene["scene_id"], "station_index": index, **result}
                )
                if result.get("failure_scope") == "shared":
                    self.failure = result
                    self.stop.set()
                continue
            prepared = result["prepared"]
            candidates = grasp_candidates(scene["task"], self.inputs.assets_dir)
            query_id = f"{identities[0].attempt_id}-batch"
            request = PlanningRequest(
                query_id,
                scene["scene_path"],
                identities,
                prepared.state,
                "preclose",
                budgets,
                {
                    "task": scene["task"],
                    "robot": scene["robot_config"],
                    "config": self.inputs.config,
                    "assets_dir": self.inputs.assets_dir,
                    "root": self.inputs.root,
                    "candidates": candidates,
                    "preparation_path": prepared.path,
                },
            )
            self.batches[query_id] = (scene, index, prepared, budgets, candidates)
            self.pool.submit(request)

    def _record_completed(self):
        for future in list(self.recordings):
            if future.done():
                (scene, index, identity) = self.recordings.pop(future)
                self._save(scene, index, identity, future.result())

    def _route_queries(self):
        while True:
            try:
                (worker, request) = self.requests.get_nowait()
            except Empty:
                break
            self.routes[request.query_id] = worker
            self.pool.submit(request)

    def _plans_completed(self):
        while True:
            completed = self.pool.poll()
            if completed is None:
                break
            (request, responses) = completed
            self.gpu_active_s += responses[0].planning_s
            if request.stage == "pose":
                self.replies[self.routes.pop(request.query_id)].put(responses[0])
            else:
                (scene, index, prepared, budgets, candidates) = self.batches.pop(
                    request.query_id
                )
                for response, budget in zip(responses, budgets):
                    if response.status == "success":
                        self.ready.append(
                            (scene, index, prepared, response, budget, candidates)
                        )
                    else:
                        self._record_later(scene, index, prepared, response, budget)
        if self.pool.failure:
            self.failure = self.pool.failure
            self.stop.set()

    def _trials_completed(self):
        while True:
            try:
                self._receive_cpu(self.results.get_nowait())
            except Empty:
                break

    def _check_workers(self):
        for worker in list(self.active):
            if self.cpus[worker].exitcode is not None:
                self.failure = {
                    "status": "infrastructure_error",
                    "failure_scope": "shared",
                    "reason": f"CPU worker {worker} exited with code {self.cpus[worker].exitcode}",
                }
                (scene, index, _, response, _, _) = self.active.pop(worker)
                self._save(
                    scene,
                    index,
                    response.identity,
                    {
                        **self.failure,
                        "rollout_id": response.identity.attempt_id,
                        "recording_complete": False,
                    },
                )

    def _dispatch_trials(self):
        while self.idle and self.ready:
            worker = self.idle.popleft()
            self.active[worker] = self.ready.popleft()
            self.jobs[worker].put(self.active[worker])

    def _submit_preparations(self):
        buffered = (
            len(self.active)
            + len(self.ready)
            + len(self.recordings)
            + sum((len(job[2]) for job in self.preparations.values()))
            + sum((len(job[3]) for job in self.batches.values()))
        )
        while (
            not self.exhausted
            and len(self.preparations) < self.inputs.resources.preparation_workers
            and (buffered + self.inputs.config.trials_per_station <= self.capacity)
        ):
            job = next(self.units, None)
            if job is None:
                self.exhausted = True
                break
            (scene, index, identities, budgets) = job
            future = self.prep.submit(
                prepare_job, scene, index, self.inputs.config, self.inputs.root
            )
            self.preparations[future] = job
            buffered += len(identities)

    def _dispatch_planning(self):
        self.pool.dispatch()
        self.peaks["cpu_trials"] = max(self.peaks["cpu_trials"], len(self.active))
        self.peaks["gpu_queries"] = max(
            self.peaks["gpu_queries"], len(self.pool.active)
        )
        self.peaks["preparations"] = max(
            self.peaks["preparations"], len(self.preparations)
        )

    def run(self):
        try:
            while True:
                self._prepare_completed()
                self._record_completed()
                self._route_queries()
                self._plans_completed()
                self._trials_completed()
                if self.stop.is_set():
                    break
                self._check_workers()
                if self.failure:
                    break
                self._dispatch_trials()
                self._submit_preparations()
                self._dispatch_planning()
                if self.exhausted and (
                    not (
                        self.preparations
                        or self.batches
                        or self.recordings
                        or self.ready
                        or self.active
                    )
                ):
                    break
                try:
                    self._receive_cpu(self.results.get(timeout=0.01))
                except Empty:
                    pass
        finally:
            self.close()
        return (
            self.trials,
            {
                "planned_trials": self.planned,
                "recorded_trials": len(self.trials),
                "preparation_failures": self.preparation_failures,
                "failure": self.failure,
                "peaks": self.peaks,
                "gpu_active_s": self.gpu_active_s,
                "elapsed_s": time.monotonic() - self.started,
            },
        )

    def close(self):
        self.stop.set()
        if self.failure or self.active or self.pool.active:
            self.failure = self.failure or {
                "status": "infrastructure_error",
                "failure_scope": "shared",
                "reason": "calibration interrupted",
            }
            self.pool.failure = self.failure
            for worker, job in self.active.items():
                identity = job[3].identity
                self.replies[worker].put(
                    PlanningResponse(
                        f"{identity.attempt_id}-stop",
                        identity,
                        "infrastructure_error",
                        0.0,
                        job[4].plan_count,
                        diagnostics=self.failure,
                    )
                )
            while self.ready:
                (scene, index, prepared, response, budget, _) = self.ready.popleft()
                self._record_later(
                    scene,
                    index,
                    prepared,
                    replace(
                        response,
                        status="infrastructure_error",
                        diagnostics=self.failure,
                    ),
                    budget,
                )
        while self.active:
            try:
                self._receive_cpu(self.results.get(timeout=0.1))
            except Empty:
                for worker in list(self.active):
                    if self.cpus[worker].exitcode is not None:
                        (scene, index, _, response, _, _) = self.active.pop(worker)
                        self._save(
                            scene,
                            index,
                            response.identity,
                            {
                                "status": "infrastructure_error",
                                "failure_scope": "shared",
                                "reason": "execution worker exited",
                                "rollout_id": response.identity.attempt_id,
                                "recording_complete": False,
                            },
                        )
        for future, (scene, index, identity) in self.recordings.items():
            self._save(scene, index, identity, future.result())
        self.prep.shutdown(wait=True, cancel_futures=bool(self.failure))
        for queue in self.jobs:
            queue.put(None)
        if self.failure:
            while any((process.is_alive() for process in self.cpus)):
                try:
                    self.requests.get(timeout=0.1)
                except Empty:
                    pass
        for process in self.cpus:
            process.join()
        self.pool.close()
        for queue in [self.requests, self.results, *self.jobs, *self.replies]:
            if self.failure:
                queue.cancel_join_thread()
            queue.close()
            if not self.failure:
                queue.join_thread()
        if self.original_cuda is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = self.original_cuda


def calibrate_stations(
    scenes,
    assets_dir,
    config,
    collection,
    root,
    resources,
    *,
    attempt_ids,
    max_trials=None,
    service_type=None,
    trial_worker=execution_worker,
):
    inputs = CalibrationInputs(
        scenes, assets_dir, config, collection, root, resources, attempt_ids, max_trials
    )
    return _CalibrationScheduler(inputs, service_type, trial_worker).run()
