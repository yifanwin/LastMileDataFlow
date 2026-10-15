"""Simple observed station-rate selection; no difficulty or path oracle."""
import numpy as np


def select_starts(stations, calibration, binding, *, limit=1, low=.2, high=.8, nearby_m=1.):
    valid = {s['station_id']:s for s in stations if s['geometry']=='valid'}
    if len(valid) != sum(s['geometry']=='valid' for s in stations):
        raise ValueError('duplicate station identity')
    if calibration is None:
        return [(s, {'status':'uncalibrated_smoke','recovery_verified':False}) for s in list(valid.values())[:limit]]
    # A heatmap from another model / scene / task cannot justify this episode.
    for key, value in binding.items():
        if calibration.get(key) != value:
            raise ValueError('calibration binding mismatch: '+key)
    rows = calibration['stations']
    if len({r['station_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate calibration station')
    rates = {}
    for r in rows:
        n, k = r['trials'], r['successes']
        if r['station_id'] not in valid or n <= 0 or not 0 <= k <= n:
            raise ValueError('invalid calibration station/counts')
        rate = k/n
        rates[r['station_id']] = rate
    result = []
    for sid in sorted(rates, key=lambda s:(rates[s],s)):
        if rates[sid] > low:
            continue
        neighbors = [other for other in sorted(rates) if rates[other]>=high and other!=sid
            and np.linalg.norm(np.asarray(valid[other]['xy'])-valid[sid]['xy'])<=nearby_m]
        if neighbors:
            result.append((valid[sid], {'status':'observed_low_high_nearby',
                'start_success_rate':rates[sid], 's_plus':neighbors,
                'nearby_m':nearby_m, 'low_threshold':low, 'high_threshold':high,
                'path_validation':'not_required_v0', 'recovery_verified':'observed_station_rates_only'}))
        if len(result)==limit:
            break
    return result


def summarize(episodes, results):
    expected = {e['episode_id'] for e in episodes}
    if len(expected)!=len(episodes):
        raise ValueError('duplicate benchmark episode')
    ids = [r['episode_id'] for r in results]
    if len(ids)!=len(set(ids)) or set(ids)-expected:
        raise ValueError('duplicate or foreign evaluation result')
    task_outcomes = ('success','failure','timeout')
    counts = {s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}
    valid = sum(counts.get(s,0) for s in task_outcomes)
    unknown = len(expected)-valid
    return {'scheduled':len(expected), 'reported':len(results), 'valid_task_outcomes':valid,
        'successes':counts.get('success',0), 'unknown_or_infra':unknown, 'status_counts':counts,
        'coverage':valid/len(expected) if expected else 0.,
        'task_success_rate':counts.get('success',0)/len(expected) if expected and unknown==0 else None,
        'observed_task_success_rate':counts.get('success',0)/valid if valid else None,
        'complete':unknown==0 and bool(expected),
        'denominator_rule':'full scheduled set; infra/unknown suppress official TSR; timeout is task failure'}
