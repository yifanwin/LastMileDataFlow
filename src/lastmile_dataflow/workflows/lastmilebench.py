"""Build from existing reference calibration; independently evaluate policies."""
from collections import Counter
from dataclasses import asdict
import logging
import os
from pathlib import Path
import time

import numpy as np

from ..config import CollectionConfig, robot_config
from ..data.evaluation.benchmark_config import BenchmarkConfig, load_benchmark_config
from ..data.evaluation.benchmark import reference_calibration, select_starts, failure_family, summarize
from ..data.evaluation.recovery import RecoveryRegion, difficulty, motion_diagnostics
from ..planning.astar import scene_grid, task_support_obstacles
from ..runtime.simulation import Simulation
from ..runtime.execution_config import ExecutionConfig
from ..runtime.construction.benchmark_scene import native_scene
from ..runtime.execution.molmobot import run_vla_attempt
from ..scenes.dataset import load_scenes
from ..scenes.stations.sampling import place_frozen_station, robot_contacts
from ..scenes.stations.initialization import floor_support
from ..scenes.tasks.discovery import robot_footprint
from ..policies.molmobot import MolmoBotClient
from ..policies.molmobot_config import load_policy_config
from ..utils.io import create_run, read_json, rows, write_json, write_rows, append_row
from ..utils.logging_config import Progress, trial_logging

logger = logging.getLogger(__name__)


def _navigation_region(sim, scene, calibration, settings, radius):
    """Validate unchanged source initializations and construct one task grid."""
    observed = {r['station_id']: r for r in calibration['stations']}
    legal, measured, footprints = [], [], []
    rejected = Counter()
    for station in scene['stations']:
        if station['geometry'] != 'valid':
            rejected['source_initialization_invalid'] += 1
            continue
        place_frozen_station(sim, station)
        if robot_contacts(sim) or not floor_support(sim, station['xy']):
            rejected['initialization_invalid'] += 1
            continue
        legal.append(station)
        footprints.append(robot_footprint(sim, whole_body=settings.navigation_whole_body))
        row = observed[station['station_id']]
        if row['complete']:
            measured.append({'station_id': station['station_id'], 'xy': station['xy'], 'yaw': station['base'][2],
                             'successes': row['successes'], 'trials': row['trials'],
                             'high': row['successes'] / row['trials'] >= settings.high})
    if not footprints:
        return None, legal, dict(rejected), None
    footprint = {'radius_m': max(f['radius_m'] for f in footprints),
                 'height_m': max(f['max'][2] for f in footprints),
                 'method': 'held_whole_body_swept_circle' if settings.navigation_whole_body else 'chassis_only'}
    grid = scene_grid(sim, scene['task']['anchor_world'], radius, settings.map_resolution_m,
                      footprint['radius_m'], settings.navigation_margin_m,
                      robot_height=footprint['height_m'] if settings.navigation_whole_body else .4,
                      support_obstacles=task_support_obstacles(sim, scene['task']))
    # Existing station visibility is reusable: construction has not changed the
    # source model, cameras, target, or initialization. No rerender is needed.
    return RecoveryRegion.build(grid, measured, settings), legal, dict(rejected), footprint


def build(dataset, output, *, calibration_dir, config_path=None, gpu_ids=None):
    if calibration_dir is None:
        raise ValueError('benchmark requires existing measured calibration')
    settings = load_benchmark_config(config_path) if config_path else BenchmarkConfig()
    source = Path(dataset).resolve()
    calibration_dir = Path(calibration_dir).resolve()
    saved = read_json(calibration_dir / 'config.json')
    if Path(saved['dataset']).resolve() != source:
        raise ValueError('calibration source dataset mismatch')
    reference = saved['config']
    if reference['operation_backend'] != 'curobo':
        raise ValueError('v0.1 construction requires cuRobo reference calibration')
    trials = rows(calibration_dir / 'trials.jsonl')
    scenes = load_scenes(source)
    scene_ids = {s['scene_id'] for s in scenes}
    if any(r['scene_id'] not in scene_ids for r in trials):
        raise ValueError('calibration contains a scene outside the source dataset')
    root = create_run(output, {'schema_version': 'lastmile-bench-v0.1', 'dataset': str(source),
                              'calibration': str(calibration_dir), 'config': asdict(settings),
                              'reference_execution': reference,
                              'success_rate_semantics': 'task completion from calibrated initial station; base motion allowed by reference protocol'})
    episodes = []
    oracle = {'regions': {}, 'episodes': {}}
    reports = []
    progress = Progress(logger)
    started = time.monotonic()
    rng = np.random.default_rng(settings.seed)
    completed = False
    try:
        for scene in scenes:
            sid = scene['scene_id']
            subset = [r for r in trials if r['scene_id'] == sid]
            binding = {'scene_id': sid, 'task_id': scene['task']['task_id']}
            calibration = reference_calibration(scene['stations'], subset, reference['trials_per_station'], binding,
                                                reference['operation_backend'])
            choices = select_starts(scene['stations'], calibration, binding, low=settings.low, high=settings.high)
            complete = [r for r in calibration['stations'] if r['complete']]
            low_count = sum(r['successes'] / r['trials'] <= settings.low for r in complete)
            high_count = sum(r['successes'] / r['trials'] >= settings.high for r in complete)
            report = {'scene_id': sid, 'task': scene['task']['operation'], 'stations': len(scene['stations']),
                      'complete_stations': len(complete), 'incomplete_stations': len(scene['stations']) - len(complete),
                      'low_stations': low_count, 'high_stations': high_count,
                      'candidate_episodes': len(choices), 'eligible_episodes': 0, 'written_episodes': 0,
                      'exclusions': {}, 'initialization_rejections': {}}
            reports.append(report)
            exclusions = Counter()
            if not choices:
                reason = 'incomplete_calibration' if not complete else 'no_high_success_station' if low_count and not high_count else 'no_low_success_station'
                report['exclusions'] = {reason: low_count or 1}
                continue
            snapshot = Path(scene['scene_path'])
            snapshot_config = read_json(snapshot / 'config.json')
            if saved['profiles'][sid]['robot'] != snapshot_config['robot']:
                raise ValueError('source robot profile does not match calibration: ' + sid)
            sim = Simulation.from_snapshot(snapshot, robot_config(snapshot_config['robot']), target=scene['task']['target_body'])
            try:
                radius = reference['radius_m']
                region, legal, init_rejected, footprint = _navigation_region(sim, scene, calibration, settings, radius)
                report['initialization_rejections'] = init_rejected
                report['navigation_footprint'] = footprint
                report['navigation_radius_m'] = radius
                legal_ids = {s['station_id'] for s in legal}
                eligible = []
                for station, evidence in choices:
                    if station['station_id'] not in legal_ids:
                        exclusions['initialization_invalid'] += 1
                        continue
                    metrics = difficulty(region, station, legal, settings.local_candidate_radius_m) if region else None
                    if metrics is None:
                        exclusions['no_navigation_path'] += 1
                        continue
                    station_trials = [r for r in subset if r['station_id'] == station['station_id']]
                    group = failure_family(station_trials)
                    eligible.append((station, evidence, metrics, group))
                report['eligible_episodes'] = len(eligible)
                if settings.max_episodes_per_scene and len(eligible) > settings.max_episodes_per_scene:
                    indices = sorted(rng.choice(len(eligible), settings.max_episodes_per_scene, replace=False).tolist())
                    exclusions['explicit_seeded_subsample'] = len(eligible) - len(indices)
                    eligible = [eligible[i] for i in indices]
                report['exclusions'] = dict(exclusions)
                if eligible:
                    relative = Path('scene-files') / sid
                    sim.restore_snapshot(snapshot / 'initial.npz')
                    sim.freeze(root / relative)
                    oracle['regions'][sid] = region.to_dict()
                    for station, evidence, metrics, group in eligible:
                        eid = f'e{len(episodes):06d}'
                        episodes.append({'episode_id': eid, 'scene_id': sid, 'scene': str(relative),
                                         'task': scene['task'], 'station': station, 'seed': settings.seed})
                        oracle['episodes'][eid] = {'region_id': sid, 'scene_id': sid,
                            'object_id': scene['task']['instance_id'], 'task_id': scene['task']['task_id'],
                            'task': scene['task']['operation'], 'case': scene.get('case', scene.get('case_name', 'unknown')),
                            'start_successes': evidence['successes'], 'start_trials': evidence['trials'],
                            'start_success_rate': evidence['success_rate'], **group, 'difficulty': metrics}
                    report['written_episodes'] = len(eligible)
            finally:
                sim.close()
            progress.update('构建 bench 场景=%s/%s episode=%s 当前=%s', len(reports), len(scenes), len(episodes), sid)
        completed = True
    finally:
        write_rows(root / 'episodes.jsonl', episodes)
        write_json(root / 'oracle.json', oracle)
        write_json(root / 'summary.json', {'schema_version': 'lastmile-bench-v0.1',
            'status': 'complete' if completed else 'incomplete',
            'source_scenes': len(scenes), 'processed_scenes': len(reports), 'episodes': len(episodes),
            'candidate_episodes': sum(r['candidate_episodes'] for r in reports),
            'eligible_episodes': sum(r['eligible_episodes'] for r in reports),
            'new_calibration_rollouts': 0, 'scenes': reports, 'elapsed_s': time.monotonic() - started})
        progress.update('benchmark 构建收尾 场景=%s/%s 已写入episode=%s 输出=%s',
                        len(reports), len(scenes), len(episodes), root, force=True)
    return root


def evaluate(benchmark, output, policy_path, *, gpu_ids, log_dir):
    benchmark = Path(benchmark).resolve()
    episodes = rows(benchmark / 'episodes.jsonl')
    if not episodes:
        raise ValueError('no benchmark episodes')
    saved = read_json(benchmark / 'config.json')
    settings = BenchmarkConfig(**saved['config'])
    oracle = read_json(benchmark / 'oracle.json')
    regions = {key: RecoveryRegion.from_dict(value) for key, value in oracle['regions'].items()}
    policy_config = load_policy_config(policy_path)
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]))
    root = create_run(output, {'benchmark': str(benchmark), 'policy': asdict(policy_config),
                              'seed': settings.seed, 'horizon': settings.horizon, 'wall_timeout_s': settings.wall_timeout_s,
                              'reference_calibration': saved['calibration'], 'scene_profiles': {}})
    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    policy = None
    profiles = {}
    progress = Progress(logger)
    try:
        for episode in episodes:
            eid = episode['episode_id']
            snapshot = benchmark / episode['scene']
            robot = robot_config(read_json(snapshot / 'config.json')['robot'])
            if robot.name != 'rby1m':
                record = {'episode_id': eid, 'status': 'unsupported_policy', 'reason': 'MolmoBot RBY-1 policy does not support ' + robot.name,
                          'recovery_success': None, 'recording_complete': False, 'executed_steps': 0}
                append_row(root / 'results.jsonl', record)
                results.append(record)
                continue
            parent = sim = None
            try:
                parent = Simulation.from_snapshot(snapshot, robot, target=episode['task']['target_body'])
                sim = native_scene(parent)
                sim.scene_id = episode['scene_id']
                profiles[episode['scene_id']] = asdict(sim.robot.config)
                if policy is None:
                    policy = MolmoBotClient(policy_path, logs / f'{root.name}-policy.log')
                    from dataclasses import replace
                    policy.config = replace(policy.config, task_horizon=settings.horizon)
                config = ExecutionConfig(operation_backend='molmobot', operation_base_mode='policy_relative',
                    policy_config=str(policy_path), width=1024, height=576, radius_m=saved['reference_execution']['radius_m'],
                    attempt_timeout_s=settings.wall_timeout_s, third_person_enabled=False,
                    open_hold_s=saved['reference_execution'].get('open_hold_s', 1.))
                collection = CollectionConfig(output_dir=str(root), width=1024, height=576, seed=episode['seed'],
                    max_steps=settings.horizon, record_video=False)
                with trial_logging(f'episode={eid} scene={episode["scene_id"]}', progress):
                    result = run_vla_attempt(sim, episode['task'], episode['station'], config, collection,
                        root=root, attempt_id=eid, seed=episode['seed'], policy=policy)
                record = {key: result.get(key) for key in ('status', 'reason', 'executed_steps', 'recording_complete',
                    'rollout_id', 'execution_termination', 'task_outcome', 'failure_scope')}
                record['episode_id'] = eid
                record.update(motion_diagnostics(root / 'rollouts' / (record['rollout_id'] or eid) / 'trajectory.npz',
                    regions[oracle['episodes'][eid]['region_id']], settings,
                    recording_complete=bool(record['recording_complete'])))
                append_row(root / 'results.jsonl', record)
                results.append(record)
                progress.update('benchmark 已记录episode=%s/%s 当前=%s status=%s', len(results), len(episodes), eid, record['status'])
                if record.get('failure_scope') == 'shared':
                    break
            finally:
                if sim is not None:
                    sim.close()
                if parent is not None:
                    parent.close()
    finally:
        if policy is not None:
            policy.close()
        write_json(root / 'summary.json', summarize(episodes, results, oracle['episodes']))
        run_config = read_json(root / 'config.json')
        run_config['scene_profiles'] = profiles
        write_json(root / 'config.json', run_config)
        progress.update('benchmark 评测收尾 已记录episode=%s/%s 输出=%s', len(results), len(episodes), root, force=True)
    return root
