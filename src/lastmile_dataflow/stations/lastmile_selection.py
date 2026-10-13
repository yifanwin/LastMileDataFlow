"""Measured LastMile calibration, reusing the benchmark low/high selector."""
from dataclasses import asdict
from pathlib import Path
import re

import numpy as np

from ..benchmark.episodes import select_starts
from ..io import digest, file_digest, read_json
from .no_edit_statistics import is_episode_timeout


def evaluable(row):
    return row.get('status') in ('success', 'failure', 'planning_no_solution') or is_episode_timeout(row)


def accepted_trials(stations, trials, expected):
    """One valid outcome per planned trial; invalid predecessors remain evidence."""
    ids = {s['station_id'] for s in stations}
    if len(ids) != len(stations):
        raise ValueError('duplicate selected station')
    accepted = {}
    for row in trials:
        trial = row.get('trial')
        if row['station_id'] not in ids or type(trial) is not int or not 0 <= trial < expected:
            raise ValueError('foreign station or trial outside frozen budget')
        if evaluable(row):
            key = (row['station_id'], trial)
            if key in accepted:
                raise ValueError('duplicate valid calibration trial')
            accepted[key] = row
    return accepted


def recover_trial_records(scene_path, task, stations, trials, config):
    """Reconcile sealed outcomes after a crash between finish and trial-index append."""
    known = {str(Path(r['attempt']).resolve()) for r in trials}
    ids = {s['station_id'] for s in stations}
    recovered = []
    for outcome_path in sorted((Path(scene_path)/'attempts').glob('*/*/*/outcome.json')):
        attempt = outcome_path.parent
        if str(attempt.resolve()) in known or not (attempt/'config.json').is_file() or not (attempt/'artifacts.json').is_file():
            continue
        packet = read_json(attempt/'config.json')
        match = re.search(r'-t(\d+)-[0-9a-f]+$', attempt.name)
        if not match or packet.get('task',{}).get('task_id') != task['task_id'] or packet.get('path'):
            continue
        sid = packet.get('station',{}).get('station_id')
        if sid not in ids or packet.get('protocol') != asdict(config):
            raise ValueError('orphan calibration input mismatch')
        outcome = read_json(outcome_path)
        result = read_json(attempt/'result.json')
        status = 'infrastructure_error' if result.get('status') == 'infrastructure_error' else outcome['status']
        recovered.append({**outcome,'status':status,'station_id':sid,'trial':int(match[1]),
                          'attempt':str(attempt),'seed':packet['seed'],'recovered_after_interruption':True,
                          'videos':str(attempt/'videos.json') if (attempt/'videos.json').is_file() else None})
    return trials+recovered


def recover_rollout_records(scene_path, task, selection, trials, config):
    known = {str(Path(p['attempt']).resolve()) for p in selection['successful']+selection['failed_rollouts'] if p.get('attempt')}
    for outcome_path in sorted((Path(scene_path)/'attempts').glob('*/*/*/outcome.json')):
        attempt = outcome_path.parent
        if str(attempt.resolve()) in known or not (attempt/'config.json').is_file() or not (attempt/'artifacts.json').is_file():
            continue
        packet = read_json(attempt/'config.json')
        if packet.get('task',{}).get('task_id') != task['task_id'] or not packet.get('path'):
            continue
        if packet.get('protocol') != asdict(config):
            raise ValueError('orphan recovery input mismatch')
        outcome = read_json(outcome_path)
        if read_json(attempt/'result.json').get('status') == 'infrastructure_error':
            outcome['status'] = 'infrastructure_error'
        entry = {**outcome,'attempt':str(attempt),'start':packet['station'],'goal':packet['goal'],
                 'path':packet['path'],'seed':packet['seed'],'station_id':packet['station']['station_id'],
                 'recovered_after_interruption':True,
                 's0_failure_evidence':[r for r in trials if r['station_id']==packet['station']['station_id'] and r['status']!='success']}
        key = 'successful' if entry['status']=='success' and entry.get('navigation',{}).get('status')=='success' else 'failed_rollouts'
        selection[key].append(entry)
    return selection


def calibration_binding(scene_path, task, config):
    root = Path(scene_path)
    manifest = read_json(root/'scene_dataset_input/input_manifest.json')
    return {'scene_dataset_id': manifest['scene_dataset_id'], 'sample_id': manifest['sample_id'],
            'source_package_sha256': manifest['package_checksums_sha256'],
            'robot_sensor_config_id': manifest['robot_sensor_config_id'],
            'compiled_model_sha256': file_digest(root/'scene/model.mjb'), 'task_id': task['task_id']}


def station_calibration(stations, trials, expected, binding, config):
    accepted = accepted_trials(stations, trials, expected)
    rows = []
    for station in stations:
        sid = station['station_id']
        chosen = [accepted[(sid, i)] for i in range(expected) if (sid, i) in accepted]
        evidence = [r for r in trials if r['station_id'] == sid]
        k = sum(r['status'] == 'success' for r in chosen)
        rows.append({'station_id': sid, 'trials': len(chosen), 'successes': k,
                     'success_rate': k/len(chosen) if chosen else None,
                     'scheduled_trials': expected, 'complete': len(chosen) == expected,
                     'error_attempts': sum(not evaluable(r) for r in evidence),
                     'timeout_trials': sum(is_episode_timeout(r) for r in chosen),
                     'attempts': [{ 'trial': r['trial'], 'attempt': r['attempt'],
                                   'status': r['status'], 'seed': r.get('seed'),
                                   'accepted': accepted.get((sid, r['trial'])) is r}
                                  for r in evidence]})
    return {'schema_version': 'lastmilebench-station-rates-v1', 'backend': 'curobo', **binding,
            'selected_stations_sha256': digest(stations), 'stations': rows,
            'complete': bool(rows) and all(r['complete'] for r in rows),
            'trials_per_station': expected, 'execution_config': asdict(config),
            'execution_config_sha256': digest(asdict(config)),
            'denominator_rule': 'budgeted valid outcomes including planning_no_solution/timeouts; errors excluded',
            'path_validation': 'not_performed_in_calibration'}


def recovery_choices(stations, calibration, binding, config):
    """Incomplete maps never justify a formal recovery set."""
    if calibration['selected_stations_sha256'] != digest(stations):
        raise ValueError('calibration selected stations mismatch')
    if not calibration['complete']:
        return []
    if any(r['trials'] != config.trials_per_station or not r['complete'] for r in calibration['stations']):
        raise ValueError('incomplete formal calibration counts')
    choices = select_starts(stations, calibration, binding, limit=config.start_count,
                           low=config.lastmile_low, high=config.lastmile_high,
                           nearby_m=config.lastmile_nearby_m)
    rates = {r['station_id']: r['success_rate'] for r in calibration['stations']}
    lookup = {s['station_id']: s for s in stations}
    result = []
    for start, evidence in choices:
        goals = sorted((lookup[sid] for sid in evidence['s_plus']), key=lambda g:
                       (-rates[g['station_id']], float(np.linalg.norm(np.asarray(g['xy'])-start['xy'])), g['station_id']))
        result.append((start, goals, evidence))
    return result
