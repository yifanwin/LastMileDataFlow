"""Build a mode candidate library with one shared context per task.

House workers reuse their read-only source model. Only the parent writes scenes.jsonl;
actual stepping and edits remain in the shared runtime construction session.
"""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing, contextmanager, nullcontext
from dataclasses import asdict, replace
import logging
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import signal
import time

import numpy as np

from ..config import load_robot_config
from ..runtime.execution_config import load_execution_config
from ..runtime.simulation import Simulation
from ..scenes.build.context import BuildContext
from ..scenes.build.builder import build_task
from ..scenes.augmentation.regions import support_regions
from ..scenes.dataset_config import load_task_mode_recipe
from ..scenes.loading.source import SceneSource
from ..scenes.mode_config import load_mode_config
from ..scenes.modes import create_builders
from ..scenes.stations.initialization import inside_triangles, room_triangles
from ..scenes.stations.sampling import disk_samples
from ..scenes.tasks.discovery import discover_tasks, grasp_candidates
from ..planning.navigation.astar import task_support_obstacles
from ..utils.io import append_row, create_run, rows, write_json
from ..utils.logging_config import Progress

logger = logging.getLogger(__name__)


def _target_distance(left, right):
    """按世界坐标 XY 距离选目标，不把支撑面的高度差算作空间分散。"""
    return float(
        np.linalg.norm(np.asarray(left["task"]["anchor_world"][:2]) - right["task"]["anchor_world"][:2])
    )


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



def _select_tasks(sim, recipe, house):
    tasks, skipped = discover_tasks(sim, sim.source_metadata, recipe.geometry)
    floor = room_triangles(sim)
    candidates = []
    selectors = recipe.selectors(house)
    for task in tasks:
        if selectors and not any(selector in (task['instance_id'], task['asset_id'], task['task_id']) for selector in selectors):
            continue
        if f"{house}:{task['task_id']}" in recipe.exclude_tasks:
            skipped.append({'task_id': task['task_id'], 'reason': 'excluded_task'})
            continue
        if recipe.include_categories and task['category'] not in recipe.include_categories:
            skipped.append({'task_id': task['task_id'], 'reason': 'not_included_target_category'})
            continue
        prefixes = recipe.include_assets.get(task['category'])
        if prefixes and not any(str(task.get('asset_id', '')).startswith(prefix) for prefix in prefixes):
            skipped.append({'task_id': task['task_id'], 'reason': 'not_included_target_asset'})
            continue
        if task['category'] in recipe.exclude_categories:
            skipped.append({'task_id': task['task_id'], 'reason': 'excluded_target_category'})
            continue
        if not grasp_candidates(task, recipe.assets_dir, max_rows=16, allow_fallback=False):
            skipped.append({'task_id': task['task_id'], 'reason': 'missing_valid_data_grasps'})
            continue
        supports = task_support_obstacles(sim, task)
        surfaces = support_regions(sim, {**task, 'support_obstacles': supports}) if task['operation'] == 'pick' else []
        if task['operation'] == 'pick' and not surfaces:
            skipped.append({'task_id': task['task_id'], 'reason': 'missing_valid_support'})
            continue
        coarse = [station for station in disk_samples(task['anchor_world'], 1., .25)
                  if len(floor) and inside_triangles(np.asarray(station['xy']), floor)]
        if not coarse:
            skipped.append({'task_id': task['task_id'], 'reason': 'no_source_floor_in_sampling_disk'})
            continue
        area = max(((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]) for r in surfaces), default=0.)
        candidates.append({'task': task, 'support_id': surfaces[0].support if surfaces else task['target_body'],
                           'support_area_m2': area, 'prefilter_count': len(coarse)})
    selected = []
    for operation in recipe.operations:
        count = getattr(recipe, f'max_{operation}_targets_per_house')
        selected.extend(spatial_select([row for row in candidates if row['task']['operation'] == operation], count))
    return [row['task'] for row in selected], {'house': house, 'discovered_task_count': len(tasks),
            'eligible_task_count': len(candidates), 'selected_task_ids': [row['task']['task_id'] for row in selected],
            'exclusions': skipped, 'prefilter_scope': 'source_floor_and_grasp_support_only_not_station_rollout'}


def _task_preparation_rejections(task, recipe, house, reason):
    if not recipe.modes:
        yield {'house': house, 'task_id': task['task_id'], 'proposal_intent': 'no_edit',
               'status': 'rejected', 'reason': 'task_preparation_failed', 'detail': reason}
        return
    for mode in recipe.modes:
        if mode == 'articulated_interaction' and task['operation'] != 'open':
            yield {'house': house, 'task_id': task['task_id'], 'proposal_intent': mode,
                   'status': 'skipped', 'reason': 'mode_not_applicable_to_operation'}
            continue
        for variant in range(recipe.variants_per_intent):
            yield {'house': house, 'task_id': task['task_id'], 'proposal_intent': mode,
                   'variant': variant, 'status': 'rejected', 'reason': 'task_preparation_failed', 'detail': reason}


class _TaskTimeout(Exception):
    pass


@contextmanager
def _task_deadline(seconds):
    if not seconds:
        yield
        return
    def handler(signum, frame):
        raise _TaskTimeout(f'task exceeded {seconds:g}s')
    previous = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _build_house(job):
    recipe, house, robot, root, gpu = job
    os.environ.update(MUJOCO_GL='egl', MUJOCO_EGL_DEVICE_ID=str(gpu), CUDA_VISIBLE_DEVICES=str(gpu),
                      OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    start = time.monotonic()
    records = []
    report = {'house': house, 'dataset_split': recipe.house_splits[str(house)], 'tasks': []}
    try:
        source = SceneSource.procthor(recipe.dataset_dir, house)
        snapshot = recipe.input_snapshots.get(str(house))
        sim = (Simulation.from_snapshot(snapshot, robot) if snapshot else Simulation.from_source(source, robot))
        with closing(sim):
            if sim.source.scene_id != source.scene_id:
                raise ValueError('input snapshot source does not match requested house')
            if not hasattr(sim, 'source_metadata'):
                from ..utils.io import read_json
                sim.source_metadata = read_json(source.metadata_path)
            tasks, discovery = _select_tasks(sim, recipe, house)
            report.update(discovery)
            report['status'] = 'selected' if tasks else 'no_eligible_tasks'
            baseline_cache = {}
            for task_index, task in enumerate(tasks):
                logger.info('构建 house=%s task=%s 共享准备', house, task['task_id'])
                try:
                    with _task_deadline(recipe.task_timeout_s if not recipe.modes else None):
                        try:
                            ctx = BuildContext.prepare(sim, task, recipe)
                        except ValueError as exc:
                            records.extend(_task_preparation_rejections(task, recipe, house, str(exc)))
                            report['tasks'].append({'task_id': task['task_id'], 'status': 'preparation_rejected', 'reason': str(exc)})
                            continue
                        with ctx:
                            builders = create_builders(recipe.modes)
                            seed = recipe.seed + house * 10000 + task_index
                            records.extend(build_task(ctx, builders, root, house=house, seed=seed,
                                                      baseline_cache=baseline_cache))
                            report['tasks'].append({'task_id': task['task_id'], 'preparation_s': ctx.timings,
                                'grasp_evidence': ctx.grasp_evidence, 'asset_compiles': ctx.assets.compile_count,
                                'station_initializations': sum(r.get('build_metrics', {}).get('station_initializations', 0)
                                                              for r in records if r.get('task_id') == task['task_id'])})
                except _TaskTimeout as exc:
                    records.append({'house': house, 'task_id': task['task_id'], 'proposal_intent': 'no_edit',
                                    'status': 'rejected', 'reason': 'task_timeout', 'detail': str(exc)})
                    report['tasks'].append({'task_id': task['task_id'], 'status': 'rejected',
                                            'reason': 'task_timeout', 'detail': str(exc)})
    except Exception as exc:
        # A worker boundary preserves finished records before the parent stops
        # on shared render/model/I/O faults. Ordinary candidates reject locally.
        report['error'] = {'type': type(exc).__name__, 'reason': str(exc)}
    report['elapsed_s'] = time.monotonic() - start
    return {'records': records, 'source': report}


def _shared_build_error(error):
    text = f"{error.get('type', '')}:{error.get('reason', '')}".lower()
    return any(token in text for token in ('cuda', 'egl', 'opengl', 'renderer', 'out of memory', 'gpu', 'curobo'))


def _house_error_record(house, error, dataset_split):
    return {'house': house, 'scene_id': f'val{house}-house-error', 'proposal_intent': 'no_edit',
            'status': 'rejected', 'reason': 'house_preparation_error', 'detail': error,
            'dataset_split': dataset_split}


def _build_into_root(recipe, robot, root, gpu_ids, workers, *, isolate_house_errors=False):
    (root / 'scenes.jsonl').touch()
    start = time.monotonic()
    jobs = [(recipe, house, robot, root, gpu_ids[i % len(gpu_ids)]) for i, house in enumerate(recipe.houses)]
    summary = {'status': 'running', 'sources': [], 'accepted': 0, 'rejected': 0, 'skipped': 0,
               'isolated_errors': 0, 'build_workers': workers, 'gpu_ids': list(gpu_ids)}
    progress = Progress(logger)
    error = None
    pool = None
    try:
        if workers > 1:
            pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'))
            pending = [pool.submit(_build_house, job) for job in jobs]
            results = (future.result() for future in as_completed(pending))
        else:
            results = map(_build_house, jobs)
        for result in results:
            for record in result['records']:
                append_row(root / 'scenes.jsonl', record)
                summary[record['status']] += 1
            summary['sources'].append(result['source'])
            progress.update('场景准备 来源=%s/%s 接受=%s 拒绝=%s 跳过=%s',
                            len(summary['sources']), len(jobs), summary['accepted'], summary['rejected'], summary['skipped'])
            if result['source'].get('error'):
                failure = result['source']['error']
                if isolate_house_errors and not _shared_build_error(failure):
                    append_row(root / 'scenes.jsonl',
                               _house_error_record(result['source']['house'], failure,
                                                   result['source'].get('dataset_split')))
                    summary['rejected'] += 1
                    summary['isolated_errors'] += 1
                    continue
                error = failure
                if pool:
                    for future in pending:
                        future.cancel()
                break
    except Exception as exc:
        error = {'type': type(exc).__name__, 'reason': str(exc)}
        if pool:
            for future in pending:
                future.cancel()
    finally:
        if pool:
            pool.shutdown(wait=True, cancel_futures=True)
            # Active workers may have finished while shutdown waited. Save every
            # real result once; never fill canceled houses with fake rejections.
            recorded = {source['house'] for source in summary['sources']}
            for future in pending:
                if future.cancelled() or not future.done() or future.exception() is not None:
                    continue
                result = future.result()
                if result['source']['house'] in recorded:
                    continue
                recorded.add(result['source']['house'])
                for record in result['records']:
                    append_row(root / 'scenes.jsonl', record)
                    summary[record['status']] += 1
                summary['sources'].append(result['source'])
                if result['source'].get('error') and isolate_house_errors:
                    failure = result['source']['error']
                    if _shared_build_error(failure):
                        error = failure
                        continue
                    append_row(root / 'scenes.jsonl',
                               _house_error_record(result['source']['house'], failure,
                                                   result['source'].get('dataset_split')))
                    summary['rejected'] += 1
                    summary['isolated_errors'] += 1
        summary.update(status='infrastructure_error' if error else 'completed', wall_elapsed_s=time.monotonic() - start)
        if error:
            summary['error'] = error
        summary['intent_counts'] = dict(Counter(row['proposal_intent'] for row in rows(root / 'scenes.jsonl')))
        write_json(root / 'summary.json', summary)
    if error:
        raise RuntimeError('scene build stopped: ' + error['type'] + ':' + error['reason'])
    return root


def _house_worker(job, queue):
    try:
        queue.put(_build_house(job))
    except BaseException as exc:
        queue.put({'records': [], 'source': {
            'house': job[1], 'error': {'type': type(exc).__name__, 'reason': str(exc)}}})


def _build_into_root_isolated(recipe, robot, root, gpu_ids, workers, house_timeout_s):
    """Per-house processes that can be killed; one pathological house never blocks the batch."""
    (root / 'scenes.jsonl').touch()
    start = time.monotonic()
    jobs = [(recipe, house, robot, root, gpu_ids[i % len(gpu_ids)]) for i, house in enumerate(recipe.houses)]
    summary = {'status': 'running', 'sources': [], 'accepted': 0, 'rejected': 0, 'skipped': 0,
               'isolated_errors': 0, 'build_workers': workers, 'gpu_ids': list(gpu_ids)}
    progress = Progress(logger)
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    pending, active, recorded = list(jobs), {}, set()
    error = None

    def launch(job):
        process = context.Process(target=_house_worker, args=(job, results), daemon=True)
        process.start()
        active[job[1]] = (process, time.monotonic())

    def save(result):
        house = result['source']['house']
        if house in recorded:
            return
        recorded.add(house)
        for record in result['records']:
            append_row(root / 'scenes.jsonl', record)
            summary[record['status']] += 1
        summary['sources'].append(result['source'])
        progress.update('场景准备 来源=%s/%s 接受=%s 拒绝=%s 跳过=%s',
                        len(summary['sources']), len(jobs), summary['accepted'], summary['rejected'], summary['skipped'])
        failure = result['source'].get('error')
        if failure:
            if _shared_build_error(failure):
                return failure
            append_row(root / 'scenes.jsonl',
                       _house_error_record(house, failure, result['source'].get('dataset_split')))
            summary['rejected'] += 1
            summary['isolated_errors'] += 1
        return None

    for job in pending[:workers]:
        launch(job)
    pending = pending[workers:]
    try:
        while active or pending:
            try:
                result = results.get(timeout=1)
            except Empty:
                result = None
            if result is not None:
                house = result['source']['house']
                process, _ = active.pop(house, (None, None))
                failure = save(result)
                if process is not None:
                    process.join(timeout=1)
                    if process.is_alive():
                        process.terminate()
                if failure:
                    error = failure
                    break
                if pending:
                    launch(pending.pop(0))
            now = time.monotonic()
            for house, (process, started) in list(active.items()):
                if now - started > house_timeout_s:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                    active.pop(house)
                    if house not in recorded:
                        recorded.add(house)
                        append_row(root / 'scenes.jsonl', _house_error_record(
                            house, {'type': 'TimeoutError', 'reason': f'house_timeout>{house_timeout_s:g}s'}, None))
                        summary['sources'].append({'house': house, 'status': 'timeout',
                                                   'error': {'type': 'TimeoutError',
                                                             'reason': f'house_timeout>{house_timeout_s:g}s'}})
                        summary['rejected'] += 1
                        summary['isolated_errors'] += 1
                        progress.update('场景准备 来源=%s/%s 超时 house=%s', len(summary['sources']), len(jobs), house)
                    if pending:
                        launch(pending.pop(0))
                elif process.exitcode is not None:
                    active.pop(house)
                    if house not in recorded:
                        recorded.add(house)
                        append_row(root / 'scenes.jsonl', _house_error_record(
                            house, {'type': 'ProcessExit', 'reason': f'exit code {process.exitcode}'}, None))
                        summary['sources'].append({'house': house, 'status': 'process_exit',
                                                   'error': {'type': 'ProcessExit',
                                                             'reason': f'exit code {process.exitcode}'}})
                        summary['rejected'] += 1
                        summary['isolated_errors'] += 1
                    if pending:
                        launch(pending.pop(0))
    finally:
        for process, _ in active.values():
            process.terminate()
            process.join(timeout=5)
    summary.update(status='infrastructure_error' if error else 'completed',
                   wall_elapsed_s=time.monotonic() - start)
    if error:
        summary['error'] = error
    summary['intent_counts'] = dict(Counter(row['proposal_intent'] for row in rows(root / 'scenes.jsonl')))
    write_json(root / 'summary.json', summary)
    if error:
        raise RuntimeError('scene build stopped: ' + error['type'] + ':' + error['reason'])
    return root


def _create_build_root(root, recipe, robot):
    return create_run(Path(root),
        {'recipe': asdict(recipe), 'robot': asdict(robot), 'geometry': asdict(recipe.geometry),
         'mode_settings': asdict(load_mode_config(recipe.mode_config)),
         'execution': asdict(load_execution_config(recipe.execution_config)), 'seed': recipe.seed})


def _build_worker_limit():
    return min(64, max(1, os.cpu_count() or 1))


def build_scenes(config, output_dir, run_id, *, gpu_ids, workers=1):
    if not 1 <= workers <= _build_worker_limit():
        raise ValueError(f'build workers must be between 1 and {_build_worker_limit()}')
    recipe = load_task_mode_recipe(config)
    robot = load_robot_config(recipe.robot_config)
    root = _create_build_root(Path(output_dir) / 'scene_datasets' / run_id, recipe, robot)
    return _build_into_root(recipe, robot, root, gpu_ids, workers)


def prepare_scene_inputs(config, root, *, gpu_ids, workers=1, exclude_categories=None, house_timeout_s=1200):
    """One-shot no-edit inputs for direct calibration; isolated house errors do not stop the batch."""
    if not 1 <= workers <= _build_worker_limit():
        raise ValueError(f'build workers must be between 1 and {_build_worker_limit()}')
    recipe = load_task_mode_recipe(config)
    if exclude_categories:
        recipe = replace(recipe, exclude_categories=list(exclude_categories))
    robot = load_robot_config(recipe.robot_config)
    root = _create_build_root(root, recipe, robot)
    return _build_into_root_isolated(recipe, robot, root, gpu_ids, workers, house_timeout_s)
