"""从主 trial 记录计算实测率；平滑图不参与选择。"""
from .benchmark import select_starts


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


def recovery_choices(stations, calibration, binding, config):
    if not calibration['complete']:
        return []
    lookup = {station['station_id']: station for station in stations}
    rates = {row['station_id']: row['successes'] / row['trials'] for row in calibration['stations']}
    choices = select_starts(stations, calibration, binding, limit=config.start_count,
                           low=config.lastmile_low, high=config.lastmile_high, nearby_m=config.lastmile_nearby_m)
    return [(station, [lookup[sid] for sid in sorted(evidence['s_plus'], key=lambda sid: -rates[sid])], evidence)
            for station, evidence in choices]
