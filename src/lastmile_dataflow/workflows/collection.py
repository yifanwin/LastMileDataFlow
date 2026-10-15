"""Station evaluation and continuous LastMile collection share one run lifecycle."""
from dataclasses import asdict, replace
import os
from itertools import count
from pathlib import Path
import time

from ..config import read_config
from ..config import RobotConfig
from ..io import append_row, create_run, rows, write_json
from ..runtime.simulation import Simulation
from ..runtime.execution.curobo import run_curobo_attempt
from ..runtime.execution.molmobot import run_vla_attempt
from ..scenes.dataset import load_scenes
from ..runtime.execution_config import execution_profile
from ..data.evaluation.selection import station_calibration, recovery_choices
from ..data.evaluation.statistics import aggregate
from ..scenes.tasks.discovery import grasp_candidates, robot_footprint
from ..planning.astar import scene_grid, search, task_support_obstacles
from ..data.visualization.heatmap import export_heatmap


def _trial_record(result, scene, station, trial, seed):
    record = {key: result.get(key) for key in ('status', 'reason', 'execution_termination',
        'task_outcome', 'recording_complete', 'executed_steps', 'navigation', 'error')}
    record.update(scene_id=scene['scene_id'], station_id=station['station_id'], trial=trial, seed=seed,
                  rollout_id=result.get('rollout_id'), task_id=scene['task']['task_id'])
    return record


def _execute(baseline, scene, station, assets_dir, config, collection, root, seed, policy=None, path=None, goal=None, *, attempt_ids):
    # One real executor and recorder for both station and recovery experiments.
    attempt_id = f'r{next(attempt_ids):06d}'
    task = scene['task']
    if config.operation_backend == 'molmobot':
        return run_vla_attempt(baseline, task, station, config, collection, root=root, attempt_id=attempt_id,
                               seed=seed, policy=policy, path=path, goal=goal)
    return run_curobo_attempt(baseline, task, station, grasp_candidates(task, assets_dir), assets_dir,
                           config, collection, root=root, attempt_id=attempt_id, seed=seed, path=path, goal=goal)


def _station_trials(baseline, scene, assets_dir, config, collection, root, policy=None, budget=None, *, attempt_ids):
    trials = []
    for index, station in enumerate(scene['stations']):
        for trial in range(config.trials_per_station):
            if budget is not None and len(trials) >= budget:
                return trials
            seed = config.seed + index * config.trials_per_station + trial
            result = _execute(baseline, scene, station, assets_dir, config, collection, root, seed, policy, attempt_ids=attempt_ids)
            record = _trial_record(result, scene, station, trial, seed)
            append_row(root / 'trials.jsonl', record)
            trials.append(record)
            if result.get('failure_scope') == 'shared' or record['status'] == 'infrastructure_error':
                return trials
    return trials


def _recovery_trials(baseline, scene, choices, assets_dir, config, collection, root, *, attempt_ids):
    if not choices:
        return []
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
                result = _execute(baseline, scene, start, assets_dir, config, collection, root,
                                 config.seed + len(episodes), path=path, goal=goal, attempt_ids=attempt_ids)
                record = _trial_record(result, scene, start, None, config.seed + len(episodes))
                record.update(s0=start['station_id'], s1=goal['station_id'])
            append_row(root / 'episodes.jsonl', record)
            episodes.append(record)
    return episodes


def _station_summary(sim, scene, trials, config, root):
    binding = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}
    rates = station_calibration(scene['stations'], trials, config.trials_per_station, binding, config)
    grid = scene_grid(sim, scene['task']['anchor_world'], config.radius_m, config.map_resolution_m, 0., 0.,
                      support_obstacles=task_support_obstacles(sim, scene['task']))
    display = [{**row, 'attempt': row.get('rollout_id')} for row in trials]
    export_heatmap(root / 'figures' / scene['scene_id'], grid,
        aggregate(scene['stations'], display, config.trials_per_station), scene['task']['anchor_world'],
        config.smoothing_sigma_m or config.spacing_m or .1, title=scene['scene_id'])
    choices = recovery_choices(scene['stations'], rates, binding, config)
    summary = {'scene_id': scene['scene_id'], 'calibration_complete': rates['complete'],
               'eligible_pairs': len(choices), 'successes': 0}
    return summary, choices


def _evaluate_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids):
    trials = _station_trials(sim, scene, assets_dir, config, collection, root, policy, max_trials, attempt_ids=attempt_ids)
    summary, _ = _station_summary(sim, scene, trials, config, root)
    return summary


def _collect_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, *, attempt_ids):
    trials = [row for row in prior if row['scene_id'] == scene['scene_id']] if prior is not None else _station_trials(
        sim, scene, assets_dir, config, collection, root, policy, max_trials, attempt_ids=attempt_ids)
    summary, choices = _station_summary(sim, scene, trials, config, root)
    episodes = _recovery_trials(sim, scene, choices, assets_dir, config, collection, root, attempt_ids=attempt_ids)
    summary['successes'] = sum(row['status'] == 'success' and row.get('navigation', {}).get('status') == 'success'
                               and row.get('recording_complete', False) for row in episodes)
    return summary


def _prepare_run(dataset, config, collection, output_dir, run_id, gpu, samples, calibration):
    geometry = read_config(Path(dataset) / 'config.json').get('geometry', {})
    keys = ('radius_m', 'spacing_m', 'width', 'height', 'min_target_pixels', 'map_resolution_m', 'navigation_margin_m')
    config = replace(config, **{key: geometry[key] for key in keys if key in geometry})
    collection = replace(collection, width=config.width, height=config.height, seed=config.seed,
                         output_dir=str(Path(output_dir).resolve()))
    scenes = load_scenes(dataset, samples)
    if not scenes:
        raise ValueError('no accepted scenes')
    profiles = {}
    for scene in scenes:
        robot = RobotConfig(**read_config(Path(scene['scene_path']) / 'config.json')['robot'])
        if (robot.control_profile == 'molmobot-native-v1') != (config.operation_backend == 'molmobot'):
            raise ValueError('scene control profile incompatible with requested backend; construct an explicit matching scene')
        scene['robot_config'] = robot
        profiles[scene['scene_id']] = execution_profile(robot, config)
    prior = None
    if calibration:
        saved = read_config(Path(calibration) / 'config.json')
        if saved['profiles'] != profiles or saved['config'] != asdict(config):
            raise ValueError('calibration scene/backend/robot/sensor/policy configuration mismatch')
        prior = rows(Path(calibration) / 'trials.jsonl')
    root = create_run(Path(output_dir) / run_id, {
        'dataset': str(Path(dataset).resolve()), 'config': asdict(config), 'collection': asdict(collection),
        'profiles': profiles, 'gpu': gpu, 'seed': config.seed, 'calibration': str(Path(calibration).resolve()) if calibration else None})
    return root, scenes, config, collection, prior


def _run_dataset(dataset, assets_dir, config, collection, output_dir, process_scene, *, run_id,
                 gpu_ids, samples=None, calibration=None, max_trials=None):
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]), MUJOCO_GL='egl')
    root, scenes, config, collection, prior = _prepare_run(
        dataset, config, collection, output_dir, run_id, gpu_ids[0], samples, calibration)
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
        for scene in scenes:
            robot = scene['robot_config']
            sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
            sim.scene_id = scene['scene_id']
            try:
                summaries.append(process_scene(sim, scene, assets_dir, config, collection, root, policy, prior, max_trials, attempt_ids=attempt_ids))
            finally:
                sim.close()
    finally:
        if policy:
            policy.close()
        write_json(root / 'summary.json', {'status': 'completed' if len(summaries) == len(scenes) else 'incomplete',
            'scenes': summaries, 'elapsed_s': time.monotonic() - started})
    return root


def evaluate_stations(dataset, assets_dir, config, collection, output_dir, *, run_id,
                      gpu_ids, samples=None, max_trials=None):
    """Run station rollouts and heatmaps; never execute S0→S1 recovery."""
    return _run_dataset(dataset, assets_dir, config, collection, output_dir, _evaluate_scene,
        run_id=run_id, gpu_ids=gpu_ids, samples=samples, max_trials=max_trials)


def collect_lastmile(dataset, assets_dir, config, collection, output_dir, *, run_id,
                     gpu_ids, samples=None, calibration=None, max_trials=None):
    """Use matching calibration, or calibrate once, then run A* + cuRobo V2."""
    if config.operation_backend != 'curobo':
        raise ValueError('continuous collection currently uses cuRobo')
    return _run_dataset(dataset, assets_dir, config, collection, output_dir, _collect_scene,
        run_id=run_id, gpu_ids=gpu_ids, samples=samples, calibration=calibration, max_trials=max_trials)
