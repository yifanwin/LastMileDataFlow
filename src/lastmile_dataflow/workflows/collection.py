"""统一编排站位标定与连续采集，共用运行准备、场景遍历和资源收尾。"""
import logging
from collections import Counter
from dataclasses import asdict, replace
import os
from itertools import count
from pathlib import Path
import time
import multiprocessing
from queue import Empty

from ..config import read_config
from ..config import RobotConfig
from ..utils.io import append_row, create_run, read_json, rows, write_json, write_rows
from ..utils.logging_config import Progress, trial_logging
from ..runtime.simulation import Simulation
from ..runtime.execution.curobo import run_curobo_attempt
from ..runtime.execution.vla import run_vla_attempt
from ..runtime.execution.agent import run_agent_attempt
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import execution_profile, calibration_execution
from ..data.evaluation.selection import station_calibration, eligible_stations, generate_pairs, evaluable
from ..data.evaluation.statistics import aggregate
from ..data.export.lastmile_dataset import successful, training_record
from ..scenes.tasks.discovery import grasp_candidates, robot_footprint
from ..planning.navigation.astar import scene_grid, task_support_obstacles
from ..planning.navigation.geometry import GeometryNavigation, NavigationSettings, NAVIGATION_PROTOCOL
from ..planning.curobo.planner import StationPlannerCache
from ..data.visualization.heatmap import export_heatmap

logger = logging.getLogger(__name__)


def _trial_record(result, scene, station, trial, seed):
    """提取执行结果，补齐场景、站位、试验序号和随机种子。"""
    record = {key: result.get(key) for key in ('status', 'reason', 'execution_termination',
        'task_outcome', 'recording_complete', 'replay_complete', 'observation_complete',
        'executed_steps', 'navigation', 'error', 'failure_scope', 'planning_ref',
        'attribution', 'retry_history', 'exception_detail')}
    record.update(scene_id=scene['scene_id'], station_id=station['station_id'], trial=trial, seed=seed,
                  rollout_id=result.get('rollout_id'), task_id=scene['task']['task_id'])
    return record


def _execute(baseline, scene, station, assets_dir, config, collection, root, seed, policy=None, path=None, goal=None, *, attempt_ids, planner_cache=None, prepared=None):
    # 分配本次运行内唯一的试验编号。
    """调用指定后端执行一次试验，共用试验编号和记录链路。"""
    attempt_id = f'r{next(attempt_ids):06d}'
    task = scene['task']
    logger.debug('试验开始 %s scene=%s station=%s backend=%s seed=%s',
                attempt_id, scene['scene_id'], station['station_id'], config.operation_backend, seed)
    started = time.monotonic()
    # 按后端选择执行器，导航路径和目标站位沿用同一接口。
    if config.operation_backend == 'pyrua':
        if path is not None:
            raise ValueError('the PyRUA agent drives the base itself; no planned navigation path')
        result = run_agent_attempt(baseline, task, station, config, collection, root=root, attempt_id=attempt_id,
                                   seed=seed, policy=policy)
    elif config.operation_backend in ('molmobot', 'pi05'):
        result = run_vla_attempt(baseline, task, station, config, collection, root=root, attempt_id=attempt_id,
                               seed=seed, policy=policy, path=path, goal=goal)
    else:
        result = run_curobo_attempt(baseline, task, station, grasp_candidates(task, assets_dir), assets_dir,
                           config, collection, root=root, attempt_id=attempt_id, seed=seed, path=path, goal=goal,
                           planner_cache=planner_cache, prepared=prepared)
    # 根据执行状态选择日志级别，不改变原始结果。
    log_result = logger.debug
    if result.get('status') in ('initialization_error', 'episode_error', 'recording_error', 'simulation_failure', 'infrastructure_error'):
        log_result = logger.error
    elif result.get('status') == 'timeout':
        log_result = logger.warning
    log_result('试验结束 %s status=%s reason=%s steps=%s recording_complete=%s elapsed=%.1fs', attempt_id,
                result.get('status'), result.get('reason'), result.get('executed_steps'), result.get('recording_complete'), time.monotonic() - started)
    return result


def _station_records(baseline, scene, index, trial_count, assets_dir, config, collection, root,
                     policy=None, *, attempt_ids, reuse_planners=True, stop=None):
    station = scene['stations'][index]
    cache = StationPlannerCache() if reuse_planners and getattr(config, 'operation_backend', None) == 'curobo' else None
    prepared = None
    if getattr(config, 'shared_station_preparation', False) and config.operation_backend == 'curobo':
        from ..runtime.execution.preparation import prepare_station
        from ..runtime.execution.lifecycle import classify_episode_exception
        directory = Path(root) / 'preparations' / scene['scene_id'] / station['station_id']
        try:
            prepared = prepare_station(baseline, station, config, directory)
        except Exception as exc:
            failure = classify_episode_exception(exc, 'initialize')
            write_json(directory / 'preparation.json', failure)
            if failure['failure_scope'] == 'shared':
                raise
            return  # Preparation failure is one station result, never three trial failures.
    try:
        for trial in range(trial_count):
            if stop is not None and stop.is_set():
                break
            seed = config.seed + index * config.trials_per_station + trial
            label = f"scene={scene['scene_id']} station={station['station_id']} trial={trial + 1}/{config.trials_per_station}"
            with trial_logging(label, Progress(logger)):
                result = _execute(baseline, scene, station, assets_dir, config, collection, root,
                                  seed, policy, attempt_ids=attempt_ids, planner_cache=cache, prepared=prepared)
            record = _trial_record(result, scene, station, trial, seed)
            record['station_index'] = index
            if _shared_failure(record) and stop is not None:
                stop.set()
            yield record
            if _shared_failure(record):
                break
    finally:
        if cache is not None:
            cache.close()


def _station_worker(worker, gpu, egl_device, requests, results, stop, scene,
                    assets_dir, config, collection, root, reuse_planners):
    from ..utils.logging_config import setup_logging
    setup_logging()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu), MUJOCO_EGL_DEVICE_ID=str(egl_device), MUJOCO_GL='egl')
    sim = None
    try:
        sim = Simulation.from_snapshot(scene['scene_path'], scene['robot_config'], target=scene['task']['target_body'])
        sim.scene_id = scene['scene_id']
        while True:
            job = requests.get()
            if job is None:
                break
            index, ids = job
            for record in _station_records(sim, scene, index, len(ids), assets_dir, config, collection, root,
                    attempt_ids=iter(ids), reuse_planners=reuse_planners, stop=stop):
                results.put(('trial', worker, record))
            results.put(('done', worker, None))
    except Exception as exc:
        stop.set()
        results.put(('error', worker, f'{type(exc).__name__}: {exc}'))
    finally:
        if sim is not None:
            sim.close()


def _parallel_station_records(scene, assets_dir, config, collection, root, planned, *,
                              attempt_ids, workers, gpu_ids, egl_devices, reuse_planners):
    """Bounded station queue; only the parent writes the canonical trials.jsonl."""
    jobs = []
    for index in range(len(scene['stations'])):
        size = min(config.trials_per_station, planned - index * config.trials_per_station)
        if size <= 0:
            break
        jobs.append((index, [next(attempt_ids) for _ in range(size)]))
    if not jobs:
        return
    ctx = multiprocessing.get_context('spawn')
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
            process = ctx.Process(target=_station_worker, args=(worker, gpu_ids[slot], egl_devices[slot],
                requests, results, stop, scene, assets_dir, config, collection, root, reuse_planners))
            process.start()
            processes.append(process)
            requests.put(next(pending))
            active.add(worker)
        while active:
            try:
                kind, worker, payload = results.get(timeout=1.)
            except Empty:
                for worker in list(active):
                    code = processes[worker].exitcode
                    if code is not None:
                        stop.set()
                        active.remove(worker)
                        failure = failure or f'station worker exited with code {code}'
                continue
            if kind == 'error':
                stop.set()
                active.remove(worker)
                failure = failure or f'station worker {worker}: {payload}'
            elif kind == 'trial':
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
            raise RuntimeError(f'station worker exited with code {process.exitcode}')


def _station_trials(baseline, scene, assets_dir, config, collection, root, policy=None, budget=None, *,
                    attempt_ids, workers=1, gpu_ids=None, egl_devices=None, reuse_planners=True):
    """逐站位重复标定，达到预算或发生共享资源故障时停止。"""
    trials = []
    total = len(scene['stations']) * config.trials_per_station
    planned = total if budget is None else min(total, budget)
    started = time.monotonic()
    progress = Progress(logger)
    outcomes, valid_points, reasons = Counter(), Counter(), Counter()
    def serial():
        for index in range(len(scene['stations'])):
            size = min(config.trials_per_station, planned - len(trials))
            if size <= 0:
                break
            yield from _station_records(baseline, scene, index, size, assets_dir, config, collection, root,
                policy, attempt_ids=attempt_ids, reuse_planners=reuse_planners)

    records = serial() if workers == 1 else _parallel_station_records(
        scene, assets_dir, config, collection, root, planned, attempt_ids=attempt_ids,
        workers=workers, gpu_ids=gpu_ids, egl_devices=egl_devices, reuse_planners=reuse_planners)
    try:
        for record in records:
            append_row(root / 'trials.jsonl', record)
            trials.append(record)
            outcomes[record['status']] += 1
            if evaluable(record):
                valid_points[record['station_id']] += 1
            if record['status'] != 'success':
                reasons[record['reason']] += 1
            progress.update(
                'scene=%s trial=%s/%s 当前站位=%s 重复=%s/%s 成功=%s 耗时=%.1fs',
                scene['scene_id'], len(trials), planned, record['station_id'], record['trial'] + 1,
                config.trials_per_station, outcomes['success'], time.monotonic() - started)
            if _shared_failure(record) and workers == 1:
                break
    finally:
        records.close()
        # 试验记录数不等于有效试验数；仅有效次数齐全的站位计为有效完成。
        valid = sum(valid_points.values())
        progress.update(
            '标定收尾 scene=%s 已处理站位=%s/%s 有效完成站位=%s/%s trial=%s/%s 完整计划=%s '
            '有效=%s 成功=%s 异常或未知=%s 原因=%s 耗时=%.1fs',
            scene['scene_id'], len(trials) // config.trials_per_station, len(scene['stations']),
            sum(n == config.trials_per_station for n in valid_points.values()), len(scene['stations']),
            len(trials), planned, total, valid, outcomes['success'], len(trials) - valid,
            reasons.most_common(5), time.monotonic() - started, force=True)
    return trials


def _shared_failure(record):
    return record.get('failure_scope') == 'shared' or record.get('status') == 'infrastructure_error'


def _plan_collection(scenes, trials, config, root, source, diagnoses=None):
    """先落盘全部 pair 和已有成功示教，再启动任何连续执行。"""
    pairs, direct_rows, seen = [], [], set()
    for scene in scenes:
        if config.mode_config is not None and scene['dataset_split'] != 'train':
            continue
        current = [row for row in trials if row['scene_id'] == scene['scene_id']]
        binding = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}
        if any(row['task_id'] != binding['task_id'] for row in current):
            raise ValueError('calibration task mismatch')
        rates = station_calibration(scene['stations'], current, config.trials_per_station, binding, config)
        if config.mode_config is not None:
            from ..data.evaluation.diagnosis import diagnosed_pairs
            diagnosis = diagnoses[scene['scene_id']]
            if rates['complete'] != diagnosis['calibration_complete'] or rates['task_eligibility'] != diagnosis['task_eligibility']:
                raise ValueError('diagnosis does not match raw calibration completeness')
            candidates = diagnosed_pairs(scene, diagnosis)
            points = {r['station_id']: r for r in scene['stations']}
        else:
            candidates = [{'s0': start['station_id'], 's1': goal['station_id'],
                           's0_success_rate': start['success_rate'], 's1_success_rate': goal['success_rate']}
                          for start, goal in generate_pairs(*eligible_stations(scene['stations'], rates, config))]
            points = {r['station_id']: r for r in scene['stations']}
        for candidate in candidates:
            start, goal = points[candidate['s0']], points[candidate['s1']]
            distance = sum((a - b) ** 2 for a, b in zip(start['xy'], goal['xy'])) ** .5
            pairs.append({**binding, 'pair_id': f'p{len(pairs):06d}',
                          's0': start['station_id'], 's1': goal['station_id'],
                          **candidate,
                          **({'diagnosis_ref': str((Path(root) / source / 'diagnosis.jsonl').resolve())}
                             if config.mode_config is not None else {}),
                          'seed': config.seed + len(pairs), 'status': 'not_executed', 'reason': 'pending',
                          'euclidean_distance_m': distance, 'astar_path_length_m': None,
                          'navigation_steps': 0, 'rollout_id': None})
        for row in current:
            if successful(row) and row['rollout_id'] not in seen:
                direct_rows.append((row, scene))
                seen.add(row['rollout_id'])
    write_rows(root / 'pair_index.jsonl', pairs)
    write_rows(root / 'train_index.jsonl', [])
    for row, scene in direct_rows:
        direct = dict(row)
        if config.mode_config is not None:
            direct.update(diagnosis_ref=str((Path(root) / source / 'diagnosis.jsonl').resolve()), dataset_split='train')
            if not direct.get('observation_complete') and direct.get('replay_complete'):
                from ..data.rendering.replay import render_observations
                source_root = (Path(root) / source).resolve()
                saved = read_json(source_root / 'config.json')
                observation_dir = Path(root) / 'observations' / row['rollout_id']
                rendered = render_observations(source_root / 'rollouts' / row['rollout_id'], observation_dir,
                    cameras=saved['profiles'][scene['scene_id']]['robot']['cameras'],
                    depth=saved['collection']['record_depth'])
                direct.update(observation_complete=rendered['complete'], observations_ref=str(observation_dir.resolve()),
                              observation_source='measured_state_replay')
        if successful(direct, observations=True):
            append_row(root / 'train_index.jsonl', training_record(direct, source, 's1_direct_operation'))

    return pairs


def _recovery_trials(baseline, scene, pairs, assets_dir, config, collection, root, *, attempt_ids):
    """执行当前场景的全部 pair；完整计划始终保留在 pair_index。"""
    current = [pair for pair in pairs if pair['scene_id'] == scene['scene_id']]
    if not current:
        return []
    footprint = robot_footprint(baseline, whole_body=config.navigation_whole_body)
    grid = scene_grid(baseline, scene['task']['anchor_world'], None, config.map_resolution_m,
                      footprint['radius_m'], config.navigation_margin_m, robot_height=footprint['max'][2] if config.navigation_whole_body else .4,
                      support_obstacles=task_support_obstacles(baseline, scene['task']))
    stations = {station['station_id']: station for station in scene['stations']}
    navigation = GeometryNavigation(baseline, grid, list(stations.values()), NavigationSettings.from_config(config),
                                    budget_s=min(120., config.attempt_timeout_s))
    progress = Progress(logger)
    processed = []
    try:
        for pair in current:
            start, goal = stations[pair['s0']], stations[pair['s1']]
            label = f"scene={scene['scene_id']} S0={pair['s0']} S1={pair['s1']}"
            with trial_logging(label, progress):
                navigation.hold(start)
                if navigation.compatible(goal):
                    planned = navigation.plan(start['base'], [{'xy': goal['base'][:2], 'yaw': goal['base'][2],
                                                              'anchor_station_id': goal['station_id']}])
                else:
                    planned = {'path': None, 'reason': 'navigation_posture_mismatch'}
                path = planned['path']
                pair['navigation_planning'] = {k: v for k, v in planned.items() if k != 'path'}
                if path is None:
                    pair.update(status='path_unavailable', reason=planned['reason'])
                else:
                    # 使用实际发送给跟踪器的折线，包括站位到栅格中心的连接段。
                    pair['astar_path_length_m'] = sum(
                        sum((a - b) ** 2 for a, b in zip(left, right)) ** .5
                        for left, right in zip(path['xy'], path['xy'][1:]))
                    pair.update(status='running', reason='attempt_started', navigation_steps=None)
                    result = _execute(baseline, scene, start, assets_dir, config, collection, root,
                                      pair['seed'], path=path, goal=goal, attempt_ids=attempt_ids)
                    pair.update(_trial_record(result, scene, start, None, pair['seed']))
                    pair['navigation_steps'] = (result.get('navigation') or {}).get(
                        'steps', 0 if result.get('executed_steps') == 0 else None)
                    if (config.mode_config is not None and successful(pair, continuous=True)
                            and not pair.get('observation_complete') and pair.get('replay_complete')):
                        from ..data.rendering.replay import render_observations
                        saved = read_json(Path(root) / 'config.json')
                        observation_dir = Path(root) / 'observations' / pair['rollout_id']
                        rendered = render_observations(Path(root) / 'rollouts' / pair['rollout_id'], observation_dir,
                            cameras=saved['profiles'][scene['scene_id']]['robot']['cameras'],
                            depth=saved['collection']['record_depth'])
                        pair.update(observation_complete=rendered['complete'], observations_ref=str(observation_dir.resolve()),
                                    observation_source='measured_state_replay')
                    if successful(pair, continuous=True, observations=True):
                        append_row(root / 'train_index.jsonl', training_record(pair, '.', 's0_to_s1_continuous'))
            processed.append(pair)
            write_rows(root / 'pair_index.jsonl', pairs)
            progress.update('连续采集 scene=%s 已处理=%s/%s 当前pair=%s status=%s',
                            scene['scene_id'], len(processed), len(current), pair['pair_id'], pair['status'])
            if _shared_failure(pair):
                break
    finally:
        navigation.close()
        for pair in current:
            if pair['status'] == 'running':
                pair.update(status='unknown', reason='attempt_interrupted')
        write_rows(root / 'pair_index.jsonl', pairs)
    return processed


def _station_summary(sim, scene, trials, config, root):
    """汇总原始标定结果、导出展示热图，并筛选合格的 S0/S1 配对。"""
    binding = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}
    rates = station_calibration(scene['stations'], trials, config.trials_per_station, binding, config)
    if not rates['complete']:
        logger.warning('scene=%s 标定不完整，不能用于正式困难配对', scene['scene_id'])
    # 热图仅用于展示；困难配对仍由原始标定结果决定。直接标定可关闭以节省时间。
    if config.export_figures:
        grid = scene_grid(sim, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m, 0., 0.,
                          support_obstacles=task_support_obstacles(sim, scene['task']))
        display = [{**row, 'attempt': row.get('rollout_id')} for row in trials]
        export_heatmap(root / 'figures' / scene['scene_id'], grid,
            aggregate(scene['stations'], display, config.trials_per_station), scene['task']['anchor_world'],
            config.smoothing_sigma_m or config.spacing_m or .1, title=scene['scene_id'])
    if config.mode_config is None:
        starts, goals = eligible_stations(scene['stations'], rates, config)
        pair_count = len(starts) * len(goals)
    else:
        pair_count = None  # Mode relations are computed by the diagnosis stage.
    logger.info('scene=%s 标定 trial=%s complete=%s 合格配对=%s 热图=%s',
                scene['scene_id'], len(trials), rates['complete'], pair_count,
                root / 'figures' / scene['scene_id'] if config.export_figures else 'disabled')
    summary = {'scene_id': scene['scene_id'], 'calibration_complete': rates['complete'],
               'eligible_pairs': pair_count, 'successes': sum(r['successes'] for r in rates['stations'])}
    if config.mode_config is not None:
        summary.update(task_eligibility=rates['task_eligibility'],
                       expected_trials=rates['expected_trials'], invalid_trials=rates['invalid_trials'])
    return summary


def _evaluate_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids, station_options=None):
    """完成单个场景的站位标定和汇总，不执行连续采集。"""
    trials = ([r for r in prior if r['scene_id'] == scene['scene_id']] if prior is not None else
              _station_trials(sim, scene, assets_dir, config, collection, root, policy, max_trials,
                              attempt_ids=attempt_ids, **(station_options or {})))
    summary = _station_summary(sim, scene, trials, config, root)
    summary['shared_failure'] = any(_shared_failure(row) for row in trials)
    return summary


def _collect_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids, pairs):
    """标定已在规划前完成；当前场景只展示率表并执行全组合。"""
    trials = [row for row in prior if row['scene_id'] == scene['scene_id']]
    summary = _station_summary(sim, scene, trials, config, root)
    summary['eligible_pairs'] = sum(pair['scene_id'] == scene['scene_id'] for pair in pairs)
    episodes = _recovery_trials(sim, scene, pairs, assets_dir, config, collection, root, attempt_ids=attempt_ids)
    summary['successes'] = sum(successful(row, continuous=True) for row in episodes)
    summary['shared_failure'] = any(_shared_failure(row) for row in episodes)
    return summary


def _prepare_run(dataset, config, collection, output_dir, run_id, gpu, samples, calibration, *, resources=None, dataset_split=None):
    """对齐场景与执行配置，检查标定复用条件并创建运行目录。"""
    # 复用采样间距和观测尺寸；操作工作区和导航碰撞参数由执行配置决定。
    geometry = read_config(Path(dataset) / 'config.json').get('geometry', {})
    keys = ('spacing_m', 'width', 'height', 'min_target_pixels')
    config = replace(config, **{key: geometry[key] for key in keys if key in geometry})
    collection = replace(collection, width=config.width, height=config.height, seed=config.seed,
                         output_dir=str(Path(output_dir).resolve()))
    scenes = load_scenes(dataset, samples)
    if dataset_split is not None:
        scenes = [scene for scene in scenes if scene['dataset_split'] == dataset_split]
    if not scenes:
        raise ValueError('no accepted scenes')
    # 检查场景控制模式，并生成各场景的执行配置档案。
    profiles = {}
    for scene in scenes:
        from ..config import robot_config
        robot = robot_config(read_config(Path(scene['scene_path']) / 'config.json')['robot'])
        if config.mode_config is not None:
            from ..scenes.mode_config import MODE_NAMES
            if (robot.name != 'panda_omron' or robot.control_profile != 'robocasa-v1'
                    or scene.get('proposal_intent') not in MODE_NAMES or scene.get('dataset_split') not in ('train', 'val')):
                raise ValueError('task-mode evaluation requires a v2 Panda RoboCasa candidate and source split')
        from ..policies.config import check_profile
        check_profile(robot,config.operation_backend,config.policy_config)
        scene['robot_config'] = robot
        profiles[scene['scene_id']] = execution_profile(robot, config)
    prior = None
    # 仅复用执行配置及各场景执行档案均匹配的标定。
    if calibration:
        logger.info('复用已有标定=%s，不重复 rollout', calibration)
        saved = read_config(Path(calibration) / 'config.json')
        if (Path(saved['dataset']).resolve() != Path(dataset).resolve()
                or any(saved['profiles'].get(sid) != profile for sid, profile in profiles.items())
                or calibration_execution(saved['config']) != calibration_execution(asdict(config))):
            raise ValueError('calibration scene/backend/robot/sensor/policy configuration mismatch')
        prior = rows(Path(calibration) / 'trials.jsonl')
    # 保存本次运行实际使用的配置及输入引用。
    mode_settings = None
    if config.mode_config is not None:
        from ..scenes.mode_config import load_mode_config
        mode_settings = asdict(load_mode_config(config.mode_config))
    root = create_run(Path(output_dir) / run_id, {
        'dataset': str(Path(dataset).resolve()), 'config': asdict(config), 'collection': asdict(collection),
        'navigation_protocol': NAVIGATION_PROTOCOL,
        'navigation_settings': asdict(NavigationSettings.from_config(config)),
        'profiles': profiles, 'gpu': gpu, 'seed': config.seed, 'calibration': str(Path(calibration).resolve()) if calibration else None,
        'resources': resources, **({'mode_settings': mode_settings,
            'mode_visibility': {'min_reference_pixels': geometry.get('min_reference_pixels', 24)}}
            if mode_settings is not None else {})})
    if resources and resources.get('split_planning'):
        from ..runtime.resources import calibration_recording
        saved = read_config(root / 'config.json')
        saved['calibration_collection'] = asdict(calibration_recording(collection, resources['recording_mode']))
        write_json(root / 'config.json', saved)
    return root, scenes, config, collection, prior


def _run_dataset(dataset, assets_dir, config, collection, output_dir, process_scene, *, run_id,
                 gpu_ids, samples=None, calibration=None, max_trials=None, station_workers=1,
                 egl_devices=None, reuse_planners=True, cpu_workers=60, preparation_workers=12,
                 recording_mode='numerical', planning_workers_per_gpu=1):
    """统一准备运行、遍历场景，并关闭资源和保存汇总结果。"""
    # 设置计算与渲染设备，再准备场景和运行目录。
    started = time.monotonic()
    split = config.operation_backend == 'curobo' and config.shared_station_preparation
    if station_workers < 1:
        raise ValueError('station_workers must be positive')
    if station_workers > 1 and config.operation_backend != 'curobo':
        raise ValueError('parallel station evaluation currently uses cuRobo')
    egl_devices = list(gpu_ids) if egl_devices is None else egl_devices
    if len(egl_devices) != len(gpu_ids):
        raise ValueError('provide one EGL device index per GPU')
    resources = {'station_workers': station_workers, 'gpu_ids': list(gpu_ids),
                 'egl_devices': list(egl_devices), 'reuse_planners': reuse_planners}
    split_resources = None
    if split:
        from ..runtime.resources import RunResources
        split_resources = RunResources(tuple(gpu_ids), cpu_workers, preparation_workers,
                                       recording_mode, reuse_planners, planning_workers_per_gpu)
        resources.update(asdict(split_resources), split_planning=True)
    station_options = dict(workers=station_workers, gpu_ids=gpu_ids, egl_devices=egl_devices,
                           reuse_planners=reuse_planners)
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(egl_devices[0]), MUJOCO_GL='egl')
    root, scenes, config, collection, prior = _prepare_run(
        dataset, config, collection, output_dir, run_id, gpu_ids[0], samples, calibration, resources=resources,
        **({'dataset_split': 'train'} if config.mode_config is not None and process_scene is _collect_scene else {}))
    logger.info('运行=%s scene数=%s backend=%s GPU=%s 每点重复=%s 每场景预算=%s 标定来源=%s',
                root, len(scenes), config.operation_backend, gpu_ids[0], config.trials_per_station,
                max_trials if max_trials is not None else '完整标定', calibration or '当前 run 内执行')
    summaries = []
    attempt_ids = count()
    policy = None
    collecting = process_scene is _collect_scene
    pairs = []
    stopped = False
    calibration_summary = None
    diagnosis_summary = None
    try:
        if config.operation_backend in ('molmobot', 'pi05', 'pyrua'):
            from ..policies.config import create_policy
            logs = Path(output_dir) / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            policy = create_policy(config.policy_config, logs / f'{run_id}-policy.log')
        if split and prior is None:
            from .station_calibration import calibrate_stations
            from ..runtime.resources import calibration_recording
            prior, calibration_summary = calibrate_stations(scenes, assets_dir, config,
                calibration_recording(collection, recording_mode), root, split_resources,
                attempt_ids=attempt_ids, max_trials=max_trials)
            stopped = bool(calibration_summary['failure'])
        # 无外部标定时，先完成全部场景的标定，再生成全量计划。
        if collecting:
            if prior is None:
                prior = []
                for scene in scenes:
                    sim = Simulation.from_snapshot(scene['scene_path'], scene['robot_config'], target=scene['task']['target_body'])
                    sim.scene_id = scene['scene_id']
                    try:
                        current = _station_trials(sim, scene, assets_dir, config, collection, root,
                                                  budget=max_trials, attempt_ids=attempt_ids, **station_options)
                        prior.extend(current)
                    finally:
                        sim.close()
                    if any(_shared_failure(row) for row in current):
                        stopped = True
                        break
            diagnoses = None
            if config.mode_config is not None:
                from ..data.evaluation.diagnosis import load_diagnoses
                if calibration:
                    diagnoses = load_diagnoses(calibration, scenes, read_json(root / 'config.json')['mode_settings'])
                else:
                    from .station_calibration import diagnose_stations
                    results, diagnosis_summary = diagnose_stations(scenes, prior, assets_dir, config, root,
                        gpu_ids, allow_probes=not stopped)
                    diagnoses = {row['scene_id']: row for row in results}
                    stopped = stopped or bool(diagnosis_summary['failure'])
            pairs = _plan_collection(scenes, prior, config, root,
                                     os.path.relpath(Path(calibration).resolve(), root.resolve()) if calibration else '.',
                                     **({'diagnoses': diagnoses} if diagnoses is not None else {}))
        # 策略客户端跨场景复用，仿真实例逐场景创建并关闭。
        for index, scene in enumerate(scenes, 1):
            if stopped and (collecting or not split):
                break
            logger.info('场景 %s/%s 开始 scene=%s target=%s station数=%s',
                        index, len(scenes), scene['scene_id'], scene['task'].get('asset_id', scene['task']['task_id']),
                        len(scene['stations']), extra={'progress': True})
            robot = scene['robot_config']
            sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
            sim.scene_id = scene['scene_id']
            try:
                summary = process_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials,
                                        attempt_ids=attempt_ids,
                                        **({'pairs': pairs} if collecting else {'station_options': station_options}))
                summaries.append(summary)
                stopped = stopped or summary.get('shared_failure', False)
                logger.info('场景结束 %s', scene['scene_id'], extra={'progress': True})
            finally:
                sim.close()
        if config.mode_config is not None and not collecting:
            from .station_calibration import diagnose_stations
            raw_trials = prior if prior is not None else rows(root / 'trials.jsonl')
            diagnoses, diagnosis_summary = diagnose_stations(scenes, raw_trials, assets_dir, config,
                root, gpu_ids, source_root=calibration, allow_probes=not stopped)
            by_scene = {row['scene_id']: row for row in diagnoses}
            for summary in summaries:
                diagnosis = by_scene[summary['scene_id']]
                mode_pairs = {(row['s0'], goal) for row in diagnosis['s0_results']
                         for label in row['labels'].values() if label['status'] == 'matched'
                         for goal in label['qualifying_s1_ids']}
                summary.update(task_eligibility=diagnosis['task_eligibility'],
                               intent_hit=diagnosis['intent_hit'], eligible_pairs=len(mode_pairs))
            stopped = stopped or bool(diagnosis_summary['failure'])
    finally:
        # 异常退出也关闭策略服务并保存已完成场景；运行完整不等于任务成功。
        if policy:
            policy.close()
        if collecting:
            if not pairs:
                pairs = rows(root / 'pair_index.jsonl')
            for pair in pairs:
                if pair['status'] == 'not_executed':
                    pair['reason'] = 'shared_failure' if stopped else 'run_interrupted'
            write_rows(root / 'pair_index.jsonl', pairs)
        training = rows(root / 'train_index.jsonl')
        executed_pairs = sum((pair.get('executed_steps') or 0) > 0 for pair in pairs)
        write_json(root / 'summary.json', {
            'status': 'completed' if len(summaries) == len(scenes) and not stopped else 'incomplete',
            'scenes': summaries, 'elapsed_s': time.monotonic() - started,
            **({'calibration': calibration_summary} if calibration_summary is not None else {}),
            **({'diagnosis': diagnosis_summary} if diagnosis_summary is not None else {}),
            **({'planned_pairs': len(pairs), 'processed_pairs': sum(p['status'] != 'not_executed' for p in pairs),
                'executed_pairs': executed_pairs, 'execution_coverage': executed_pairs / len(pairs) if pairs else 0.,
                'pair_status_counts': dict(Counter(p['status'] for p in pairs)),
                'train_source_counts': dict(Counter(row['source_type'] for row in training))} if collecting else {})})
        logger.info('运行收尾 输出=%s 已处理场景=%s/%s 耗时=%.1fs',
                    root, len(summaries), len(scenes), time.monotonic() - started)
        if len(summaries) != len(scenes) or stopped:
            logger.warning('运行未完整结束：已处理场景=%s/%s', len(summaries), len(scenes))
    return root


def evaluate_stations(dataset, assets_dir, config, collection, output_dir, *, run_id,
                      gpu_ids, samples=None, max_trials=None, station_workers=1, egl_devices=None, reuse_planners=True,
                      cpu_workers=60, preparation_workers=12, recording_mode='numerical',
                      planning_workers_per_gpu=1):
    """执行逐站位试验并导出热图，不执行 S0→S1 连续采集。"""
    return _run_dataset(dataset, assets_dir, config, collection, output_dir, _evaluate_scene,
        run_id=run_id, gpu_ids=gpu_ids, samples=samples, max_trials=max_trials,
        station_workers=station_workers, egl_devices=egl_devices, reuse_planners=reuse_planners,
        cpu_workers=cpu_workers, preparation_workers=preparation_workers, recording_mode=recording_mode,
        planning_workers_per_gpu=planning_workers_per_gpu)


def evaluate_recipe(recipe, assets_dir, config, collection, output_dir, *, run_id, gpu_ids,
                    build_workers=1, exclude_categories=None, **options):
    """Prepare one-shot no-edit scene inputs from a recipe, then calibrate them."""
    from .scene_datasets import prepare_scene_inputs
    input_root = Path(output_dir) / '_calibration_inputs' / run_id
    logger.info('直接标定准备 recipe=%s 输入=%s build_workers=%s exclude=%s',
                recipe, input_root, build_workers, exclude_categories or [])
    prepare_scene_inputs(recipe, input_root, gpu_ids=gpu_ids, workers=build_workers,
                         exclude_categories=exclude_categories)
    return evaluate_stations(input_root, assets_dir, config, collection, output_dir,
                             run_id=run_id, gpu_ids=gpu_ids, **options)


def collect_lastmile(dataset, assets_dir, config, collection, output_dir, *, run_id,
                     gpu_ids, samples=None, calibration=None, max_trials=None,
                     station_workers=1, egl_devices=None, reuse_planners=True,
                     cpu_workers=60, preparation_workers=12, recording_mode='numerical',
                     planning_workers_per_gpu=1):
    """复用匹配的标定或先完成标定，再用 A* 导航与 cuRobo V2 连续采集。"""
    if config.operation_backend != 'curobo':
        raise ValueError('continuous collection currently uses cuRobo')
    return _run_dataset(dataset, assets_dir, config, collection, output_dir, _collect_scene,
        run_id=run_id, gpu_ids=gpu_ids, samples=samples, calibration=calibration, max_trials=max_trials,
        station_workers=station_workers, egl_devices=egl_devices, reuse_planners=reuse_planners,
        cpu_workers=cpu_workers, preparation_workers=preparation_workers, recording_mode=recording_mode,
        planning_workers_per_gpu=planning_workers_per_gpu)
