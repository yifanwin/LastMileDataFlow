"""从主 trial 记录计算实测率；平滑图不参与选择。"""


def evaluable(row):
    return row.get('status') in ('success', 'failure', 'planning_no_solution', 'timeout')


def station_calibration(stations, trials, expected, binding, config):
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
