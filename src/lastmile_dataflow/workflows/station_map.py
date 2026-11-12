"""Station calibration: run preparation, the CPU/GPU trial scheduler and measured success maps."""

import logging
import multiprocessing
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from itertools import count
from pathlib import Path
from queue import Empty

from ..config import CollectionConfig, StationRun, load_local_config, robot_config
from ..data.evaluation.calibration import aggregate, eligible_stations, station_calibration
from ..data.visualization.heatmap import export_heatmap
from ..gpu import resolve_egl_devices
from ..planning.navigation.astar import scene_grid, task_support_obstacles
from ..planning.navigation.geometry import NAVIGATION_PROTOCOL, NavigationSettings
from ..planning.pool import PlanningPool, PlanningService, per_gpu_workers
from ..policies.config import check_profile, load_policy_config
from ..runtime.execution.protocol import (
    PlanningInputs,
    PlanningRequest,
    PlanningResponse,
    ReadyTrial,
    StationJob,
    TrialBudget,
    TrialIdentity,
)
from ..runtime.execution.workers import (
    execution_worker,
    initialize_preparation_worker,
    prepare_job,
    record_unexecuted,
)
from ..runtime.execution_config import ExecutionConfig, load_execution_config
from ..scenes.dataset import load_scenes, open_scene
from ..scenes.tasks.discovery import grasp_candidates
from ..utils.io import append_row, create_run, read_json, rows, write_json

logger = logging.getLogger(__name__)

# Selection and display parameters do not change physical execution, so calibrations stay reusable across them.
_SELECTION_KEYS = {"lastmile_low", "lastmile_high", "smoothing_sigma_m", "navigation_margin_m",
                   "navigation_clearance_weight", "navigation_max_penetration_m"}
# Sampling geometry of the scene library that overrides the execution config.
_LIBRARY_GEOMETRY = ("spacing_m", "width", "height", "min_target_pixels")
_TRIAL_FIELDS = ("status", "reason", "execution_termination", "task_outcome", "recording_complete",
                 "replay_complete", "observation_complete", "executed_steps", "navigation", "error",
                 "failure_scope", "planning_ref", "attribution", "retry_history", "exception_detail")


def execution_profile(robot, config):
    return {"robot": asdict(robot), "width": config.width, "height": config.height,
            "backend": config.operation_backend,
            "policy": asdict(load_policy_config(config.policy_config)) if config.policy_config else None}


def calibration_execution(config):
    """标定匹配执行条件，不把选点和展示参数当作物理执行条件。"""
    return {key: value for key, value in config.items() if key not in _SELECTION_KEYS}


@dataclass
class StationRunContext:
    """One prepared calibration or collection run: inputs, run directory and the shared attempt counter."""
    root: Path
    scenes: list[dict]
    config: ExecutionConfig
    collection: CollectionConfig
    run: StationRun
    assets_dir: str | Path
    gpu_ids: tuple[int, ...]
    prior: list[dict] | None = None
    attempt_ids: Iterator[int] = field(default_factory=count)
    started: float = field(default_factory=time.monotonic)

    @property
    def calibration_collection(self):
        return self.run.calibration_recording(self.collection)


def _resolve_scenes(dataset, config, samples):
    scenes = load_scenes(dataset, samples)
    if not scenes:
        raise ValueError("no accepted scenes")
    profiles = {}
    for scene in scenes:
        robot = robot_config(read_json(Path(scene["scene_path"]) / "config.json")["robot"])
        check_profile(robot, config.operation_backend, config.policy_config)
        scene["robot_config"] = robot
        profiles[scene["scene_id"]] = execution_profile(robot, config)
    return scenes, profiles


def _reused_trials(calibration, dataset, profiles, config):
    """Trials of an earlier calibration whose scenes, profiles and physical execution all match."""
    logger.info("复用已有标定=%s，不重复 rollout", calibration)
    saved = read_json(Path(calibration) / "config.json")
    if (Path(saved["dataset"]).resolve() != Path(dataset).resolve()
            or any(saved["profiles"].get(sid) != profile for sid, profile in profiles.items())
            or calibration_execution(saved["config"]) != calibration_execution(asdict(config))):
        raise ValueError("calibration scene/backend/robot/sensor/policy configuration mismatch")
    return rows(Path(calibration) / "trials.jsonl")


def prepare_run(dataset, run, output, gpu_ids, *, collect, samples=None, calibration=None):
    """读取执行配置、对齐场景、检查标定复用条件并创建运行目录。"""
    config = load_execution_config(run.execution)
    if config.operation_backend != "curobo":
        raise ValueError("station calibration and continuous collection use cuRobo")
    assets_dir = run.assets_dir or load_local_config().assets_dir
    if assets_dir is None:
        raise ValueError("pass --assets-dir or set assets_dir in configs/local.yaml")
    gpu_ids = tuple(gpu_ids)
    if not gpu_ids or len(set(gpu_ids)) != len(gpu_ids) or any(g < 0 for g in gpu_ids):
        raise ValueError("select one or more distinct GPU indices")
    output = Path(output)
    # 复用采样间距和观测尺寸；操作工作区和导航碰撞参数由执行配置决定。
    geometry = read_json(Path(dataset) / "config.json").get("geometry", {})
    config = replace(config, **{key: geometry[key] for key in _LIBRARY_GEOMETRY if key in geometry})
    collection = CollectionConfig(record_video=run.record_video, record_images=collect, width=config.width,
                                  height=config.height, seed=config.seed, output_dir=str(output.parent.resolve()))
    scenes, profiles = _resolve_scenes(dataset, config, samples)
    prior = _reused_trials(calibration, dataset, profiles, config) if calibration else None
    resources = {"gpu_ids": list(gpu_ids), "egl_devices": resolve_egl_devices(gpu_ids),
                 "cpu_workers": run.cpu_workers, "preparation_workers": run.preparation_workers,
                 "recording_mode": run.mode, "planning_workers_per_gpu": list(per_gpu_workers(gpu_ids, run.planning_workers_per_gpu))}
    # 保存本次运行实际使用的配置及输入引用。
    root = create_run(output, {
        "dataset": str(Path(dataset).resolve()),
        "config": asdict(config),
        "collection": asdict(collection),
        "navigation_protocol": NAVIGATION_PROTOCOL,
        "navigation_settings": asdict(NavigationSettings.from_config(
            config, speed=config.navigation_speed_m_s, yaw_speed=config.navigation_yaw_speed_rad_s)),
        "profiles": profiles,
        "gpu": gpu_ids[0],
        "seed": config.seed,
        "calibration": str(Path(calibration).resolve()) if calibration else None,
        "resources": resources,
        "calibration_collection": asdict(run.calibration_recording(collection)),
    }, prepared=True)
    logger.info("运行=%s scene数=%s backend=%s GPU=%s 每点重复=%s 每场景预算=%s 标定来源=%s",
                root, len(scenes), config.operation_backend, gpu_ids[0], config.trials_per_station,
                run.max_trials if run.max_trials is not None else "完整标定", calibration or "当前 run 内执行")
    return StationRunContext(root, scenes, config, collection, run, assets_dir, gpu_ids, prior)


def trial_record(result, scene, station, trial, seed):
    """提取执行结果，补齐场景、站位、试验序号和随机种子。"""
    record = {key: result.get(key) for key in _TRIAL_FIELDS}
    record.update(scene_id=scene["scene_id"], station_id=station["station_id"], trial=trial, seed=seed,
                  rollout_id=result.get("rollout_id"), task_id=scene["task"]["task_id"])
    return record


def shared_failure(record):
    return record.get("failure_scope") == "shared" or record.get("status") == "infrastructure_error"


def station_summary(sim, scene, trials, config, root):
    """汇总一个场景的原始标定结果并导出展示热图；困难配对仍由原始标定结果决定。"""
    binding = {"scene_id": scene["scene_id"], "task_id": scene["task"]["task_id"]}
    rates = station_calibration(scene["stations"], trials, config.trials_per_station, binding,
                                config.operation_backend)
    if not rates["complete"]:
        logger.warning("scene=%s 标定不完整，不能用于正式困难配对", scene["scene_id"])
    if config.export_figures:
        grid = scene_grid(sim, scene["task"]["anchor_world"], config.radius_m, config.map_resolution_m, 0.0, 0.0,
                          support_obstacles=task_support_obstacles(sim, scene["task"]))
        export_heatmap(root / "figures" / scene["scene_id"], grid,
                       aggregate(scene["stations"], trials, config.trials_per_station),
                       scene["task"]["anchor_world"], config.smoothing_sigma_m or config.spacing_m or 0.1,
                       title=scene["scene_id"])
    starts, goals = eligible_stations(scene["stations"], rates, config.lastmile_low, config.lastmile_high)
    pair_count = len(starts) * len(goals)
    logger.info("scene=%s 标定 trial=%s complete=%s 合格配对=%s 热图=%s", scene["scene_id"], len(trials),
                rates["complete"], pair_count,
                root / "figures" / scene["scene_id"] if config.export_figures else "disabled")
    return {"scene_id": scene["scene_id"], "calibration_complete": rates["complete"],
            "eligible_pairs": pair_count, "successes": sum(r["successes"] for r in rates["stations"])}


def scene_trials(trials, scene):
    return [row for row in trials if row["scene_id"] == scene["scene_id"]]


def log_scene_start(index, scenes, scene):
    logger.info("场景 %s/%s 开始 scene=%s target=%s station数=%s", index, len(scenes), scene["scene_id"],
                scene["task"].get("asset_id", scene["task"]["task_id"]), len(scene["stations"]),
                extra={"progress": True})


def write_summary(context, summaries, stopped, **fields):
    """运行完整（全部场景处理且无共享失败）不等于任务成功。"""
    elapsed = time.monotonic() - context.started
    complete = len(summaries) == len(context.scenes) and not stopped
    write_json(context.root / "summary.json", {"status": "completed" if complete else "incomplete",
                                               "scenes": summaries, "elapsed_s": elapsed, **fields})
    logger.info("运行收尾 输出=%s 已处理场景=%s/%s 耗时=%.1fs", context.root, len(summaries),
                len(context.scenes), elapsed)
    if not complete:
        logger.warning("运行未完整结束：已处理场景=%s/%s", len(summaries), len(context.scenes))


def run_station_map(dataset, run, output, *, gpu_ids, samples=None):
    """逐站位标定并导出热图，不执行 S0→S1 连续采集。"""
    context = prepare_run(dataset, run, output, gpu_ids, collect=False, samples=samples)
    summaries, calibration = [], None
    try:
        trials, calibration = calibrate_stations(context)
        for index, scene in enumerate(context.scenes, 1):
            log_scene_start(index, context.scenes, scene)
            current = scene_trials(trials, scene)
            with open_scene(scene) as sim:
                summary = station_summary(sim, scene, current, context.config, context.root)
            summary["shared_failure"] = any(shared_failure(row) for row in current)
            summaries.append(summary)
            logger.info("场景结束 %s", scene["scene_id"], extra={"progress": True})
    finally:
        stopped = calibration is None or bool(calibration["failure"])
        stopped = stopped or any(summary["shared_failure"] for summary in summaries)
        write_summary(context, summaries, stopped,
                      **({"calibration": calibration} if calibration is not None else {}))
    return context.root


def _planned_trials(scene, trials_per_station, max_trials):
    planned = len(scene["stations"]) * trials_per_station
    return planned if max_trials is None else min(planned, max_trials)


def _infrastructure_failure(reason):
    return {"status": "infrastructure_error", "failure_scope": "shared", "reason": reason}


@dataclass(frozen=True)
class _PlannedStation:
    """A prepared station whose batched grasp plan is on the GPU pool."""
    job: StationJob
    prepared: object
    candidates: list


class _CalibrationScheduler:
    """CPU preparation → shared GPU planning pool → CPU execution workers, per station."""

    def __init__(self, context, service_type, trial_worker):
        self.context = context
        self.config = context.config
        self.service_type = service_type
        self.trial_worker = trial_worker
        self.started = time.monotonic()
        self.mp = multiprocessing.get_context("spawn")
        self.planned = sum(_planned_trials(s, self.config.trials_per_station, context.run.max_trials)
                           for s in context.scenes)
        self.cpu_count = min(context.run.cpu_workers, self.planned)
        self.capacity = context.run.cpu_workers + self.config.trials_per_station * len(context.gpu_ids)
        self.stop = self.mp.Event()
        self.requests, self.results = self.mp.Queue(), self.mp.Queue()
        self.jobs = [self.mp.Queue() for _ in range(self.cpu_count)]
        self.replies = [self.mp.Queue() for _ in range(self.cpu_count)]
        self.preparations = {}  # future -> StationJob
        self.batches = {}  # query id -> _PlannedStation
        self.recordings = {}  # future -> ReadyTrial
        self.routes = {}  # pose query id -> worker
        self.ready, self.active, self.idle = deque(), {}, deque(range(self.cpu_count))
        self.trials, self.preparation_failures = [], []
        self.failure = None
        self.peaks = {"cpu_trials": 0, "gpu_queries": 0, "preparations": 0}
        self.gpu_active_s = 0.0
        self.units = iter(self._stations())
        self.exhausted = False

    def run(self):
        with self._started():
            while not self._step():
                pass
        return self.trials, {"planned_trials": self.planned, "recorded_trials": len(self.trials),
                             "preparation_failures": self.preparation_failures, "failure": self.failure,
                             "peaks": self.peaks, "gpu_active_s": self.gpu_active_s,
                             "elapsed_s": time.monotonic() - self.started}

    @contextmanager
    def _started(self):
        """Start the pools and CPU workers; on exit settle every trial, then stop in reverse order."""
        with ExitStack() as stack:
            stack.callback(self._close_queues)
            self.pool = PlanningPool(self.context.gpu_ids, workers_per_gpu=self.context.run.planning_workers_per_gpu,
                                     service_type=self.service_type)
            stack.callback(self.pool.close)
            self.cpus = [self.mp.Process(target=self.trial_worker, args=(
                index, self.jobs[index], self.requests, self.replies[index], self.results, self.stop, self.config,
                self.context.calibration_collection, self.context.assets_dir, self.context.root))
                for index in range(self.cpu_count)]
            for process in self.cpus:
                process.start()
            stack.callback(self._stop_workers)
            self.prep = ProcessPoolExecutor(max_workers=self.context.run.preparation_workers,
                                            mp_context=self.mp, initializer=initialize_preparation_worker)
            stack.callback(self._shutdown_preparation)
            try:
                yield
            finally:
                self.stop.set()
                self._fail_inflight()
                self._drain_active()
                self._flush_recordings()

    def _step(self):
        """One scheduling round; True when calibration is finished or stopped."""
        self._prepare_completed()
        self._record_completed()
        self._route_queries()
        self._plans_completed()
        self._trials_completed()
        if self.stop.is_set():
            return True
        self._reap_exited("CPU worker {worker} exited with code {code}")
        if self.failure:
            return True
        self._dispatch_trials()
        self._submit_preparations()
        self._dispatch_planning()
        if self.exhausted and not (self.preparations or self.batches or self.recordings or self.ready
                                   or self.active):
            return True
        self._trials_completed(timeout=0.01)
        return False

    def _stations(self):
        config, run = self.config, self.context.run
        for scene in self.context.scenes:
            remaining = _planned_trials(scene, config.trials_per_station, run.max_trials)
            for index in range(len(scene["stations"])):
                size = min(config.trials_per_station, remaining)
                if size <= 0:
                    break
                identities = [TrialIdentity(scene["scene_id"], index, trial,
                                            f"r{next(self.context.attempt_ids):06d}",
                                            config.seed + index * config.trials_per_station + trial)
                              for trial in range(size)]
                yield StationJob(scene, index, identities, [TrialBudget(config.attempt_timeout_s)
                                                            for _ in identities])
                remaining -= size

    def _save(self, trial, result):
        record = trial_record(result, trial.scene, trial.scene["stations"][trial.index],
                              trial.response.identity.trial, trial.response.identity.seed)
        record["station_index"] = trial.index
        append_row(Path(self.context.root) / "trials.jsonl", record)
        self.trials.append(record)
        if shared_failure(record):
            self.failure = result
            self.stop.set()

    def _receive_cpu(self, message):
        worker, _, _, _, result = message
        self.idle.append(worker)
        self._save(self.active.pop(worker), result)

    def _record_later(self, trial):
        future = self.prep.submit(record_unexecuted, trial.scene, trial.index, trial.prepared, trial.response,
                                  trial.budget, self.config, self.context.calibration_collection, self.context.root)
        self.recordings[future] = trial

    def _prepare_completed(self):
        for future in list(self.preparations):
            if not future.done():
                continue
            job = self.preparations.pop(future)
            result = future.result()
            if result["status"] != "ready":
                self.preparation_failures.append({"scene_id": job.scene["scene_id"], "station_index": job.index,
                                                  **result})
                if result.get("failure_scope") == "shared":
                    self.failure = result
                    self.stop.set()
                continue
            prepared = result["prepared"]
            candidates = grasp_candidates(job.scene["task"], self.context.assets_dir)
            query_id = f"{job.identities[0].attempt_id}-batch"
            request = PlanningRequest(query_id, job.scene["scene_path"], job.identities, prepared.state, "preclose",
                                      job.budgets, PlanningInputs(
                                          job.scene["task"], job.scene["robot_config"], self.config,
                                          self.context.root, self.context.assets_dir, candidates=candidates,
                                          preparation_path=prepared.path))
            self.batches[query_id] = _PlannedStation(job, prepared, candidates)
            self.pool.submit(request)

    def _record_completed(self):
        for future in list(self.recordings):
            if future.done():
                self._save(self.recordings.pop(future), future.result())

    def _route_queries(self):
        while True:
            try:
                worker, request = self.requests.get_nowait()
            except Empty:
                break
            self.routes[request.query_id] = worker
            self.pool.submit(request)

    def _plans_completed(self):
        while (completed := self.pool.poll()) is not None:
            request, responses = completed
            self.gpu_active_s += responses[0].planning_s
            if request.stage == "pose":
                self.replies[self.routes.pop(request.query_id)].put(responses[0])
                continue
            planned = self.batches.pop(request.query_id)
            for response, budget in zip(responses, planned.job.budgets):
                trial = ReadyTrial(planned.job.scene, planned.job.index, planned.prepared, response, budget,
                                   planned.candidates)
                if response.status == "success":
                    self.ready.append(trial)
                else:
                    self._record_later(trial)
        if self.pool.failure:
            self.failure = self.pool.failure
            self.stop.set()

    def _trials_completed(self, timeout=None):
        """Save every finished trial; with ``timeout`` first wait that long for one."""
        try:
            message = self.results.get(timeout=timeout) if timeout else self.results.get_nowait()
            while True:
                self._receive_cpu(message)
                message = self.results.get_nowait()
        except Empty:
            pass

    def _reap_exited(self, reason):
        """Record the running trial of every exited CPU worker as a shared infrastructure failure."""
        for worker in list(self.active):
            code = self.cpus[worker].exitcode
            if code is not None:
                trial = self.active.pop(worker)
                self._save(trial, {**_infrastructure_failure(reason.format(worker=worker, code=code)),
                                   "rollout_id": trial.response.identity.attempt_id, "recording_complete": False})

    def _dispatch_trials(self):
        while self.idle and self.ready:
            worker = self.idle.popleft()
            self.active[worker] = self.ready.popleft()
            self.jobs[worker].put(self.active[worker])

    def _submit_preparations(self):
        buffered = (len(self.active) + len(self.ready) + len(self.recordings)
                    + sum(len(job.identities) for job in self.preparations.values())
                    + sum(len(planned.job.budgets) for planned in self.batches.values()))
        while (not self.exhausted and len(self.preparations) < self.context.run.preparation_workers
               and buffered + self.config.trials_per_station <= self.capacity):
            job = next(self.units, None)
            if job is None:
                self.exhausted = True
                break
            future = self.prep.submit(prepare_job, job.scene, job.index, self.config, self.context.root)
            self.preparations[future] = job
            buffered += len(job.identities)

    def _dispatch_planning(self):
        self.pool.dispatch()
        self.peaks["cpu_trials"] = max(self.peaks["cpu_trials"], len(self.active))
        self.peaks["gpu_queries"] = max(self.peaks["gpu_queries"], len(self.pool.active))
        self.peaks["preparations"] = max(self.peaks["preparations"], len(self.preparations))

    def _fail_inflight(self):
        """After an interruption: stop running trials and record planned trials as unexecuted."""
        if not (self.failure or self.active or self.pool.active):
            return
        self.failure = self.failure or _infrastructure_failure("calibration interrupted")
        self.pool.failure = self.failure
        for worker, trial in self.active.items():
            identity = trial.response.identity
            self.replies[worker].put(PlanningResponse(f"{identity.attempt_id}-stop", identity, "infrastructure_error",
                                                      0.0, trial.budget.plan_count, diagnostics=self.failure))
        while self.ready:
            trial = self.ready.popleft()
            self._record_later(replace(trial, response=replace(
                trial.response, status="infrastructure_error", diagnostics=self.failure)))

    def _drain_active(self):
        while self.active:
            try:
                self._receive_cpu(self.results.get(timeout=0.1))
            except Empty:
                self._reap_exited("execution worker exited")

    def _flush_recordings(self):
        for future, trial in self.recordings.items():
            self._save(trial, future.result())
        self.recordings.clear()

    def _shutdown_preparation(self):
        self.prep.shutdown(wait=True, cancel_futures=bool(self.failure))

    def _stop_workers(self):
        for queue in self.jobs:
            queue.put(None)
        if self.failure:
            # Keep draining planning requests so blocked workers can observe the stop and exit.
            while any(process.is_alive() for process in self.cpus):
                try:
                    self.requests.get(timeout=0.1)
                except Empty:
                    pass
        for process in self.cpus:
            process.join()

    def _close_queues(self):
        for queue in [self.requests, self.results, *self.jobs, *self.replies]:
            if self.failure:
                queue.cancel_join_thread()
            queue.close()
            if not self.failure:
                queue.join_thread()


def calibrate_stations(context, *, service_type=PlanningService, trial_worker=execution_worker):
    """Calibrate every station of ``context.scenes``; returns (trial records, scheduler summary)."""
    return _CalibrationScheduler(context, service_type, trial_worker).run()
