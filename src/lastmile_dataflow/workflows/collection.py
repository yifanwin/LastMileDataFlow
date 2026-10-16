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
from ..utils.io import append_row, create_run, rows, write_json
from ..utils.logging_config import Progress, trial_logging
from ..runtime.simulation import Simulation
from ..runtime.execution.curobo import run_curobo_attempt
from ..runtime.execution.molmobot import run_vla_attempt
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import execution_profile
from ..data.evaluation.selection import station_calibration, recovery_choices, evaluable
from ..data.evaluation.statistics import aggregate
from ..scenes.tasks.discovery import grasp_candidates, robot_footprint
from ..planning.astar import scene_grid, search, task_support_obstacles
from ..data.visualization.heatmap import export_heatmap

logger = logging.getLogger(__name__)


def _trial_record(result, scene, station, trial, seed):
    """提取执行结果，补齐场景、站位、试验序号和随机种子。"""
    record = {key: result.get(key) for key in ('status', 'reason', 'execution_termination',
        'task_outcome', 'recording_complete', 'executed_steps', 'navigation', 'error')}
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
    if config.operation_backend == 'molmobot':
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


def _recovery_trials(baseline, scene, choices, assets_dir, config, collection, root, *, attempt_ids):
    """从困难起点 S0 导航至候选站位 S1，再执行操作并记录结果。"""
    if not choices:
        logger.info('scene=%s 无合格 S0/S1 配对，不生成连续执行轨迹', scene['scene_id'])
        return []
    # 无配对时直接返回；有配对才构建包含机器人尺寸和导航余量的栅格。
    grid = scene_grid(baseline, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m,
                      robot_footprint(baseline)['radius_m'], config.navigation_margin_m,
                      support_obstacles=task_support_obstacles(baseline, scene['task']))
    episodes = []
    planned = sum(len(goals[:config.max_s1_candidates or 5]) for _, goals, _ in choices)
    progress = Progress(logger)
    started = time.monotonic()
    outcomes = Counter()
    logger.info('连续采集 scene=%s 计划配对=%s', scene['scene_id'], planned, extra={'progress': True})
    try:
        for start, goals, _ in choices:
            for goal in goals[:config.max_s1_candidates or 5]:
                label = f"scene={scene['scene_id']} S0={start['station_id']} S1={goal['station_id']}"
                with trial_logging(label, progress):
                    path = search(grid, start['xy'], goal['xy'])
                    # 无路径只记录负结果，不调用执行器或生成虚假的轨迹。
                    if not path:
                        logger.debug('scene=%s S0=%s S1=%s 无 A* 路径',
                                    scene['scene_id'], start['station_id'], goal['station_id'])
                        record = {'scene_id': scene['scene_id'], 's0': start['station_id'], 's1': goal['station_id'],
                                  'status': 'path_unavailable', 'reason': 'astar_no_path', 'rollout_id': None}
                    else:
                        result = _execute(baseline, scene, start, assets_dir, config, collection, root,
                                         config.seed + len(episodes), path=path, goal=goal, attempt_ids=attempt_ids)
                        record = _trial_record(result, scene, start, None, config.seed + len(episodes))
                        record.update(s0=start['station_id'], s1=goal['station_id'])
                append_row(root / 'episodes.jsonl', record)
                episodes.append(record)
                outcomes[record['status']] += 1
                progress.update('连续采集 scene=%s 已记录配对=%s/%s 无路径=%s 当前S0=%s S1=%s 耗时=%.1fs',
                                scene['scene_id'], len(episodes), planned, outcomes['path_unavailable'],
                                start['station_id'], goal['station_id'], time.monotonic() - started)
    finally:
        progress.update('连续采集收尾 scene=%s 已记录配对=%s/%s 无路径=%s 结果=%s 耗时=%.1fs',
                        scene['scene_id'], len(episodes), planned, outcomes['path_unavailable'],
                        dict(outcomes), time.monotonic() - started, force=True)
    return episodes


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
    choices = recovery_choices(scene['stations'], rates, binding, config)
    logger.info('scene=%s 标定 trial=%s complete=%s 合格配对=%s 热图=%s',
                scene['scene_id'], len(trials), rates['complete'], len(choices), root / 'figures' / scene['scene_id'])
    summary = {'scene_id': scene['scene_id'], 'calibration_complete': rates['complete'],
               'eligible_pairs': len(choices), 'successes': 0}
    return summary, choices


def _evaluate_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids):
    """完成单个场景的站位标定和汇总，不执行连续采集。"""
    trials = _station_trials(sim, scene, assets_dir, config, collection, root, policy, max_trials, attempt_ids=attempt_ids)
    summary, _ = _station_summary(sim, scene, trials, config, root)
    return summary


def _collect_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids):
    # 复用标定时只读取当前场景的试验，不重复执行站位标定。
    """复用或执行场景标定，再连续采集并统计成功次数。"""
    trials = [row for row in prior if row['scene_id'] == scene['scene_id']] if prior is not None else _station_trials(
        sim, scene, assets_dir, config, collection, root, policy, max_trials, attempt_ids=attempt_ids)
    summary, choices = _station_summary(sim, scene, trials, config, root)
    episodes = _recovery_trials(sim, scene, choices, assets_dir, config, collection, root, attempt_ids=attempt_ids)
    # 连续采集成功要求操作成功、导航成功且记录完整。
    summary['successes'] = sum(row['status'] == 'success' and row.get('navigation', {}).get('status') == 'success'
                               and row.get('recording_complete', False) for row in episodes)
    return summary


def _prepare_run(dataset, config, collection, output_dir, run_id, gpu, samples, calibration):
    """对齐场景与执行配置，检查标定复用条件并创建运行目录。"""
    # 沿用数据集生成时的几何和传感器尺寸，保持采样与执行一致。
    geometry = read_config(Path(dataset) / 'config.json').get('geometry', {})
    keys = ('radius_m', 'spacing_m', 'width', 'height', 'min_target_pixels', 'map_resolution_m', 'navigation_margin_m')
    config = replace(config, **{key: geometry[key] for key in keys if key in geometry})
    collection = replace(collection, width=config.width, height=config.height, seed=config.seed,
                         output_dir=str(Path(output_dir).resolve()))
    scenes = load_scenes(dataset, samples)
    if not scenes:
        raise ValueError('no accepted scenes')
    # 检查场景控制模式，并生成各场景的执行配置档案。
    profiles = {}
    for scene in scenes:
        robot = RobotConfig(**read_config(Path(scene['scene_path']) / 'config.json')['robot'])
        if (robot.control_profile == 'molmobot-native-v1') != (config.operation_backend == 'molmobot'):
            raise ValueError('scene control profile incompatible with requested backend; construct an explicit matching scene')
        scene['robot_config'] = robot
        profiles[scene['scene_id']] = execution_profile(robot, config)
    prior = None
    # 仅复用执行配置及各场景执行档案均匹配的标定。
    if calibration:
        logger.info('复用已有标定=%s，不重复 rollout', calibration)
        saved = read_config(Path(calibration) / 'config.json')
        if saved['profiles'] != profiles or saved['config'] != asdict(config):
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
    try:
        if config.operation_backend == 'molmobot':
            from ..policies.molmobot import MolmoBotClient
            logs = Path(output_dir) / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            policy = MolmoBotClient(config.policy_config, logs / f'{run_id}-policy.log')
        # 策略客户端跨场景复用，仿真实例逐场景创建并关闭。
        for index, scene in enumerate(scenes, 1):
            logger.info('场景 %s/%s 开始 scene=%s target=%s station数=%s',
                        index, len(scenes), scene['scene_id'], scene['task'].get('asset_id', scene['task']['task_id']),
                        len(scene['stations']), extra={'progress': True})
            robot = scene['robot_config']
            sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
            sim.scene_id = scene['scene_id']
            try:
                summaries.append(process_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, attempt_ids=attempt_ids))
                logger.info('场景结束 %s', scene['scene_id'], extra={'progress': True})
            finally:
                sim.close()
    finally:
        # 异常退出也关闭策略服务并保存已完成场景；运行完整不等于任务成功。
        if policy:
            policy.close()
        write_json(root / 'summary.json', {'status': 'completed' if len(summaries) == len(scenes) else 'incomplete',
            'scenes': summaries, 'elapsed_s': time.monotonic() - started})
        logger.info('运行收尾 输出=%s 已处理场景=%s/%s 耗时=%.1fs',
                    root, len(summaries), len(scenes), time.monotonic() - started)
        if len(summaries) != len(scenes):
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
