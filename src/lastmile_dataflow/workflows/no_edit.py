"""统一采集编排；协议先简化，文件名在下一步调整。"""
from dataclasses import asdict, replace
import os
from pathlib import Path
import time

from ..config import CollectionConfig, RobotConfig
from ..io import append_row, create_run, read_json, rows, write_json
from ..runtime.simulation import Simulation
from ..runtime.no_edit_execution import run_raw_attempt
from ..runtime.molmobot_execution import run_vla_attempt
from ..stations.scene_package import load_scenes
from ..policies.molmobot_config import load_policy_config
from ..stations.lastmile_selection import station_calibration, recovery_choices
from ..stations.no_edit_statistics import aggregate
from ..tasks.raw_scene import grasp_candidates, robot_footprint
from ..navigation.astar import scene_grid, search, task_support_obstacles
from ..exporting.no_edit_heatmap import export_heatmap


def profile(robot, config):
    return {'robot': asdict(robot), 'width': config.width, 'height': config.height,
            'backend': config.operation_backend, 'policy': asdict(load_policy_config(config.policy_config)) if config.policy_config else None}


def trial_record(result, scene, station, trial, seed, root):
    record = {key: result.get(key) for key in ('status', 'reason', 'execution_termination',
        'task_outcome', 'recording_complete', 'executed_steps', 'navigation', 'error')}
    record.update(scene_id=scene['scene_id'], station_id=station['station_id'], trial=trial, seed=seed,
                  rollout_id=result.get('rollout_id'), task_id=scene['task']['task_id'])
    return record


def execute(baseline, scene, station, assets_dir, config, collection, root, seed, policy=None, path=None, goal=None):
    # One real executor and recorder for both station and recovery experiments.
    attempt_id = f'r{len(rows(root / "trials.jsonl")) + len(rows(root / "episodes.jsonl")):06d}'
    task = scene['task']
    if config.operation_backend == 'molmobot':
        return run_vla_attempt(baseline, task, station, config, collection, root, attempt_id,
                               seed=seed, policy=policy, path=path, goal=goal)
    return run_raw_attempt(baseline, task, station, grasp_candidates(task, assets_dir), assets_dir,
                           config, collection, root, attempt_id, seed=seed, path=path, goal=goal)


def evaluate_stations(baseline, scene, assets_dir, config, collection, root, policy=None, budget=None):
    trials = []
    for index, station in enumerate(scene['stations']):
        for trial in range(config.trials_per_station):
            if budget is not None and len(trials) >= budget:
                return trials
            seed = config.seed + index * config.trials_per_station + trial
            result = execute(baseline, scene, station, assets_dir, config, collection, root, seed, policy)
            record = trial_record(result, scene, station, trial, seed, root)
            append_row(root / 'trials.jsonl', record)
            trials.append(record)
    return trials


def collect_lastmile(baseline, scene, trials, assets_dir, config, collection, root):
    binding = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}
    calibration = station_calibration(scene['stations'], trials, config.trials_per_station, binding, config)
    choices = recovery_choices(scene['stations'], calibration, binding, config)
    grid = scene_grid(baseline, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m,
                      robot_footprint(baseline)['radius_m'], config.navigation_margin_m,
                      support_obstacles=task_support_obstacles(baseline, scene['task']))
    episodes = []
    for start, goals, _ in choices:
        for goal in goals[:config.max_s1_candidates or 5]:
            path = search(grid, start['xy'], goal['xy'])
            if not path:
                record = {'scene_id': scene['scene_id'], 's0': start['station_id'], 's1': goal['station_id'],
                          'status': 'path_unavailable', 'reason': 'astar_no_path', 'rollout_id': None}
            else:
                result = execute(baseline, scene, start, assets_dir, config, collection, root,
                                 config.seed + len(episodes), path=path, goal=goal)
                record = trial_record(result, scene, start, None, config.seed + len(episodes), root)
                record.update(s0=start['station_id'], s1=goal['station_id'])
            append_row(root / 'episodes.jsonl', record)
            episodes.append(record)
    return episodes


def collect_scene_dataset(dataset, assets_dir, config, collection, output_dir, *, run_id,
                          gpu_ids, samples=None, continuous=False, calibration=None, max_trials=None):
    geometry = read_json(Path(dataset) / 'config.json').get('geometry', {})
    keys = ('radius_m', 'spacing_m', 'width', 'height', 'min_target_pixels', 'map_resolution_m', 'navigation_margin_m')
    config = replace(config, **{key: geometry[key] for key in keys if key in geometry})
    collection = replace(collection, width=config.width, height=config.height, seed=config.seed)
    scenes = load_scenes(dataset, samples)
    if not scenes:
        raise ValueError('no accepted scenes')
    if continuous and config.operation_backend != 'curobo':
        raise ValueError('continuous collection currently uses cuRobo')
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]), MUJOCO_GL='egl')
    policy = None
    profiles = {}
    for scene in scenes:
        robot = RobotConfig(**read_json(Path(scene['scene_path']) / 'config.json')['robot'])
        if (robot.control_profile == 'molmobot-native-v1') != (config.operation_backend == 'molmobot'):
            raise ValueError('scene control profile incompatible with requested backend; construct an explicit matching scene')
        profiles[scene['scene_id']] = profile(robot, config)
    root = create_run(Path(output_dir) / run_id, {'dataset': str(Path(dataset).resolve()), 'config': asdict(config),
        'collection': asdict(collection), 'profiles': profiles, 'gpu': gpu_ids[0], 'seed': config.seed, 'calibration': str(calibration) if calibration else None})
    prior = rows(Path(calibration) / 'trials.jsonl') if calibration else []
    if calibration and (read_json(Path(calibration) / 'config.json')['profiles'] != profiles
                        or read_json(Path(calibration) / 'config.json')['config'] != asdict(config)):
        raise ValueError('calibration scene/backend/robot/sensor/policy configuration mismatch')
    started = time.monotonic()
    summaries = []
    try:
        if config.operation_backend == 'molmobot':
            from ..policies.molmobot import MolmoBotClient
            logs = Path(output_dir) / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            policy = MolmoBotClient(config.policy_config, logs / f'{run_id}-policy.log')
        for scene in scenes:
            robot = RobotConfig(**read_json(Path(scene['scene_path']) / 'config.json')['robot'])
            sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
            sim.scene_id = scene['scene_id']
            try:
                trials = [row for row in prior if row['scene_id'] == scene['scene_id']] if calibration else evaluate_stations(
                    sim, scene, assets_dir, config, collection, root, policy, max_trials)
                rates = station_calibration(scene['stations'], trials, config.trials_per_station,
                    {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}, config)
                episodes = collect_lastmile(sim, scene, trials, assets_dir, config, collection, root) if continuous else []
                grid = scene_grid(sim, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m, 0., 0.,
                                  support_obstacles=task_support_obstacles(sim, scene['task']))
                display = [{**row, 'attempt': row.get('rollout_id')} for row in trials]
                export_heatmap(root / 'figures' / scene['scene_id'], grid,
                    aggregate(scene['stations'], display, config.trials_per_station), scene['task']['anchor_world'],
                    config.smoothing_sigma_m or config.spacing_m or .1, title=scene['scene_id'])
                summaries.append({'scene_id': scene['scene_id'], 'calibration_complete': rates['complete'],
                    'eligible_pairs': len(recovery_choices(scene['stations'], rates, {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}, config)),
                    'successes': sum(row['status'] == 'success' and row.get('navigation', {}).get('status') == 'success'
                                     and row.get('recording_complete', False) for row in episodes)})
            finally:
                sim.close()
    finally:
        if policy: policy.close()
        write_json(root / 'summary.json', {'status': 'completed' if len(summaries) == len(scenes) else 'incomplete',
            'scenes': summaries, 'elapsed_s': time.monotonic() - started})
    return root
