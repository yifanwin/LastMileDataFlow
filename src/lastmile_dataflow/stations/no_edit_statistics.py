"""Measured rates and spatial selection; unknown is never converted to failure."""
from collections import Counter
import numpy as np



def is_episode_timeout(row):
    # Legacy timeout evidence is read-only: classify it without rewriting records.
    return row.get('status') == 'timeout' or (row.get('status') == 'incomplete'
            and row.get('reason') == 'attempt_wall_clock_budget')


def is_episode_terminal(row):
    return row.get('status') in ('success', 'failure', 'planning_no_solution', 'initialization_error', 'episode_error', 'recording_error', 'simulation_failure') or is_episode_timeout(row)


def completed_episode_rows(rows):
    # Resume/retry records for the same trial must not inflate the denominator.
    completed = {}
    for index, row in enumerate(rows):
        if is_episode_terminal(row):
            key = ('trial', row['trial']) if 'trial' in row else ('row', index)
            completed[key] = row
    return list(completed.values())

def aggregate(stations, trials, expected):
    result = []
    for station in stations:
        rows = [r for r in trials if r['station_id'] == station['station_id']]
        completed = completed_episode_rows(rows)
        terminal = [r for r in completed if r['status'] in ('success','failure','planning_no_solution') or is_episode_timeout(r)]
        successes = sum(r['status'] == 'success' for r in terminal)
        reasons = Counter(('EpisodeTimeout' if is_episode_timeout(r) else r.get('attribution',{}).get('category','Unknown'))
                          for r in completed if r['status'] != 'success')
        result.append({**station, 'attempts': [r['attempt'] for r in rows],
                       'terminal_trials': len(terminal), 'successes': successes,
                       'completed_episodes':len(completed), 'episode_errors':len(completed)-len(terminal),
                       'timeout_trials':sum(is_episode_timeout(r) for r in terminal),
                       'success_rate_scope':'budgeted_evaluable_episodes_including_timeouts_excluding_episode_errors',
                       'simulation_failures':sum(r['status']=='simulation_failure' for r in rows),
                       'success_rate': successes/len(terminal) if terminal else None,
                       'complete': station['geometry'] == 'valid' and len(completed) == expected,
                       'failure_reasons': dict(reasons)})
    return result


def construction_rate(rows, expected, threshold):
    valid = [r for r in rows if r['geometry'] == 'valid']
    if not valid:
        return {'status': 'no_valid_stations', 'success_rate': None}
    if not all(r['complete'] and r.get('completed_episodes',r['terminal_trials']) == expected for r in valid):
        return {'status': 'incomplete', 'success_rate': None}
    successes = sum(r['successes'] for r in valid); denominator = sum(r['terminal_trials'] for r in valid)
    errors = sum(r.get('episode_errors',0) for r in valid)
    if errors:
        return {'status':'completed_with_episode_errors','success_rate':successes/denominator if denominator else None,
                'success_rate_scope':'evaluable_episodes_only_excluding_episode_errors',
                'successes':successes,'terminal_trials':denominator,'completed_episodes':expected*len(valid),
                'episode_errors':errors,'timeout_trials':sum(r.get('timeout_trials',0) for r in valid)}
    return {'status': 'discarded_low_success' if successes/denominator < threshold else 'eligible',
            'success_rate': successes/denominator, 'successes': successes,
            'terminal_trials': denominator, 'timeout_trials':sum(r.get('timeout_trials',0) for r in valid),
            'success_rate_scope':'budgeted_evaluable_episodes_including_timeouts_excluding_episode_errors', 'threshold': threshold}


def select_starts(rows, count, reachable=None):
    candidates = [r for r in rows if r['complete'] and r['success_rate'] is not None and r['success_rate'] < 1
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
