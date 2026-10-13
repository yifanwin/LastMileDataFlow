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
from ..stations.no_edit_config import load_no_edit_config
from ..stations.scene_package import package_manifest
from ..validation.canonical import audit_canonical
from .no_edit import collect_batch

SCHEMA = 'lastmile-datasets-v0'


def verify_files(root):
    root = Path(root).resolve()
    checksums = read_json(root/'checksums.json')
    checksums = checksums.get('files_sha256', checksums)
    if 'manifest.json' not in checksums:
        raise ValueError('manifest missing from seal')
    for name, sha in checksums.items():
        path = safe_path(root, name)
        if not path.is_file() or file_digest(path) != sha:
            raise ValueError('sealed input modified: '+name)
    return read_json(root/'manifest.json')


def input_units(dataset, config, *, samples=None):
    """No GPU or physics allocation; validate before any new run is created."""
    dataset = Path(dataset).resolve()
    source = verify_files(dataset)
    if source.get('schema_version') != 'scene-datasets-v1' or source.get('status') not in ('sealed', 'partial'):
        raise ValueError('sealed scene datasets (or sealed packages in a partial dataset) required')
    entries = rows(dataset/'packages.jsonl')
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
        manifest = package_manifest(package)
        for key in ('sample_id', 'robot_sensor_config_id'):
            if manifest[key] != entry[key]:
                raise ValueError('package index binding mismatch: '+key)
        if manifest['scene_dataset_id'] != source['scene_dataset_id']:
            raise ValueError('foreign package dataset identity')
        version = read_json(package/'scene/version.json')
        robot = construct(RobotConfig, version['robot'])
        if robot.control_profile != 'position-v1':
            raise ValueError('v0 cuRobo execution requires position-v1; native input is not silently converted')
        task = read_json(package/'task.json')
        if task['operation'] not in config.operations:
            continue
        bound_config = replace(config, scene_package=str(package), lastmile_selection=True,
                               **manifest['binding']['station_geometry'])
        package_manifest(package, robot, bound_config)
        src = version['source']
        xml, metadata = Path(src['xml_path']), Path(src['metadata_path'])
        if not xml.is_file() or file_digest(xml) != src['xml_sha256'] or not metadata.is_file() or file_digest(metadata) != src['metadata_sha256']:
            raise ValueError('original scene source unavailable or modified')
        if not robot.model_path or not Path(robot.model_path).is_file():
            raise ValueError('robot asset unavailable')
        match = re.fullmatch(r'procthor-10k-val/val_(\d+)', manifest['scene_id'])
        if not match:
            raise ValueError('v0 collector supports ProcTHOR val scene packages')
        identity = {k:manifest[k] for k in ('scene_dataset_id','sample_id','robot_sensor_config_id','task_id')}
        units.append({**identity, 'unit_id':'unit-'+digest(identity)[:16], 'package':str(package),
                      'source_package_sha256':manifest['package_checksums_sha256'],
                      'compiled_model_sha256':file_digest(package/'scene/model.mjb'),
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


def attempt_reference(parent, packet):
    attempt = Path(packet['attempt']).resolve()
    canonical_path = attempt/'canonical/manifest.json'
    return {'attempt':os.path.relpath(attempt, parent),
            'canonical_status':'sealed' if canonical_path.is_file() and read_json(canonical_path).get('sealed') else 'incomplete',
            'canonical_checksums_sha256':file_digest(attempt/'canonical/checksums.json') if (attempt/'canonical/checksums.json').is_file() else None,
            'episode_id':read_json(canonical_path)['episode_id'] if canonical_path.is_file() else attempt.name}


def collect_views(parent, units, *, export_state_images=True):
    """Publish views of real attempts only; success and audit are independent."""
    parent = Path(parent)
    all_attempts, recoveries, train, exports = [], [], [], []
    unit_results = []
    seen = set()
    for unit in units:
        child = Path(unit['run_path'])
        scene = child/'scenes'/f'val_{unit["house"]}'
        for task_root in sorted((scene/'tasks').glob('*')):
            if not (task_root/'task.json').is_file():
                continue
            task = read_json(task_root/'task.json')
            binding = {k:unit[k] for k in ('scene_dataset_id','sample_id','robot_sensor_config_id','task_id','compiled_model_sha256','source_package_sha256')}
            result = read_json(task_root/'result.json') if (task_root/'result.json').is_file() else {'status':'incomplete'}
            unit_results.append({'unit_id':unit['unit_id'], **binding, 'status':result['status'],
                                 'selected_points':unit['selected_points'], 'task_result':os.path.relpath(task_root/'result.json',parent)})
            if (task_root/'calibration.json').is_file():
                cal = read_json(task_root/'calibration.json')
                write_json(parent/'calibration'/unit['sample_id']/unit['robot_sensor_config_id']/'rates.json',cal)
            selection = read_json(task_root/'selection.json') if (task_root/'selection.json').is_file() else {'successful':[],'failed_rollouts':[]}
            write_json(parent/'selections'/unit['sample_id']/(unit['robot_sensor_config_id']+'.json'),selection)
            trials = read_json(task_root/'trials.json') if (task_root/'trials.json').is_file() else []
            packets = [('calibration',p) for p in trials]
            packets += [('recovery',p) for p in selection['successful']+selection['failed_rollouts'] if p.get('attempt')]
            for role, packet in packets:
                identity = str(Path(packet['attempt']).resolve())
                if identity in seen:
                    raise ValueError('duplicate attempt in collection evidence')
                seen.add(identity)
                ref = attempt_reference(parent,packet)
                entry = {**binding, **ref, 'role':role, 'status':packet['status'],
                         'reason':packet.get('reason'), 'station_id':packet.get('station_id'),
                         'trial':packet.get('trial'), 'seed':packet.get('seed')}
                all_attempts.append(entry)
                if role != 'recovery':
                    continue
                attempt = Path(identity)
                audit = audit_canonical(attempt)
                audit_path = parent/'audits'/(ref['episode_id']+'.json')
                write_json(audit_path,audit)
                outcome_path = attempt/'canonical/outcomes.json'
                outcome = read_json(outcome_path) if outcome_path.is_file() else {}
                eligible = bool(result.get('dataset_eligible') and packet['status']=='success'
                                and packet.get('navigation',{}).get('status')=='success' and audit['valid']
                                and outcome.get('success') is True and outcome.get('simulation_valid') is True
                                and outcome.get('recording_complete') is True
                                and packet.get('continuous_simulation') is True)
                entry = {**entry, 's0':packet['start'], 's1':packet['goal'], 'path':packet['path'],
                         'navigation':packet.get('navigation'), 'continuous_simulation':packet.get('continuous_simulation'),
                         'phases':phase_ranges(attempt), 'audit':os.path.relpath(audit_path,parent),
                         'audit_valid':audit['valid'], 'train_eligible':eligible,
                         'selection_sha256':file_digest(parent/'selections'/unit['sample_id']/(unit['robot_sensor_config_id']+'.json'))}
                recoveries.append(entry)
                if eligible:
                    train.append(entry)
                    if export_state_images and task['operation']=='pick':
                        from ..integrations.molmobot_dataset import export_molmobot
                        output = parent.parent.parent/'derived_packages'/(parent.name+'-'+ref['episode_id'])
                        try:
                            if output.exists():
                                derived = verify_files(output)
                                if derived.get('source_checksums_sha256') != ref['canonical_checksums_sha256'] or not derived.get('configuration',{}).get('state_images_only'):
                                    raise ValueError('existing adapter export source mismatch')
                            else:
                                export_molmobot(attempt,output,state_images_only=True,seed=42)
                            exports.append({'episode_id':ref['episode_id'],'status':'state_images_exported',
                                            'output':os.path.relpath(output,parent),'action_supervision':'unavailable_without_gripper_mapping'})
                        except Exception as exc:
                            exports.append({'episode_id':ref['episode_id'],'status':'adapter_error','reason':str(exc)})
        # A hard interruption can leave an attempt not yet in trials/selection.
        # Keep it in the all-outcome view without inventing pairing or eligibility.
        for attempt_file in sorted((scene/'attempts').glob('*/*/*/attempt.json')):
            attempt=attempt_file.parent.resolve()
            if str(attempt) in seen:
                continue
            seen.add(str(attempt))
            value=read_json(attempt_file)
            ref=attempt_reference(parent,{'attempt':str(attempt)})
            all_attempts.append({**{k:unit[k] for k in ('scene_dataset_id','sample_id','robot_sensor_config_id','task_id','compiled_model_sha256','source_package_sha256')},
                                 **ref,'role':'unindexed_attempt','status':value.get('status','incomplete'),
                                 'reason':'attempt_not_yet_in_trial_or_selection_index','train_eligible':False})
    # Count distinct audited endpoints per S0, not raw successes or repeat attempts.
    for unit_result in unit_results:
        unit = next(u for u in units if u['unit_id']==unit_result['unit_id'])
        selected_path=parent/'selections'/unit['sample_id']/(unit['robot_sensor_config_id']+'.json')
        selection=read_json(selected_path) if selected_path.is_file() else {}
        groups=[]
        for group in selection.get('start_groups',[]):
            endpoints={e['s1']['station_id'] for e in train
                       if all(e.get(k)==unit_result.get(k) for k in
                              ('sample_id','robot_sensor_config_id','task_id','source_package_sha256'))
                       and e['s0']['station_id']==group['s0']}
            groups.append({**group,'train_s1':sorted(endpoints),'train_s1_count':len(endpoints),
                           'minimum_met':bool(endpoints),
                           'warnings':['single_successful_solution'] if len(endpoints)==1 else [],
                           'status':'retained' if endpoints else 'no_audited_successful_solution'})
        unit_result['start_groups']=groups
        unit_result['warnings']=selection.get('warnings',[])
    write_rows(parent/'all_attempts_index.jsonl',all_attempts)
    write_rows(parent/'lastmile_index.jsonl',recoveries)
    write_rows(parent/'train_index.jsonl',train)
    write_rows(parent/'exports.jsonl',exports)
    write_rows(parent/'unit_results.jsonl',unit_results)
    return {'all_attempts':len(all_attempts),'calibration_attempts':sum(e['role']=='calibration' for e in all_attempts),
            'recovery_attempts':len(recoveries),'successful_recoveries':sum(e['status']=='success' for e in recoveries),
            'audit_valid_recoveries':sum(e['audit_valid'] for e in recoveries),'train_episodes':len(train),
            'state_image_exports':sum(e['status']=='state_images_exported' for e in exports),
            'adapter_errors':sum(e['status']=='adapter_error' for e in exports), 'unit_results':unit_results}


def collect(dataset, assets_dir, config_path, output_dir, *, run_id, gpu_ids, workers=1,
            samples=None, resume=False, calibration_only=False, max_trials=None,
            preflight_only=False, export_state_images=True, publication_root=None):
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
    root = Path(publication_root).resolve() if publication_root else output_dir/'lastmile_datasets'/run_id
    if publication_root and len({u['sample_id'] for u in units}) != len(units):
        raise ValueError('direct sample layout requires one physical config per sample')
    for unit in units:
        unit['run_id'] = run_id+'-'+unit['unit_id']
        unit['run_path'] = str(root/'samples'/unit['sample_id']) if publication_root else str(output_dir/'no_edit'/unit['run_id'])
    identity = {'schema_version':SCHEMA,'dataset':str(dataset),'source_manifest_sha256':file_digest(dataset/'manifest.json'),
                'assets_dir':str(assets_dir),'output_dir':str(output_dir),'units':units,
                'code_sha256':code_identity()['code_sha256'],'max_trials':max_trials,
                'preflight_only':preflight_only,'export_state_images':export_state_images}
    root.mkdir(parents=True,exist_ok=resume)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run_path = root/'run.json'
        if resume:
            old = read_json(run_path)
            if old['input_digest'] != digest(identity):
                raise ValueError('parent resume input/config/code mismatch; use a new run ID')
            if (root/'manifest.json').is_file():
                verify_files(root)
                return root
        else:
            write_json(run_path,{**identity,'input_digest':digest(identity),'source_status':source['status'],
                                 'source_missing_samples':source.get('missing_samples',0),
                                 'created_at_utc':datetime.now(timezone.utc).isoformat()})
        if preflight_only:
            write_json(root/'summary.json',{'status':'preflight_complete','units':len(units),
                       'selected_points':sum(u['selected_points'] for u in units),'no_physical_execution':True})
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
                write_json(root/'progress.json',{'status':'running','active_unit':unit['unit_id'],'units':states,
                                                'calibration_only':calibration_only})
                print('LastMile unit '+unit['sample_id']+' / '+unit['robot_sensor_config_id'],flush=True)
                child = collect_batch(unit['dataset_dir'],assets_dir,construct(RobotConfig,unit['robot']),cfg,collection,
                                      run_id=unit['run_id'],gpu_ids=gpu_ids,max_workers=1,houses=[unit['house']],
                                      resume=resume and Path(unit['run_path']).exists(),max_trials=max_trials,
                                      calibration_only=calibration_only,
                                      **({'run_root':unit['run_path']} if publication_root else {}))
                summary = read_json(child/'summary.json')
                states[unit['unit_id']] = {'status':summary['status'],'calibration_only':calibration_only}
                write_json(root/'progress.json',{'status':'running','active_unit':None,'units':states})
        except BaseException as exc:
            failure = {'status':'incomplete','reason':type(exc).__name__+':'+str(exc),'traceback':traceback.format_exc()}
            write_json(root/'interruption.json',failure)
            raise
        finally:
            view = collect_views(root,units,export_state_images=export_state_images)
            complete = not failure and len(states)==len(units) and all(v['status']=='completed' for v in states.values())
            status = 'calibration_complete' if complete and calibration_only else 'completed' if complete else 'incomplete'
            summary = {**view,'status':status,'scheduled_units':len(units),'reported_units':len(states),
                       'calibration_only':calibration_only,'diagnostic_subset':max_trials is not None,
                       'lastmile_success_data':bool(view['train_episodes']),
                       'benchmark_calibration_compatibility':'requires_exact_model_and_robot_sensor_binding'}
            write_json(root/'summary.json',summary)
            write_json(root/'progress.json',{'status':status,'active_unit':None,'units':states})
            # A calibration checkpoint can advance via resume. A full completed publication is immutable.
            if complete and not calibration_only:
                write_json(root/'manifest.json',{'schema_version':SCHEMA,'status':'sealed',
                           'run_id':run_id,'source_dataset_id':source['scene_dataset_id'],
                           'source_manifest_sha256':identity['source_manifest_sha256'],
                           'summary':summary,'references':'relative to publication directory; external attempts content-hash bound'})
                write_json(root/'checksums.json',{str(p.relative_to(root)):file_digest(p) for p in sorted(root.rglob('*'))
                                                if p.is_file() and p.name not in ('checksums.json','run.lock')})
    return root
