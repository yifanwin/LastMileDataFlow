"""Measured rates and spatial selection; unknown is never converted to failure."""
from collections import Counter
import numpy as np



def is_episode_timeout(row):
    return row.get('status') == 'timeout'


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
        reasons = Counter(('EpisodeTimeout' if is_episode_timeout(r) else r.get('reason', 'unknown'))
                          for r in completed if r['status'] != 'success')
        result.append({**station, 'attempts': [r.get('rollout_id') for r in rows],
                       'terminal_trials': len(terminal), 'successes': successes,
                       'completed_episodes':len(completed), 'episode_errors':len(completed)-len(terminal),
                       'timeout_trials':sum(is_episode_timeout(r) for r in terminal),
                       'success_rate_scope':'budgeted_evaluable_episodes_including_timeouts_excluding_episode_errors',
                       'simulation_failures':sum(r['status']=='simulation_failure' for r in rows),
                       'success_rate': successes/len(terminal) if terminal else None,
                       'complete': station['geometry'] == 'valid' and len(terminal) == expected,
                       'failure_reasons': dict(reasons)})
    return result
