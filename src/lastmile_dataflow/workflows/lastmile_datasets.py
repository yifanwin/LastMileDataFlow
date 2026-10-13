"""Thin scene-package orchestration; execution and model adaptation stay existing."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import traceback

from ..config import CollectionConfig, RobotConfig, construct
from ..io import canonical, digest, file_digest, read_json, write_json
from ..recording.protocol import code_identity, rows, safe_path
from ..stations.no_edit_config import load_no_edit_config, critical_config, RESEARCH_EXECUTION_CONTRACT
from ..stations.scene_package import package_manifest
from ..validation.canonical import episode_record
from .no_edit import collect_batch

SCHEMA = 'lastmile-datasets-research-v1'


def input_units(dataset, config, *, samples=None):
    """No GPU or physics allocation; validate before any new run is created."""
    dataset = Path(dataset).resolve()
    source = read_json(dataset / 'manifest.json')
    if source.get('schema_version') != 'scene-datasets-research-v2' or source.get('status') not in ('complete', 'failed'):
        raise ValueError('research scene dataset required; old datasets are not migrated in place')
    entries = [{'sample_id': sample['sample_id'], 'robot_sensor_config_id': config_id,
                'path': sample['path'] + '/robot_sensor_configs/' + config_id}
               for sample in rows(dataset / 'samples.jsonl') for config_id in sample['robot_sensor_config_ids']]
    if len({(e['sample_id'], e['robot_sensor_config_id']) for e in entries}) != len(entries):
        raise ValueError('duplicate scene package identity')
    requested = set(samples) if samples is not None else None
    if requested is not None and (not requested or requested-{e['sample_id'] for e in entries}):
        raise ValueError('unknown or empty sample subset')
    units = []
    for entry in entries:
        if requested is not None and entry['sample_id'] not in requested:
            continue
        package = safe_path(dataset, entry['path'])
        manifest = read_json(package / 'manifest.json')
        for key in ('sample_id', 'robot_sensor_config_id'):
            if manifest[key] != entry[key]:
                raise ValueError('package index binding mismatch: '+key)
        if manifest['scene_dataset_id'] != source['scene_dataset_id']:
            raise ValueError('foreign package dataset identity')
        version = read_json(package/'scene/version.json')
        robot = construct(RobotConfig, version['robot'])
        if robot.control_profile != 'position-v1':
            raise ValueError('research cuRobo execution requires position-v1; native input is not silently converted')
        task = read_json(package/'task.json')
        if task['operation'] not in config.operations:
            continue
        bound_config = replace(config, scene_package=str(package), lastmile_selection=True,
                               **manifest['binding']['station_geometry'])
        manifest = package_manifest(package, robot, bound_config)
        src = version['source']
        xml, metadata = Path(src['xml_path']), Path(src['metadata_path'])
        if not xml.is_file() or file_digest(xml) != src['xml_sha256'] or not metadata.is_file() or file_digest(metadata) != src['metadata_sha256']:
            raise ValueError('original scene source unavailable or modified')
        if not robot.model_path or not Path(robot.model_path).is_file():
            raise ValueError('robot asset unavailable')
        match = re.fullmatch(r'procthor-10k-val/val_(\d+)', manifest['scene_id'])
        if not match:
            raise ValueError('research collector supports ProcTHOR val scene packages')
        identity = {k:manifest[k] for k in ('scene_dataset_id','sample_id','robot_sensor_config_id','task_id')}
        units.append({**identity, 'unit_id':'unit-'+digest(identity)[:16], 'package':str(package),
                      'source_package_identity':manifest['package_identity'],
                      'compiled_model_sha256':manifest['scene_identity']['model.mjb'],
                      'case':manifest['case'], 'house':int(match[1]), 'dataset_dir':str(xml.parent),
                      'robot':asdict(robot), 'config':asdict(bound_config),
                      'selected_points':manifest['station_count'], 'operation':task['operation']})
    if not units:
        raise ValueError('no eligible packages in requested scope')
    return source, sorted(units, key=lambda u:(u['sample_id'], u['robot_sensor_config_id']))


def write_rows(path, items):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.tmp')
    with temporary.open('wb') as stream:
        for item in items:
            stream.write(canonical(item)+b'\n')
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def phase_ranges(attempt):
    path = Path(attempt)/'trajectory.jsonl'
    if not path.is_file():
        return []
    result = []
    for index, row in enumerate(rows(path)):
        phase = row.get('observations', {}).get('phase', 'unknown')
        command = row.get('command', {})
        item = {'phase':phase, 'first_step':index, 'last_step':index,
                'first_frame':index, 'last_frame':index+1,
                't_start':command.get('time_before_s'), 't_end':command.get('time_after_s')}
        if result and result[-1]['phase'] == phase:
            result[-1].update(last_step=index, last_frame=index+1, t_end=item['t_end'])
        else:
            result.append(item)
    return result


def summarize_episodes(entries):
    """Count attempts separately from accepted logical trials and train samples."""
    from ..stations.lastmile_selection import evaluable
    recoveries = [e for e in entries if e.get('role') == 'recovery']
    calibration = [e for e in entries if e.get('role') == 'calibration']
    accepted = {}
    for entry in calibration:
        if not entry.get('committed') or not evaluable({**entry, 'status': entry['termination_status'],
                                                       'reason': entry.get('termination_reason')}):
            continue
        key = (entry['sample_id'], entry['robot_sensor_config_id'], entry['trial_id'])
        if key in accepted:
            raise ValueError('duplicate valid calibration trial')
        accepted[key] = entry
    successes = sum(e.get('task_success') is True for e in accepted.values())
    return {'all_attempts': len(entries), 'calibration_attempts': len(calibration),
            'valid_calibration_trials': len(accepted), 'calibration_successes': successes,
            'observed_calibration_success_rate': successes / len(accepted) if accepted else None,
            'calibration_error_attempts': len(calibration) - len(accepted),
            'recovery_attempts': len(recoveries),
            'successful_recoveries': sum(e.get('task_success') is True for e in recoveries),
            'train_episodes': sum(e.get('train_eligible', False) for e in entries),
            'denominator_rule': 'one valid outcome per logical calibration trial; planning_no_solution and episode timeout included; errors excluded'}


def generate_summary(parent):
    """Rebuild statistics from the unique global view plus the frozen plan."""
    parent = Path(parent)
    summary = summarize_episodes(rows(parent / 'episodes.jsonl'))
    plan = read_json(parent / 'plan.json')
    manifest = read_json(parent / 'manifest.json')
    summary.update(status=manifest['status'], stage=manifest.get('stage'),
                   scheduled_units=len(plan['units']),
                   scheduled_calibration_trials=plan['scheduled_calibration_trials'],
                   diagnostic_subset=plan['diagnostic_subset'],
                   calibration_only=manifest.get('stage') == 'calibration',
                   lastmile_success_data=bool(summary['train_episodes']))
    expected = plan['scheduled_calibration_trials']
    summary['calibration_complete'] = bool(expected and summary['valid_calibration_trials'] == expected and not plan['diagnostic_subset'])
    summary['calibration_coverage'] = summary['valid_calibration_trials'] / expected if expected else 0.
    summary['calibration_success_rate'] = summary['observed_calibration_success_rate'] if summary['calibration_complete'] else None
    summary['reported_units'] = sum((Path(u['run_path']) / 'summary.json').is_file() for u in plan['units'])
    write_json(parent / 'summary.json', summary)
    return summary


def collect_views(parent, units, *, export_state_images=True):
    """Rebuild one global view from immutable per-attempt completion records."""
    parent = Path(parent)
    entries, unit_results, selections = [], [], []
    episode_ids = set()
    for unit in units:
        child = Path(unit['run_path'])
        scene = child / 'scenes' / f'val_{unit["house"]}'
        binding = {k: unit[k] for k in ('scene_dataset_id', 'sample_id', 'robot_sensor_config_id',
            'task_id', 'compiled_model_sha256', 'source_package_identity')}
        tasks = {}
        packets = {}
        for task_root in sorted((scene / 'tasks').glob('*')):
            if not (task_root / 'task.json').is_file():
                continue
            task = read_json(task_root / 'task.json')
            result = read_json(task_root / 'result.json') if (task_root / 'result.json').is_file() else {}
            selection = read_json(task_root / 'selection.json') if (task_root / 'selection.json').is_file() else {}
            tasks[task['task_id']] = (task, result)
            trial_rows = task_root / 'trials.json'
            planned = [('calibration', p) for p in read_json(trial_rows)] if trial_rows.is_file() else []
            planned += [('recovery', p) for p in selection.get('successful', []) + selection.get('failed_rollouts', [])]
            for role, packet in planned:
                if packet.get('attempt'):
                    target = Path(packet['attempt']).resolve()
                    if not target.is_relative_to((scene / 'attempts').resolve()):
                        raise ValueError('foreign attempt path in task records')
                    key = str(target)
                    if key in packets:
                        raise ValueError('duplicate attempt in task records')
                    packets[key] = (role, packet)
            selections.append({**binding, 'selection_ref': os.path.relpath(task_root / 'selection.json', parent),
                               'calibration_ref': os.path.relpath(task_root / 'calibration.json', parent),
                               'start_groups': selection.get('start_groups', [])})
            unit_results.append({'unit_id': unit['unit_id'], **binding, 'status': result.get('status', 'incomplete'),
                'selected_points': unit['selected_points'], 'task_result': os.path.relpath(task_root / 'result.json', parent)})
        attempts = {p.parent.resolve() for name in ('attempt.json', 'episode.json')
                    for p in (scene / 'attempts').glob('*/*/*/' + name)} | {Path(p) for p in packets}
        for attempt in sorted(attempts):
            committed = (attempt / 'episode.json').is_file()
            if committed:
                record = episode_record(attempt)
                if record['task_id'] != unit['task_id']:
                    raise ValueError('foreign episode task')
                if record.get('compiled_model_sha256') != unit['compiled_model_sha256']:
                    raise ValueError('episode model identity mismatch')
            else:
                cfg = read_json(attempt / 'config.json') if (attempt / 'config.json').is_file() else {}
                inferred = 'recovery' if cfg.get('path') else 'calibration' if cfg.get('station') else 'uncommitted_attempt'
                role, packet = packets.get(str(attempt), (inferred, {}))
                from ..stations.lastmile_selection import evaluable
                if evaluable(packet):
                    raise ValueError('completed trial is missing its episode commit: ' + str(attempt))
                record = {'episode_id': attempt.name, 'role': role, 'task_id': unit['task_id'],
                    'termination_status': packet.get('status', 'unknown'), 'task_success': None,
                    'trial': packet.get('trial'), 'station_id': packet.get('station_id') or cfg.get('station', {}).get('station_id'),
                    's0': cfg.get('station') if role == 'recovery' else None, 's1': cfg.get('goal'),
                    'seed': cfg.get('seed'), 'scene_dataset_binding': cfg.get('scene_dataset_binding'),
                    'termination_reason': packet.get('reason'), 'recording_complete': False, 'data_valid': False}
            source_binding = record.get('scene_dataset_binding')
            if source_binding is not None and any(source_binding.get(k) != unit[k] for k in
                    ('scene_dataset_id', 'sample_id', 'robot_sensor_config_id', 'task_id')):
                raise ValueError('episode sample/configuration identity mismatch')
            if source_binding is not None and source_binding.get('package_identity') != unit['source_package_identity']:
                raise ValueError('episode source package identity mismatch')
            if record['episode_id'] in episode_ids:
                raise ValueError('duplicate episode identity')
            episode_ids.add(record['episode_id'])
            task, result = tasks.get(record.get('task_id'), ({}, {}))
            eligible = bool(committed and result.get('dataset_eligible') and record.get('role') == 'recovery'
                and record.get('execution_status') == 'executed' and record.get('navigation_success') is True
                and record.get('task_success') is True and record.get('simulation_valid') is True
                and record.get('recording_complete') is True and record.get('data_valid') is True
                and record.get('continuous_simulation') is True)
            entry = {**record, **binding, 'committed': committed,
                     'attempt': os.path.relpath(attempt, parent), 'train_eligible': eligible}
            entries.append(entry)
            if eligible and export_state_images and task.get('operation') == 'pick':
                from ..integrations.molmobot_dataset import export_molmobot
                output = parent / 'exports' / 'molmobot' / record['episode_id']
                try:
                    if output.exists():
                        exported = read_json(output / 'export_config.json')
                        if exported['source_episode_id'] != record['episode_id'] or exported['source_config_sha256'] != record['config_sha256']:
                            raise ValueError('existing export source identity mismatch')
                    else:
                        export_molmobot(attempt, output, state_images_only=True, seed=42)
                except Exception as exc:
                    from ..runtime.episode_guard import classify_episode_exception
                    if classify_episode_exception(exc, 'recording')['failure_scope'] == 'shared':
                        raise
                    write_json(output.with_suffix('.error.json'), {'episode_id': record['episode_id'],
                        'status': 'failed', 'reason': type(exc).__name__ + ':' + str(exc)})
    for unit_result in unit_results:
        selected = next((s for s in selections if s['sample_id'] == unit_result['sample_id']
                         and s['robot_sensor_config_id'] == unit_result['robot_sensor_config_id']), {})
        groups = []
        for group in selected.get('start_groups', []):
            endpoints = {e['s1']['station_id'] for e in entries if e['train_eligible']
                and e['sample_id'] == unit_result['sample_id'] and e['robot_sensor_config_id'] == unit_result['robot_sensor_config_id']
                and e['s0']['station_id'] == group['s0']}
            groups.append({**group, 'train_s1': sorted(endpoints), 'train_s1_count': len(endpoints),
                           'minimum_met': bool(endpoints),
                           'warnings': ['single_successful_solution'] if len(endpoints) == 1 else []})
        unit_result['start_groups'] = groups
    write_rows(parent / 'episodes.jsonl', entries)
    write_json(parent / 'selections.json', selections)
    return {**summarize_episodes(entries), 'unit_results': unit_results}


def collect(dataset, assets_dir, config_path, output_dir, *, run_id, gpu_ids, workers=1,
            samples=None, resume=False, calibration_only=False, max_trials=None,
            preflight_only=False, export_state_images=True):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',run_id):
        raise ValueError('unsafe parent run ID')
    if type(workers) is not int or workers != 1:
        raise ValueError('v0 runs scene packages serially; use --workers 1')
    if not gpu_ids or any(type(g) is not int or g < 0 for g in gpu_ids) or len(set(gpu_ids)) != len(gpu_ids):
        raise ValueError('invalid GPU candidate list')
    if max_trials is not None and (type(max_trials) is not int or max_trials <= 0):
        raise ValueError('invalid diagnostic trial budget')
    dataset, assets_dir, output_dir = Path(dataset).resolve(), Path(assets_dir).resolve(), Path(output_dir).resolve()
    config = load_no_edit_config(config_path)
    if config.operation_backend != 'curobo' or config.augmented_variant or config.scene_package:
        raise ValueError('parent config must be an unbound cuRobo configuration')
    source, units = input_units(dataset,config,samples=samples)
    for unit in units:
        unit['run_id'] = run_id+'-'+unit['unit_id']
        unit['run_path'] = str(output_dir/'no_edit'/unit['run_id'])
    identity = {'schema_version':SCHEMA,'dataset':str(dataset),'source_dataset_id':source.get('scene_dataset_id'),
                'assets_dir':str(assets_dir),'output_dir':str(output_dir),'units':units,
                'execution_contract': RESEARCH_EXECUTION_CONTRACT,'max_trials':max_trials}
    critical = {**identity, 'units': [{**u, 'config': critical_config(u.get('config', {}))} for u in units]}
    root = output_dir/'lastmile_datasets'/run_id
    root.mkdir(parents=True,exist_ok=resume)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run_path = root/'run.json'
        if resume:
            old = read_json(run_path)
            if old['input_digest'] != digest(critical):
                raise ValueError('parent resume critical input/config mismatch; use a new run ID')
        else:
            write_json(run_path,{**identity,'input_digest':digest(critical),'source_status':source['status'],
                                 'source_missing_samples':source.get('missing_samples',0),
                                 'code': code_identity(), 'created_at_utc':datetime.now(timezone.utc).isoformat()})
        if preflight_only and resume and (root / 'manifest.json').is_file() and read_json(root / 'manifest.json')['status'] == 'complete':
            return root
        stage = 'preflight' if preflight_only else 'calibration' if calibration_only else 'rollout'
        write_json(root / 'manifest.json', {'schema_version': SCHEMA, 'status': 'building',
            'run_id': run_id, 'source_dataset_id': source.get('scene_dataset_id'), 'stage': stage})
        write_json(root / 'plan.json', {'units': units,
            'scheduled_calibration_trials': sum(u['selected_points'] * config.trials_per_station for u in units),
            'trials_per_station': config.trials_per_station, 'diagnostic_subset': max_trials is not None,
            'seed': config.seed, 'execution_contract': RESEARCH_EXECUTION_CONTRACT})
        if preflight_only:
            write_json(root / 'summary.json', {'status': 'building', 'stage': stage, 'units': len(units),
                       'selected_points': sum(u['selected_points'] for u in units), 'no_physical_execution': True})
            return root
        states = read_json(root/'progress.json').get('units',{}) if (root/'progress.json').is_file() else {}
        failure = None
        try:
            for unit in units:
                if states.get(unit['unit_id'],{}).get('status')=='completed' and not (
                        states[unit['unit_id']].get('calibration_only') and not calibration_only):
                    continue
                cfg = construct(type(config),unit['config'])
                collection = CollectionConfig(output_dir=str(output_dir),seed=cfg.seed,width=cfg.width,
                                              height=cfg.height,raw_first=True,record_depth=True)
                write_json(root/'progress.json',{'status':'building','active_unit':unit['unit_id'],'units':states,
                                                'calibration_only':calibration_only})
                print('LastMile unit '+unit['sample_id']+' / '+unit['robot_sensor_config_id'],flush=True)
                child = collect_batch(unit['dataset_dir'],assets_dir,construct(RobotConfig,unit['robot']),cfg,collection,
                                      run_id=unit['run_id'],gpu_ids=gpu_ids,max_workers=1,houses=[unit['house']],
                                      resume=resume and Path(unit['run_path']).exists(),max_trials=max_trials,
                                      calibration_only=calibration_only)
                summary = read_json(child/'summary.json')
                states[unit['unit_id']] = {'status':summary['status'],'calibration_only':calibration_only}
                write_json(root/'progress.json',{'status':'building','active_unit':None,'units':states})
        except BaseException as exc:
            failure = {'status':'failed','reason':type(exc).__name__+':'+str(exc),'traceback':traceback.format_exc()}
            write_json(root/'interruption.json',failure)
            raise
        finally:
            try:
                view = collect_views(root,units,export_state_images=export_state_images)
            except BaseException:
                write_json(root / 'manifest.json', {'schema_version': SCHEMA, 'status': 'failed',
                    'run_id': run_id, 'source_dataset_id': source.get('scene_dataset_id'), 'stage': stage})
                raise
            complete = not failure and len(states)==len(units) and all(v['status']=='completed' for v in states.values())
            status = 'complete' if complete else 'failed'
            write_json(root / 'manifest.json', {'schema_version': SCHEMA, 'status': status, 'stage': stage,
                'run_id': run_id, 'source_dataset_id': source.get('scene_dataset_id'),
                'diagnostic_subset': max_trials is not None})
            summary = generate_summary(root)
            write_json(root / 'progress.json', {'status': status, 'active_unit': None, 'units': states})
    return root
