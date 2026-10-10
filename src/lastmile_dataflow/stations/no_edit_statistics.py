"""Measured rates and spatial selection; unknown is never converted to failure."""
from collections import Counter
import numpy as np


def aggregate(stations, trials, expected):
    result = []
    for station in stations:
        rows = [r for r in trials if r['station_id'] == station['station_id']]
        terminal = [r for r in rows if r['status'] in ('success','failure','planning_no_solution')]
        successes = sum(r['status'] == 'success' for r in terminal)
        reasons = Counter(r.get('attribution',{}).get('category','Unknown')
                          for r in terminal if r['status'] != 'success')
        result.append({**station, 'attempts': [r['attempt'] for r in rows],
                       'terminal_trials': len(terminal), 'successes': successes,
                       'success_rate': successes/len(terminal) if terminal else None,
                       'complete': station['geometry'] == 'valid' and len(terminal) == expected,
                       'failure_reasons': dict(reasons)})
    return result


def construction_rate(rows, expected, threshold):
    valid = [r for r in rows if r['geometry'] == 'valid']
    if not valid:
        return {'status': 'no_valid_stations', 'success_rate': None}
    if not all(r['complete'] and r['terminal_trials'] == expected for r in valid):
        return {'status': 'incomplete', 'success_rate': None}
    successes = sum(r['successes'] for r in valid); denominator = expected*len(valid)
    return {'status': 'discarded_low_success' if successes/denominator < threshold else 'eligible',
            'success_rate': successes/denominator, 'successes': successes,
            'terminal_trials': denominator, 'threshold': threshold}


def select_starts(rows, count, reachable=None):
    candidates = [r for r in rows if r['complete'] and r['success_rate'] < 1
                  and (reachable is None or reachable(r))]
    chosen = []
    while candidates and len(chosen) < count:
        rate = min(r['success_rate'] for r in candidates)
        tied = [r for r in candidates if r['success_rate'] == rate]
        if chosen:
            score = lambda r: min(np.linalg.norm(np.asarray(r['xy'])-s['xy']) for s in chosen)
            picked = max(tied,key=lambda r:(score(r),r['station_id']))
        else:
            center = np.mean([r['xy'] for r in candidates],axis=0)
            picked = max(tied,key=lambda r:(np.linalg.norm(np.asarray(r['xy'])-center),r['station_id']))
        chosen.append(picked); candidates.remove(picked)
    return chosen


def success_goals(rows):
    return sorted((r for r in rows if r['complete'] and r['successes'] > 0),
                  key=lambda r:(-r['success_rate'],r['station_id']))
