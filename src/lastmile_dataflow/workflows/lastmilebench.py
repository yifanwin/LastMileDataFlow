"""最小困难 S0 定义与串行策略评测。"""
import logging
from dataclasses import asdict, replace
import os
import time
from itertools import count
from pathlib import Path

from ..config import read_config
from ..data.evaluation.benchmark import select_starts, summarize
from ..config import CollectionConfig, RobotConfig
from ..utils.logging_config import Progress, trial_logging
from ..utils.io import append_row, create_run, rows, write_json
from ..runtime.simulation import Simulation
from ..runtime.construction.benchmark_scene import native_scene
from ..runtime.execution.molmobot import run_vla_attempt
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import ExecutionConfig
from ..data.evaluation.selection import station_calibration
from ..policies.molmobot import MolmoBotClient
from ..policies.molmobot_config import load_policy_config
from ..runtime.execution_config import execution_profile

logger = logging.getLogger(__name__)


def build(dataset, output, policy_path, *, gpu_ids, starts_per_sample=1, calibration_dir=None,
          allow_uncalibrated=False, seed=42, horizon=400, wall_timeout_s=600., radius_m=1.5):
    if not calibration_dir and not allow_uncalibrated:
        raise ValueError('difficulty requires matching measured calibration')
    if not calibration_dir:
        logger.warning('未标定 smoke：结果不能作为困难 S0 评测')
    os.environ['MUJOCO_EGL_DEVICE_ID'] = str(gpu_ids[0])
    source = Path(dataset).resolve()
    calibrations = rows(Path(calibration_dir) / 'trials.jsonl') if calibration_dir else []
    cal_config = read_config(Path(calibration_dir) / 'config.json') if calibration_dir else None
    policy = load_policy_config(policy_path)
    config = ExecutionConfig(operation_backend='molmobot', operation_base_mode='policy_relative',
        policy_config=str(Path(policy_path).resolve()), width=1024, height=576, radius_m=radius_m)
    root = create_run(output, {'dataset': str(source), 'policy': asdict(policy), 'seed': seed,
        'horizon': horizon, 'wall_timeout_s': wall_timeout_s, 'radius_m': radius_m,
        'calibration': str(Path(calibration_dir).resolve()) if calibration_dir else None})
    episode_ids = count()
    scenes = load_scenes(source)
    progress = Progress(logger)
    started = time.monotonic()
    screened = written = 0
    logger.info('构建 benchmark 场景数=%s 输出=%s 标定=%s',
                len(scenes), root, calibration_dir or '未标定 smoke', extra={'progress': True})
    try:
        for scene in scenes:
            progress.update('构建 benchmark 已筛选场景=%s/%s 当前场景=%s episode=%s',
                            screened, len(scenes), scene['scene_id'], written)
            robot = RobotConfig(**read_config(Path(scene['scene_path']) / 'config.json')['robot'])
            parent = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
            sim = None
            try:
                sim = native_scene(parent)
                if cal_config and cal_config['profiles'].get(scene['scene_id']) != execution_profile(sim.robot.config, config):
                    raise ValueError('benchmark calibration does not match native robot/sensor/policy configuration')
                station_rows = scene['stations']
                calibration = None
                if cal_config:
                    expected = cal_config['config']['trials_per_station']
                    subset = [r for r in calibrations if r['scene_id'] == scene['scene_id']]
                    calibration = station_calibration(station_rows, subset, expected,
                        {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}, config)
                    if not calibration['complete']:
                        raise ValueError('incomplete difficulty calibration')
                choices = select_starts(station_rows, calibration,
                    {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}, limit=starts_per_sample)
                screened += 1
                logger.debug('scene=%s 选中起点=%s', scene['scene_id'], len(choices))
                if not choices:
                    logger.warning('scene=%s 无合格起点，跳过 benchmark 样本构建', scene['scene_id'])
                    continue
                from ..scenes.stations.sampling import place_frozen_station, robot_contacts
                from ..data.rendering.head_views import head_frame
                from ..data.rendering.edit_views import ViewConfig
                from ..scenes.stations.initialization import floor_support
                relative = Path('scene-files') / scene['scene_id']
                sim.freeze(root / relative)
                for station, evidence in choices:
                    place_frozen_station(sim, station)
                    _, _, visibility = head_frame(sim, scene['task']['target_body'], ViewConfig(width=1024, height=576))
                    if robot_contacts(sim) or not floor_support(sim, station['xy']) or visibility['visible_pixels'] < 24:
                        raise ValueError('native benchmark station initialization failed')
                    episode_id = f'e{next(episode_ids):06d}'
                    append_row(root / 'episodes.jsonl', {'episode_id': episode_id, 'scene_id': scene['scene_id'],
                        'scene': str(relative), 'task': scene['task'], 'station': station, 'seed': seed,
                        'difficulty': evidence['status']})
                    written += 1
                    logger.debug('保存 episode=%s scene=%s station=%s difficulty=%s',
                                episode_id, scene['scene_id'], station['station_id'], evidence['status'])
            finally:
                if sim: sim.close()
                parent.close()
    finally:
        progress.update('benchmark 构建收尾 已筛选场景=%s/%s 已写入episode=%s 输出=%s 耗时=%.1fs',
                        screened, len(scenes), written, root, time.monotonic() - started, force=True)
    return root


def evaluate(benchmark, output, policy_path, *, gpu_ids, log_dir):
    benchmark = Path(benchmark).resolve()
    episodes = rows(benchmark / 'episodes.jsonl')
    if not episodes:
        raise ValueError('no benchmark episodes')
    settings = read_config(benchmark / 'config.json')
    policy_config = replace(load_policy_config(policy_path), task_horizon=settings['horizon'])
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]), MUJOCO_GL='egl')
    root = create_run(output, {'benchmark': str(benchmark), 'policy': asdict(policy_config),
                              'seed': settings['seed'], 'gpu': gpu_ids[0]})
    logger.info('评测 benchmark=%s 计划episode=%s 输出=%s', benchmark, len(episodes), root, extra={'progress': True})
    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    policy = MolmoBotClient(policy_path, logs / f'{root.name}-policy.log')
    policy.config = policy_config
    results = []
    progress = Progress(logger)
    started = time.monotonic()
    try:
        for index, episode in enumerate(episodes, 1):
            logger.debug('评测 %s/%s episode=%s scene=%s',
                        index, len(episodes), episode['episode_id'], episode['scene_id'])
            scene = benchmark / episode['scene']
            robot = RobotConfig(**read_config(scene / 'config.json')['robot'])
            sim = Simulation.from_snapshot(scene, robot, target=episode['task']['target_body'])
            sim.scene_id = episode['scene_id']
            try:
                config = ExecutionConfig(operation_backend='molmobot', operation_base_mode='policy_relative',
                    policy_config=str(policy_path), width=1024, height=576,
                    radius_m=settings['radius_m'], attempt_timeout_s=settings['wall_timeout_s'], third_person_enabled=False)
                collection = CollectionConfig(output_dir=str(root), width=1024, height=576,
                    seed=episode['seed'], max_steps=settings['horizon'], record_video=False)
                label = f"scene={episode['scene_id']} episode={episode['episode_id']} station={episode['station']['station_id']}"
                with trial_logging(label, progress):
                    result = run_vla_attempt(sim, episode['task'], episode['station'], config, collection,
                        root=root, attempt_id=episode['episode_id'], seed=episode['seed'], policy=policy)
                record = {key: result.get(key) for key in ('status', 'reason', 'executed_steps', 'recording_complete', 'rollout_id', 'execution_termination', 'task_outcome')}
                record['episode_id'] = episode['episode_id']
                append_row(root / 'results.jsonl', record)
                results.append(record)
                log_result = logger.debug
                if record['status'] in ('initialization_error', 'episode_error', 'recording_error', 'simulation_failure', 'infrastructure_error'):
                    log_result = logger.error
                elif record['status'] == 'timeout':
                    log_result = logger.warning
                log_result('episode=%s scene=%s station=%s status=%s reason=%s steps=%s recording_complete=%s',
                            episode['episode_id'], episode['scene_id'], episode['station']['station_id'], record['status'], record['reason'],
                            record['executed_steps'], record['recording_complete'])
                progress.update('benchmark 评测 已记录episode=%s/%s 当前=%s 耗时=%.1fs',
                                len(results), len(episodes), episode['episode_id'], time.monotonic() - started)
            finally:
                sim.close()
    finally:
        policy.close()
        summary = summarize(episodes, results)
        write_json(root / 'summary.json', summary)
        progress.update('benchmark 评测收尾 已记录episode=%s/%s 有效=%s 成功=%s 未知或未运行=%s '
                        '覆盖率=%.1f%% 任务成功率=%s 输出=%s 耗时=%.1fs',
                        summary['reported'], summary['scheduled'], summary['valid_task_outcomes'],
                        summary['successes'], summary['unknown_or_infra'], summary['coverage'] * 100,
                        summary['task_success_rate'], root, time.monotonic() - started, force=True)
        if not summary['complete']:
            logger.warning('benchmark 结果不完整，不能提供完整计划集合的任务成功率')
    return root
