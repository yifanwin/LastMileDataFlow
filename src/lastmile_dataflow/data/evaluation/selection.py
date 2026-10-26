"""从主 trial 记录计算实测率；平滑图不参与选择。"""


def evaluable(row):
    return row.get('status') in ('success', 'failure', 'planning_no_solution', 'timeout')


def station_calibration(stations, trials, expected, binding, config):
    if config.mode_config is not None:
        if expected != 3:
            raise ValueError('task-mode calibration requires three trials per station')
        return mode_calibration(stations, trials, binding, config.operation_backend)
    counts = {station['station_id']: {} for station in stations}
    for row in trials:
        if evaluable(row):
            samples = counts[row['station_id']]
            if row['trial'] in samples:
                raise ValueError('duplicate evaluated trial')
            samples[row['trial']] = row
    rates = []
    for station in stations:
        samples = counts[station['station_id']].values()
        rates.append({'station_id': station['station_id'], 'trials': len(samples),
                      'successes': sum(row['status'] == 'success' for row in samples)})
    return {**binding, 'backend': config.operation_backend, 'stations': rates,
            'complete': all(row['trials'] == expected for row in rates)}


def eligible_stations(stations, calibration, config):
    """资格仅由完整标定和含等号阈值决定，不使用距离或数量上限。"""
    if not calibration['complete']:
        return [], []
    rates = {row['station_id']: row['successes'] / row['trials'] for row in calibration['stations']}
    observed = [{**station, 'success_rate': rates[station['station_id']]}
                for station in stations if station['geometry'] == 'valid']
    return ([station for station in observed if station['success_rate'] <= config.lastmile_low],
            [station for station in observed if station['success_rate'] >= config.lastmile_high])


def generate_pairs(starts, goals):
    """同一场景任务的全组合；无路径和远距离不在这里过滤。"""
    return [(start, goal) for start in starts for goal in goals]


def mode_trial_evaluable(row):
    """Planning no-solution is measured; infrastructure and unknown timeouts are not failures."""
    if row.get('recording_complete') is not True or row.get('failure_scope') == 'shared':
        return False
    status = row.get('status')
    outcome = row.get('task_outcome') or {}
    if status == 'planning_no_solution':
        return True
    if status in ('success', 'failure'):
        return outcome.get('status') == status
    return status == 'timeout' and outcome.get('status') == 'failure'


def mode_calibration(stations, trials, binding, backend):
    """Bind the unique raw trial set to the full 3-per-pose plan."""
    counts = {station['station_id']: {} for station in stations}
    invalid = []
    for row in trials:
        if any(row.get(key) != value for key, value in binding.items()):
            raise ValueError('task-mode trial binding mismatch')
        identity, trial = row['station_id'], row['trial']
        if identity not in counts or type(trial) is not int or not 0 <= trial < 3:
            raise ValueError('task-mode trial outside planned station/repetition')
        if trial in counts[identity]:
            raise ValueError('duplicate task-mode trial')
        counts[identity][trial] = row
        if not mode_trial_evaluable(row):
            invalid.append({'station_id': identity, 'trial': trial, 'rollout_id': row.get('rollout_id'),
                            'status': row['status'], 'reason': row.get('reason')})
    rates = []
    for station in stations:
        observed = counts[station['station_id']]
        valid = [row for row in observed.values() if mode_trial_evaluable(row)]
        rates.append({'station_id': station['station_id'], 'trials': len(valid),
            'observed_trials': len(observed), 'successes': sum(row['status'] == 'success' for row in valid),
            'complete': len(valid) == 3, 'missing_trials': sorted(set(range(3)) - observed.keys())})
    complete = bool(rates) and all(row['complete'] for row in rates)
    success = sum(row['successes'] for row in rates)
    eligibility = ('incomplete' if not complete else 'no_success' if not success else
                   'no_high_rate_s1' if not any(row['successes'] >= 2 for row in rates) else 'eligible')
    return {**binding, 'backend': backend, 'stations': rates, 'complete': complete,
            'expected_trials_per_station': 3, 'expected_trials': 3 * len(stations),
            'observed_trials': len(trials), 'invalid_trials': invalid,
            'task_eligibility': eligibility}


def mode_station_pools(stations, calibration):
    """Order 0/3 then 1/3 starts; each label chooses qualified 3/3 then 2/3 goals."""
    if not calibration['complete']:
        return {'starts': [], 'goals': []}
    rates = {row['station_id']: row for row in calibration['stations']}
    observed = [{**station, 'successes': rates[station['station_id']]['successes'], 'trials': 3,
                 'success_rate': rates[station['station_id']]['successes'] / 3}
                for station in stations if station['geometry'] == 'valid']
    return {'starts': sorted((r for r in observed if r['successes'] <= 1),
                            key=lambda r: (r['successes'], r['station_id'])),
            'goals': sorted((r for r in observed if r['successes'] >= 2),
                           key=lambda r: (-r['successes'], r['station_id']))}


def select_mode_goal(goals, relation):
    """A nonqualifying 3/3 pose must not hide a qualifying 2/3 pose."""
    checks = []
    for successes in (3, 2):
        qualified = []
        for goal in goals:
            if goal['successes'] != successes:
                continue
            evidence = {**relation(goal), 'station_id': goal['station_id'], 'successes': successes}
            checks.append(evidence)
            if evidence['status'] == 'matched':
                qualified.append(evidence)
        if qualified:
            chosen = min(qualified, key=lambda r: (
                r['path_length_m'] if r.get('path_length_m') is not None else float('inf'), r['station_id']))
            return {'status': 'matched', 'evidence_s1': chosen['station_id'],
                    'successes': successes, 'qualifying_s1_ids': sorted(r['station_id'] for r in qualified),
                    'checks': checks}
    return {'status': 'unresolved' if any(r['status'] == 'unresolved' for r in checks) else 'unmatched',
            'evidence_s1': None, 'qualifying_s1_ids': [], 'checks': checks}
