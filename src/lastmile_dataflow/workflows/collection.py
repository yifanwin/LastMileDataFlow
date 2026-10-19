"""统一编排站位标定与连续采集，共用运行准备、场景遍历和资源收尾。"""
import logging
from collections import Counter
from dataclasses import asdict, replace
import os
from itertools import count
from pathlib import Path
import time

from ..config import read_config
from ..config import RobotConfig
from ..utils.io import append_row, create_run, rows, write_json, write_rows
from ..utils.logging_config import Progress, trial_logging
from ..runtime.simulation import Simulation
from ..runtime.execution.curobo import run_curobo_attempt
from ..runtime.execution.vla import run_vla_attempt
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import execution_profile, calibration_execution
from ..data.evaluation.selection import station_calibration, eligible_stations, generate_pairs, evaluable
from ..data.evaluation.statistics import aggregate
from ..data.export.lastmile_dataset import successful, training_record
from ..scenes.tasks.discovery import grasp_candidates, robot_footprint
from ..planning.astar import scene_grid, search, task_support_obstacles
from ..data.visualization.heatmap import export_heatmap

logger = logging.getLogger(__name__)


def _trial_record(result, scene, station, trial, seed):
    """提取执行结果，补齐场景、站位、试验序号和随机种子。"""
    record = {key: result.get(key) for key in ('status', 'reason', 'execution_termination',
        'task_outcome', 'recording_complete', 'executed_steps', 'navigation', 'error', 'failure_scope')}
    record.update(scene_id=scene['scene_id'], station_id=station['station_id'], trial=trial, seed=seed,
                  rollout_id=result.get('rollout_id'), task_id=scene['task']['task_id'])
    return record


def _execute(baseline, scene, station, assets_dir, config, collection, root, seed, policy=None, path=None, goal=None, *, attempt_ids):
    # 分配本次运行内唯一的试验编号。
    """调用指定后端执行一次试验，共用试验编号和记录链路。"""
    attempt_id = f'r{next(attempt_ids):06d}'
    task = scene['task']
    logger.debug('试验开始 %s scene=%s station=%s backend=%s seed=%s',
                attempt_id, scene['scene_id'], station['station_id'], config.operation_backend, seed)
    started = time.monotonic()
    # 按后端选择执行器，导航路径和目标站位沿用同一接口。
    if config.operation_backend in ('molmobot', 'pi05'):
        result = run_vla_attempt(baseline, task, station, config, collection, root=root, attempt_id=attempt_id,
                               seed=seed, policy=policy, path=path, goal=goal)
    else:
        result = run_curobo_attempt(baseline, task, station, grasp_candidates(task, assets_dir), assets_dir,
                           config, collection, root=root, attempt_id=attempt_id, seed=seed, path=path, goal=goal)
    # 根据执行状态选择日志级别，不改变原始结果。
    log_result = logger.debug
    if result.get('status') in ('initialization_error', 'episode_error', 'recording_error', 'simulation_failure', 'infrastructure_error'):
        log_result = logger.error
    elif result.get('status') == 'timeout':
        log_result = logger.warning
    log_result('试验结束 %s status=%s reason=%s steps=%s recording_complete=%s elapsed=%.1fs', attempt_id,
                result.get('status'), result.get('reason'), result.get('executed_steps'), result.get('recording_complete'), time.monotonic() - started)
    return result


def _station_trials(baseline, scene, assets_dir, config, collection, root, policy=None, budget=None, *, attempt_ids):
    """逐站位重复标定，达到预算或发生共享资源故障时停止。"""
    trials = []
    total = len(scene['stations']) * config.trials_per_station
    planned = total if budget is None else min(total, budget)
    started = time.monotonic()
    progress = Progress(logger)
    outcomes, valid_points, reasons = Counter(), Counter(), Counter()
    try:
        for index, station in enumerate(scene['stations']):
            for trial in range(config.trials_per_station):
                if budget is not None and len(trials) >= budget:
                    logger.warning('scene=%s 标定达到预算=%s，停止新增 trial', scene['scene_id'], budget)
                    return trials
                seed = config.seed + index * config.trials_per_station + trial
                label = f"scene={scene['scene_id']} station={station['station_id']} trial={trial + 1}/{config.trials_per_station}"
                with trial_logging(label, progress):
                    result = _execute(baseline, scene, station, assets_dir, config, collection, root,
                                      seed, policy, attempt_ids=attempt_ids)
                record = _trial_record(result, scene, station, trial, seed)
                append_row(root / 'trials.jsonl', record)
                trials.append(record)
                outcomes[record['status']] += 1
                if evaluable(record):
                    valid_points[station['station_id']] += 1
                if record['status'] != 'success':
                    reasons[record['reason']] += 1
                progress.update(
                    'scene=%s 已处理站位=%s/%s trial=%s/%s 当前站位=%s 重复=%s/%s 成功=%s 耗时=%.1fs',
                    scene['scene_id'], len(trials) // config.trials_per_station, len(scene['stations']),
                    len(trials), planned, station['station_id'], trial + 1, config.trials_per_station,
                    outcomes['success'], time.monotonic() - started)
                if result.get('failure_scope') == 'shared' or record['status'] == 'infrastructure_error':
                    logger.error('scene=%s 标定因共享资源故障提前结束：%s', scene['scene_id'], record['reason'])
                    return trials
    finally:
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


def _plan_collection(scenes, trials, config, root, source):
    """先落盘全部 pair 和已有成功示教，再启动任何连续执行。"""
    pairs, training, seen = [], [], set()
    for scene in scenes:
        current = [row for row in trials if row['scene_id'] == scene['scene_id']]
        binding = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}
        if any(row['task_id'] != binding['task_id'] for row in current):
            raise ValueError('calibration task mismatch')
        rates = station_calibration(scene['stations'], current, config.trials_per_station, binding, config)
        for start, goal in generate_pairs(*eligible_stations(scene['stations'], rates, config)):
            distance = sum((a - b) ** 2 for a, b in zip(start['xy'], goal['xy'])) ** .5
            pairs.append({**binding, 'pair_id': f'p{len(pairs):06d}',
                          's0': start['station_id'], 's1': goal['station_id'],
                          's0_success_rate': start['success_rate'], 's1_success_rate': goal['success_rate'],
                          'seed': config.seed + len(pairs), 'status': 'not_executed', 'reason': 'pending',
                          'euclidean_distance_m': distance, 'astar_path_length_m': None,
                          'navigation_steps': 0, 'rollout_id': None})
        for row in current:
            if successful(row) and row['rollout_id'] not in seen:
                training.append(training_record(row, source, 's1_direct_operation'))
                seen.add(row['rollout_id'])
    write_rows(root / 'pair_index.jsonl', pairs)
    write_rows(root / 'train_index.jsonl', training)
    return pairs


def _recovery_trials(baseline, scene, pairs, assets_dir, config, collection, root, *, attempt_ids):
    """执行当前场景的全部 pair；完整计划始终保留在 pair_index。"""
    current = [pair for pair in pairs if pair['scene_id'] == scene['scene_id']]
    if not current:
        return []
    footprint = robot_footprint(baseline, whole_body=config.navigation_whole_body)
    grid = scene_grid(baseline, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m,
                      footprint['radius_m'], config.navigation_margin_m, robot_height=footprint['max'][2] if config.navigation_whole_body else .4,
                      support_obstacles=task_support_obstacles(baseline, scene['task']))
    stations = {station['station_id']: station for station in scene['stations']}
    progress = Progress(logger)
    processed = []
    try:
        for pair in current:
            start, goal = stations[pair['s0']], stations[pair['s1']]
            label = f"scene={scene['scene_id']} S0={pair['s0']} S1={pair['s1']}"
            with trial_logging(label, progress):
                path = search(grid, start['xy'], goal['xy'])
                if not path:
                    pair.update(status='path_unavailable', reason='astar_no_path')
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
                    if successful(pair, continuous=True):
                        append_row(root / 'train_index.jsonl', training_record(pair, '.', 's0_to_s1_continuous'))
            processed.append(pair)
            write_rows(root / 'pair_index.jsonl', pairs)
            progress.update('连续采集 scene=%s 已处理=%s/%s 当前pair=%s status=%s',
                            scene['scene_id'], len(processed), len(current), pair['pair_id'], pair['status'])
            if _shared_failure(pair):
                break
    finally:
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
    # 热图仅用于展示；困难配对仍由原始标定结果决定。
    grid = scene_grid(sim, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m, 0., 0.,
                      support_obstacles=task_support_obstacles(sim, scene['task']))
    display = [{**row, 'attempt': row.get('rollout_id')} for row in trials]
    export_heatmap(root / 'figures' / scene['scene_id'], grid,
        aggregate(scene['stations'], display, config.trials_per_station), scene['task']['anchor_world'],
        config.smoothing_sigma_m or config.spacing_m or .1, title=scene['scene_id'])
    starts, goals = eligible_stations(scene['stations'], rates, config)
    pair_count = len(starts) * len(goals)
    logger.info('scene=%s 标定 trial=%s complete=%s 合格配对=%s 热图=%s',
                scene['scene_id'], len(trials), rates['complete'], pair_count, root / 'figures' / scene['scene_id'])
    summary = {'scene_id': scene['scene_id'], 'calibration_complete': rates['complete'],
               'eligible_pairs': pair_count, 'successes': 0}
    return summary


def _evaluate_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids):
    """完成单个场景的站位标定和汇总，不执行连续采集。"""
    trials = _station_trials(sim, scene, assets_dir, config, collection, root, policy, max_trials, attempt_ids=attempt_ids)
    summary = _station_summary(sim, scene, trials, config, root)
    summary['shared_failure'] = any(_shared_failure(row) for row in trials)
    return summary


def _collect_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids, pairs):
    """标定已在规划前完成；当前场景只展示率表并执行全组合。"""
    trials = [row for row in prior if row['scene_id'] == scene['scene_id']]
    summary = _station_summary(sim, scene, trials, config, root)
    episodes = _recovery_trials(sim, scene, pairs, assets_dir, config, collection, root, attempt_ids=attempt_ids)
    summary['successes'] = sum(successful(row, continuous=True) for row in episodes)
    summary['shared_failure'] = any(_shared_failure(row) for row in episodes)
    return summary


def _prepare_run(dataset, config, collection, output_dir, run_id, gpu, samples, calibration):
    """对齐场景与执行配置，检查标定复用条件并创建运行目录。"""
    # 沿用数据集生成时的几何和传感器尺寸，保持采样与执行一致。
    geometry = read_config(Path(dataset) / 'config.json').get('geometry', {})
    keys = ('radius_m', 'spacing_m', 'width', 'height', 'min_target_pixels', 'map_resolution_m', 'navigation_margin_m', 'navigation_whole_body', 'require_target_visibility', 'require_navigation_clearance')
    config = replace(config, **{key: geometry[key] for key in keys if key in geometry})
    collection = replace(collection, width=config.width, height=config.height, seed=config.seed,
                         output_dir=str(Path(output_dir).resolve()))
    scenes = load_scenes(dataset, samples)
    if not scenes:
        raise ValueError('no accepted scenes')
    # 检查场景控制模式，并生成各场景的执行配置档案。
    profiles = {}
    for scene in scenes:
        from ..config import robot_config
        robot = robot_config(read_config(Path(scene['scene_path']) / 'config.json')['robot'])
        from ..policies.config import check_profile
        check_profile(robot,config.operation_backend,config.policy_config)
        scene['robot_config'] = robot
        profiles[scene['scene_id']] = execution_profile(robot, config)
    prior = None
    # 仅复用执行配置及各场景执行档案均匹配的标定。
    if calibration:
        logger.info('复用已有标定=%s，不重复 rollout', calibration)
        saved = read_config(Path(calibration) / 'config.json')
        if saved['profiles'] != profiles or calibration_execution(saved['config']) != calibration_execution(asdict(config)):
            raise ValueError('calibration scene/backend/robot/sensor/policy configuration mismatch')
        prior = rows(Path(calibration) / 'trials.jsonl')
    # 保存本次运行实际使用的配置及输入引用。
    root = create_run(Path(output_dir) / run_id, {
        'dataset': str(Path(dataset).resolve()), 'config': asdict(config), 'collection': asdict(collection),
        'profiles': profiles, 'gpu': gpu, 'seed': config.seed, 'calibration': str(Path(calibration).resolve()) if calibration else None})
    return root, scenes, config, collection, prior


def _run_dataset(dataset, assets_dir, config, collection, output_dir, process_scene, *, run_id,
                 gpu_ids, samples=None, calibration=None, max_trials=None):
    """统一准备运行、遍历场景，并关闭资源和保存汇总结果。"""
    # 设置计算与渲染设备，再准备场景和运行目录。
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]), MUJOCO_GL='egl')
    root, scenes, config, collection, prior = _prepare_run(
        dataset, config, collection, output_dir, run_id, gpu_ids[0], samples, calibration)
    logger.info('运行=%s scene数=%s backend=%s GPU=%s 每点重复=%s 每场景预算=%s 标定来源=%s',
                root, len(scenes), config.operation_backend, gpu_ids[0], config.trials_per_station,
                max_trials if max_trials is not None else '完整标定', calibration or '当前 run 内执行')
    started = time.monotonic()
    summaries = []
    attempt_ids = count()
    policy = None
    collecting = process_scene is _collect_scene
    pairs = []
    stopped = False
    try:
        if config.operation_backend in ('molmobot', 'pi05'):
            from ..policies.config import create_policy
            logs = Path(output_dir) / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            policy = create_policy(config.policy_config, logs / f'{run_id}-policy.log')
        # 无外部标定时，先完成全部场景的标定，再生成全量计划。
        if collecting:
            if prior is None:
                prior = []
                for scene in scenes:
                    sim = Simulation.from_snapshot(scene['scene_path'], scene['robot_config'], target=scene['task']['target_body'])
                    sim.scene_id = scene['scene_id']
                    try:
                        current = _station_trials(sim, scene, assets_dir, config, collection, root,
                                                  budget=max_trials, attempt_ids=attempt_ids)
                        prior.extend(current)
                    finally:
                        sim.close()
                    if any(_shared_failure(row) for row in current):
                        stopped = True
                        break
            pairs = _plan_collection(scenes, prior, config, root,
                                     os.path.relpath(Path(calibration).resolve(), root.resolve()) if calibration else '.')
        # 策略客户端跨场景复用，仿真实例逐场景创建并关闭。
        for index, scene in enumerate(scenes, 1):
            if stopped:
                break
            logger.info('场景 %s/%s 开始 scene=%s target=%s station数=%s',
                        index, len(scenes), scene['scene_id'], scene['task'].get('asset_id', scene['task']['task_id']),
                        len(scene['stations']), extra={'progress': True})
            robot = scene['robot_config']
            sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
            sim.scene_id = scene['scene_id']
            try:
                summary = process_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials,
                                        attempt_ids=attempt_ids, **({'pairs': pairs} if collecting else {}))
                summaries.append(summary)
                stopped = summary.get('shared_failure', False)
                logger.info('场景结束 %s', scene['scene_id'], extra={'progress': True})
            finally:
                sim.close()
    finally:
        # 异常退出也关闭策略服务并保存已完成场景；运行完整不等于任务成功。
        if policy:
            policy.close()
        if collecting:
            for pair in pairs:
                if pair['status'] == 'not_executed':
                    pair['reason'] = 'shared_failure' if stopped else 'run_interrupted'
            write_rows(root / 'pair_index.jsonl', pairs)
        training = rows(root / 'train_index.jsonl')
        executed_pairs = sum((pair.get('executed_steps') or 0) > 0 for pair in pairs)
        write_json(root / 'summary.json', {
            'status': 'completed' if len(summaries) == len(scenes) and not stopped else 'incomplete',
            'scenes': summaries, 'elapsed_s': time.monotonic() - started,
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
                      gpu_ids, samples=None, max_trials=None):
    """执行逐站位试验并导出热图，不执行 S0→S1 连续采集。"""
    return _run_dataset(dataset, assets_dir, config, collection, output_dir, _evaluate_scene,
        run_id=run_id, gpu_ids=gpu_ids, samples=samples, max_trials=max_trials)


def collect_lastmile(dataset, assets_dir, config, collection, output_dir, *, run_id,
                     gpu_ids, samples=None, calibration=None, max_trials=None):
    """复用匹配的标定或先完成标定，再用 A* 导航与 cuRobo V2 连续采集。"""
    if config.operation_backend != 'curobo':
        raise ValueError('continuous collection currently uses cuRobo')
    return _run_dataset(dataset, assets_dir, config, collection, output_dir, _collect_scene,
        run_id=run_id, gpu_ids=gpu_ids, samples=samples, calibration=calibration, max_trials=max_trials)
