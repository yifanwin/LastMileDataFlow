"""Build from existing reference calibration; independently evaluate policies."""
from collections import Counter
from dataclasses import asdict, replace
import logging
import os
from pathlib import Path
import time

import numpy as np

from ..config import CollectionConfig, robot_config
from ..data.evaluation.benchmark_config import BenchmarkConfig, load_benchmark_config
from ..data.evaluation.benchmark import reference_calibration, select_starts, failure_family, summarize
from ..data.evaluation.recovery import RecoveryRegion, difficulty, motion_diagnostics
from ..planning.navigation.astar import scene_grid, task_support_obstacles
from ..planning.navigation.geometry import GeometryNavigation, NavigationSettings, NAVIGATION_PROTOCOL
from ..runtime.simulation import Simulation
from ..runtime.execution_config import ExecutionConfig
from ..runtime.construction.benchmark_scene import native_scene
from ..runtime.execution.vla import run_vla_attempt
from ..runtime.execution.agent import run_agent_attempt
from ..scenes.dataset import load_scenes
from ..scenes.stations.sampling import place_frozen_station
from ..runtime.outcomes.contacts import ContactSampler, NavigationContacts
from ..scenes.stations.initialization import floor_support
from ..scenes.tasks.discovery import robot_footprint
from ..policies.config import load_policy_config, create_policy, backend, check_profile
from ..utils.io import create_run, read_json, rows, write_json, write_rows, append_row
from ..utils.logging_config import Progress, trial_logging
from ..data.visualization.benchmark import benchmark_view, evaluation_view

logger = logging.getLogger(__name__)


def _navigation_region(sim, scene, calibration, settings):
    """Validate unchanged source initializations and construct one task grid."""
    observed = {r['station_id']: r for r in calibration['stations']}
    legal, measured, footprints = [], [], []
    rejected = Counter()
    sampler = ContactSampler(sim)
    for station in scene['stations']:
        if station['geometry'] != 'valid':
            rejected['source_initialization_invalid'] += 1
            continue
        place_frozen_station(sim, station)
        contacts = NavigationContacts()
        if contacts.observe(sim, sampler, settings.navigation_max_penetration_m) or not floor_support(sim, station['xy']):
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
    grid = scene_grid(sim, scene['task']['anchor_world'], None, settings.map_resolution_m,
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
    mode_run = reference.get('mode_config') is not None
    diagnoses = None
    source_val_count = None
    if mode_run:
        from ..data.evaluation.diagnosis import load_diagnoses
        source_val_count = sum(s['dataset_split'] == 'val' for s in scenes)
        scenes = [s for s in scenes if s['dataset_split'] == 'val' and s['scene_id'] in saved['profiles']]
        diagnoses = load_diagnoses(calibration_dir, scenes, saved['mode_settings'])
        if diagnoses:
            references = {row['trials_ref'] for row in diagnoses.values()}
            if len(references) != 1:
                raise ValueError('mode diagnoses must reference one raw trial set')
            trial_source = Path(next(iter(references)))
            if not trial_source.is_file():
                raise FileNotFoundError(trial_source)
            trials = rows(trial_source)
        settings = replace(settings, **{key: reference[key] for key in (
            'map_resolution_m', 'navigation_whole_body')})
    if any(r['scene_id'] not in scene_ids for r in trials):
        raise ValueError('calibration contains a scene outside the source dataset')
    schema = 'lastmile-bench-task-modes-v1' if mode_run else 'lastmile-bench-v0.1'
    root = create_run(output, {'schema_version': schema, 'dataset': str(source),
                              'navigation_protocol': NAVIGATION_PROTOCOL,
                              'navigation_settings': asdict(NavigationSettings.from_config(settings, reference)),
                              'calibration': str(calibration_dir), 'config': asdict(settings),
                              'reference_execution': reference,
                              **({'selection_source': 'diagnosis.jsonl', 'mode_settings': saved['mode_settings']}
                                 if mode_run else {}),
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
            if mode_run:
                from ..data.evaluation.diagnosis import diagnosed_starts
                from ..data.evaluation.selection import mode_calibration
                calibration = mode_calibration(scene['stations'], subset, binding, reference['operation_backend'])
                diagnosis = diagnoses[sid]
                if (calibration['complete'] != diagnosis['calibration_complete']
                        or calibration['task_eligibility'] != diagnosis['task_eligibility']):
                    raise ValueError('diagnosis does not match raw reference calibration')
                choices = diagnosed_starts(scene, diagnosis)
            else:
                calibration = reference_calibration(scene['stations'], subset, reference['trials_per_station'], binding,
                                                    reference['operation_backend'])
                choices = select_starts(scene['stations'], calibration, binding, low=settings.low, high=settings.high)
            complete = [r for r in calibration['stations'] if r['complete']]
            low_count = sum(r['successes'] <= 1 if mode_run else
                            r['successes'] / r['trials'] <= settings.low for r in complete)
            high_count = sum(r['successes'] >= 2 if mode_run else
                             r['successes'] / r['trials'] >= settings.high for r in complete)
            report = {'scene_id': sid, 'task': scene['task']['operation'], 'stations': len(scene['stations']),
                      'complete_stations': len(complete), 'incomplete_stations': len(scene['stations']) - len(complete),
                      'low_stations': low_count, 'high_stations': high_count,
                      'candidate_episodes': len(choices), 'eligible_episodes': 0, 'written_episodes': 0,
                      'exclusions': {}, 'initialization_rejections': {}}
            reports.append(report)
            exclusions = Counter()
            if not choices:
                reason = (diagnoses[sid].get('reason', 'no_diagnosed_recovery_start') if mode_run else
                          'incomplete_calibration' if not complete else 'no_high_success_station' if low_count and not high_count else 'no_low_success_station')
                report['exclusions'] = {reason: low_count or 1}
                continue
            snapshot = Path(scene['scene_path'])
            snapshot_config = read_json(snapshot / 'config.json')
            if saved['profiles'][sid]['robot'] != snapshot_config['robot']:
                raise ValueError('source robot profile does not match calibration: ' + sid)
            sim = Simulation.from_snapshot(snapshot, robot_config(snapshot_config['robot']), target=scene['task']['target_body'])
            navigation = None
            try:
                region, legal, init_rejected, footprint = _navigation_region(sim, scene, calibration, settings)
                report['initialization_rejections'] = init_rejected
                report['navigation_footprint'] = footprint
                report['navigation_extent'] = 'source_room_floor'
                legal_ids = {s['station_id'] for s in legal}
                navigation_stations = legal
                if mode_run:
                    observed = diagnosis['station_measurements']
                    navigation_stations = [{**s, 'base': observed[s['station_id']]['base']}
                        for s in legal if observed.get(s['station_id'], {}).get('status') == 'measured']
                if region is not None:
                    navigation = GeometryNavigation(sim, region.grid, navigation_stations,
                        NavigationSettings.from_config(settings, reference), budget_s=min(120., settings.wall_timeout_s))
                report['navigation_method'] = 'geometry_soft_astar'
                report['navigation_diagnostics'] = []
                by_id = {s['station_id']: s for s in legal}
                eligible = []
                for station, evidence in choices:
                    if station['station_id'] not in legal_ids:
                        exclusions['initialization_invalid'] += 1
                        continue
                    if mode_run:
                        observed = diagnosis['station_measurements']
                        required = [station['station_id'], *evidence['high_station_ids']]
                        if any(observed.get(identity, {}).get('status') != 'measured' for identity in required):
                            exclusions['missing_measured_recovery_anchor'] += 1
                            continue
                        measured = [{**row, 'xy': observed[row['station_id']]['base'][:2],
                                     'yaw': observed[row['station_id']]['yaw'],
                                     'high': row['station_id'] in evidence['high_station_ids']}
                                    for row in region.measured if observed.get(row['station_id'], {}).get('status') == 'measured']
                    else:
                        measured = region.measured
                    navigation.hold(station)
                    base = diagnosis['station_measurements'][station['station_id']]['base'] if mode_run else station['base']
                    start_pose = {**station, 'xy': base[:2], 'base': base}
                    source_check = navigation.check_pose(base)
                    start_cell = region.grid.cell(base[:2])
                    diagnostic = {'station_id': station['station_id'],
                                  'conservative_start_blocked': start_cell is None or not bool(region.grid.free[start_cell]),
                                  'start_clearance_lower_bound_m': source_check.clearance_m}
                    report['navigation_diagnostics'].append(diagnostic)
                    if not source_check.valid:
                        diagnostic['reason'] = source_check.reason
                        exclusions[source_check.reason] += 1
                        continue
                    incompatible = [r['station_id'] for r in measured if r['high']
                                    and not navigation.compatible(by_id[r['station_id']])]
                    measured = [{**r, 'high': r['high'] and r['station_id'] not in incompatible} for r in measured]
                    episode_region = RecoveryRegion.build(region.grid, measured, settings, navigation=navigation)
                    diagnostic['incompatible_recovery_stations'] = incompatible
                    diagnostic['recovery_anchors'] = [p['anchor']['station_id'] for p in episode_region.patches]
                    planned = navigation.plan(base, episode_region.goals())
                    diagnostic.update({k: v for k, v in planned.items() if k != 'path'})
                    if planned['path'] is None:
                        exclusions[planned['reason']] += 1
                        continue
                    metrics = difficulty(episode_region, start_pose, legal, settings.local_candidate_radius_m,
                                         reference_path=planned['path'])
                    station_trials = [r for r in subset if r['station_id'] == station['station_id']]
                    group = failure_family(station_trials)
                    eligible.append((station, evidence, metrics, group, episode_region))
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
                    for station, evidence, metrics, group, episode_region in eligible:
                        eid = f'e{len(episodes):06d}'
                        region_id = eid
                        oracle['regions'][region_id] = episode_region.to_dict()
                        episodes.append({'episode_id': eid, 'scene_id': sid, 'scene': str(relative),
                                         'task': scene['task'], 'station': station, 'seed': settings.seed})
                        oracle['episodes'][eid] = {'region_id': region_id, 'scene_id': sid,
                            'object_id': scene['task']['instance_id'], 'task_id': scene['task']['task_id'],
                            'task': scene['task']['operation'],
                            **({'labels': evidence['labels'], 's1_by_label': evidence['s1_by_label'],
                                'proposal_intent': scene['proposal_intent'], 'dataset_split': 'val',
                                'diagnosis_ref': str(calibration_dir / 'diagnosis.jsonl')}
                               if mode_run else {'case': scene.get('case', scene.get('case_name', 'unknown'))}),
                            'start_successes': evidence['successes'], 'start_trials': evidence['trials'],
                            'start_success_rate': evidence['success_rate'], **group, 'difficulty': metrics}
                    report['written_episodes'] = len(eligible)
            finally:
                if navigation is not None:
                    navigation.close()
                sim.close()
            progress.update('构建 bench 场景=%s/%s episode=%s 当前=%s', len(reports), len(scenes), len(episodes), sid)
        completed = True
    finally:
        write_rows(root / 'episodes.jsonl', episodes)
        write_json(root / 'oracle.json', oracle)
        write_json(root / 'summary.json', {'schema_version': schema,
            'status': 'complete' if completed else 'incomplete',
            'source_scenes': len(scenes), 'processed_scenes': len(reports), 'episodes': len(episodes),
            'candidate_episodes': sum(r['candidate_episodes'] for r in reports),
            'eligible_episodes': sum(r['eligible_episodes'] for r in reports),
            **({'source_val_scenes': source_val_count, 'reference_val_scenes': len(scenes),
                'outside_reference_plan': source_val_count - len(scenes)} if mode_run else {}),
            'new_calibration_rollouts': 0, 'scenes': reports, 'elapsed_s': time.monotonic() - started})
        progress.update('benchmark 构建收尾 场景=%s/%s 已写入episode=%s 输出=%s',
                        len(reports), len(scenes), len(episodes), root, force=True)
    benchmark_view(root)
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
    selected_backend = backend(policy_path)
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(gpu_ids[0]))
    agent_budget = ({'control_step_budget': policy_config.control_step_budget,
                     'attempt_timeout_s': policy_config.attempt_timeout_s, 'max_turns': policy_config.max_turns}
                    if selected_backend == 'pyrua' else None)
    root = create_run(output, {'benchmark': str(benchmark), 'policy': asdict(policy_config),
                              'seed': settings.seed, 'horizon': settings.horizon, 'wall_timeout_s': settings.wall_timeout_s,
                              'agent_budget': agent_budget,
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
            supported = robot.name == (getattr(policy_config,'embodiment','rby1m'))
            if not supported:
                record = {'episode_id': eid, 'status': 'unsupported_policy', 'reason': selected_backend+' policy does not support ' + robot.name,
                          'recovery_success': None, 'recording_complete': False, 'executed_steps': 0}
                append_row(root / 'results.jsonl', record)
                results.append(record)
                continue
            parent = sim = None
            try:
                parent = Simulation.from_snapshot(snapshot, robot, target=episode['task']['target_body'])
                if selected_backend == 'molmobot':
                    sim = native_scene(parent)
                else:
                    check_profile(robot,selected_backend,policy_path)
                    sim,parent = parent,None
                sim.scene_id = episode['scene_id']
                profiles[episode['scene_id']] = asdict(sim.robot.config)
                agent = selected_backend == 'pyrua'
                if policy is None:
                    policy = create_policy(policy_path, logs / f'{root.name}-policy.log')
                    if not agent:
                        from dataclasses import replace
                        policy.config = replace(policy.config, task_horizon=settings.horizon)
                width,height = (1024,576) if selected_backend == 'molmobot' else (saved['reference_execution']['width'],saved['reference_execution']['height'])
                # The agent's control-step and wall budgets are declared in its own policy config.
                config = ExecutionConfig(operation_backend=selected_backend, operation_base_mode='policy_relative',
                    policy_config=str(policy_path), width=width, height=height, radius_m=saved['reference_execution']['radius_m'],
                    attempt_timeout_s=policy.config.attempt_timeout_s if agent else settings.wall_timeout_s,
                    third_person_enabled=True, open_hold_s=saved['reference_execution'].get('open_hold_s', 1.))
                collection = CollectionConfig(output_dir=str(root), width=width, height=height, seed=episode['seed'],
                    max_steps=settings.horizon, record_video=True, record_images=False,
                    video_cameras={sim.robot.observation_camera: 'head' if selected_backend=='molmobot' else 'observation', 'third_person_camera': 'third_person'})
                with trial_logging(f'episode={eid} scene={episode["scene_id"]}', progress):
                    run = run_agent_attempt if agent else run_vla_attempt
                    result = run(sim, episode['task'], episode['station'], config, collection,
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
        evaluation_view(root)
        progress.update('benchmark 评测收尾 已记录episode=%s/%s 输出=%s', len(results), len(episodes), root, force=True)
    return root
