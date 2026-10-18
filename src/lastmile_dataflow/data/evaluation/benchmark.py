"""Measured starting-state selection and full-plan benchmark summaries."""
from collections import Counter
from types import SimpleNamespace

import numpy as np

from .selection import station_calibration


def reference_calibration(stations, trials, expected, binding, backend):
    for row in trials:
        if any(row.get(key) != value for key, value in binding.items()):
            raise ValueError('calibration trial binding mismatch')
    value = station_calibration(stations, trials, expected, binding, SimpleNamespace(operation_backend=backend))
    for row in value['stations']:
        row['complete'] = row['trials'] == expected
    return value


def select_starts(stations, calibration, binding, *, low=.2, high=.6):
    if calibration is None:
        raise ValueError('benchmark requires measured calibration')
    for key, value in binding.items():
        if calibration.get(key) != value:
            raise ValueError('calibration binding mismatch: ' + key)
    valid = {s['station_id']: s for s in stations if s['geometry'] == 'valid'}
    if len(valid) != sum(s['geometry'] == 'valid' for s in stations):
        raise ValueError('duplicate station identity')
    counts = calibration['stations']
    if len({r['station_id'] for r in counts}) != len(counts):
        raise ValueError('duplicate calibration station')
    observed = {}
    for row in counts:
        n, k = row['trials'], row['successes']
        if n < 0 or not 0 <= k <= n:
            raise ValueError('invalid calibration counts')
        if row['station_id'] in valid and n and row.get('complete', True):
            observed[row['station_id']] = {**row, 'success_rate': k / n}
    high_ids = sorted(sid for sid, row in observed.items() if row['success_rate'] >= high)
    if not high_ids:
        return []
    return [(valid[sid], {**observed[sid], 'high_station_ids': high_ids})
            for sid in sorted(observed) if observed[sid]['success_rate'] <= low]


def failure_family(trials):
    reasons = Counter(row.get('reason', 'unknown') for row in trials if row['status'] != 'success')
    categories = set()
    for row in trials:
        if row['status'] == 'success':
            continue
        reason = row.get('reason', '').lower()
        if row['status'] == 'planning_no_solution' or any(term in reason for term in ('planning', 'ik_', 'no_grasp_candidates')):
            categories.add('planning_limited')
        elif row['status'] == 'failure' and reason:
            categories.add('execution_limited')
        else:
            categories.add('unknown')
    family = next(iter(categories)) if len(categories) == 1 else 'mixed' if categories else 'unknown'
    return {'failure_family': family, 'failure_reasons': dict(reasons)}


def _counts(episodes, results):
    expected = {e['episode_id'] for e in episodes}
    relevant = [r for r in results if r['episode_id'] in expected]
    counts = Counter(r['status'] for r in relevant)
    valid = sum(counts[s] for s in ('success', 'failure', 'timeout'))
    unknown = len(episodes) - valid
    return {'scheduled': len(episodes), 'reported': len(relevant), 'valid_task_outcomes': valid,
            'successes': counts['success'], 'failures': counts['failure'], 'timeouts': counts['timeout'],
            'unknown_or_infra': unknown, 'not_executed': len(episodes) - len(relevant) + counts['unsupported_policy'],
            'status_counts': dict(counts),
            'coverage': valid / len(episodes) if episodes else 0.,
            'task_success_rate': counts['success'] / len(episodes) if episodes and not unknown else None,
            'observed_task_success_rate': counts['success'] / valid if valid else None,
            'complete': bool(episodes) and not unknown}


def summarize(episodes, results, groups=None):
    expected = {e['episode_id'] for e in episodes}
    ids = [r['episode_id'] for r in results]
    if len(expected) != len(episodes) or len(ids) != len(set(ids)) or set(ids) - expected:
        raise ValueError('duplicate or foreign benchmark episode/result')
    summary = _counts(episodes, results)
    summary['denominator_rule'] = 'full scheduled set; infra/unknown suppress official TSR; timeout is task failure'
    groups = groups or {}
    for field in ('case', 'task', 'failure_family'):
        labels = {}
        for episode in episodes:
            label = groups.get(episode['episode_id'], {}).get(field)
            if label is None:
                label = episode.get('task', {}).get('operation', 'unknown') if field == 'task' else 'unknown'
            labels.setdefault(label, []).append(episode)
        table = {label: _counts(subset, results) for label, subset in sorted(labels.items())}
        summary['by_' + field] = table
        values = [row['task_success_rate'] for row in table.values()]
        summary['macro_by_' + field] = float(np.mean(values)) if values and all(v is not None for v in values) else None
    recovery = [r['recovery_success'] for r in results if isinstance(r.get('recovery_success'), bool)]
    summary['recovery'] = {'valid': len(recovery), 'successes': sum(recovery),
                           'coverage': len(recovery) / len(episodes) if episodes else 0.,
                           'success_rate': sum(recovery) / len(episodes) if episodes and len(recovery) == len(episodes) else None,
                           'observed_success_rate': sum(recovery) / len(recovery) if recovery else None}
    summary['primary_macro'] = 'macro_by_case'
    for subset, selected in [('all', results), ('task_success', [r for r in results if r['status'] == 'success'])]:
        metrics = {}
        for key in ('base_path_length_m', 'final_displacement_m', 'max_displacement_m', 'base_yaw_travel_rad'):
            values = [r[key] for r in selected if r.get(key) is not None]
            metrics[key] = {'n': len(values), 'mean': float(np.mean(values)) if values else None,
                            'min': min(values) if values else None, 'max': max(values) if values else None}
        summary['base_motion_' + subset] = metrics
    return summary
