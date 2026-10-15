"""最小困难 S0 定义与串行策略评测。"""
from dataclasses import asdict, replace
import os
from itertools import count
from pathlib import Path

from ..config import read_config
from ..data.evaluation.benchmark import select_starts, summarize
from ..config import CollectionConfig, RobotConfig
from ..io import append_row, create_run, rows, write_json
from ..runtime.simulation import Simulation
from ..runtime.construction.benchmark_scene import native_scene
from ..runtime.execution.molmobot import run_vla_attempt
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import ExecutionConfig
from ..data.evaluation.selection import station_calibration
from ..policies.molmobot import MolmoBotClient
from ..policies.molmobot_config import load_policy_config
from ..runtime.execution_config import execution_profile


def build(dataset, output, policy_path, *, gpu_ids, starts_per_sample=1, calibration_dir=None,
          allow_uncalibrated=False, seed=20261012, horizon=400, wall_timeout_s=600., radius_m=1.5):
    if not calibration_dir and not allow_uncalibrated:
        raise ValueError('difficulty requires matching measured calibration')
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
    for scene in load_scenes(source):
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
            if not choices:
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
        finally:
            if sim: sim.close()
            parent.close()
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
    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    policy = MolmoBotClient(policy_path, logs / f'{root.name}-policy.log')
    policy.config = policy_config
    results = []
    try:
        for episode in episodes:
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
                result = run_vla_attempt(sim, episode['task'], episode['station'], config, collection,
                    root=root, attempt_id=episode['episode_id'], seed=episode['seed'], policy=policy)
                record = {key: result.get(key) for key in ('status', 'reason', 'executed_steps', 'recording_complete', 'rollout_id', 'execution_termination', 'task_outcome')}
                record['episode_id'] = episode['episode_id']
                append_row(root / 'results.jsonl', record)
                results.append(record)
            finally:
                sim.close()
    finally:
        policy.close()
        write_json(root / 'summary.json', summarize(episodes, results))
    return root
