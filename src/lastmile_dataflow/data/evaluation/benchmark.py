"""Measured starting-state selection and full-plan benchmark summaries."""
from dataclasses import dataclass
import math
from ...config import read_config
from collections import Counter
from types import SimpleNamespace

import numpy as np

from .selection import station_calibration


@dataclass(frozen=True)
class BenchmarkConfig:
    low: float = .20
    high: float = .60
    region_radius_m: float = .10
    region_yaw_tolerance_rad: float = math.pi / 12
    map_resolution_m: float = .05
    navigation_margin_m: float = .02
    navigation_clearance_weight: float = 2.
    navigation_max_penetration_m: float = .005
    navigation_whole_body: bool = True
    local_candidate_radius_m: float | None = None
    max_episodes_per_scene: int = 0
    effective_translation_m: float = .02
    effective_yaw_rad: float = math.pi / 90
    seed: int = 42
    horizon: int = 400
    wall_timeout_s: float = 600.

    def __post_init__(self):
        if not 0 <= self.low < self.high <= 1:
            raise ValueError('benchmark requires 0 <= low < high <= 1')
        for key in ('region_radius_m', 'region_yaw_tolerance_rad', 'map_resolution_m',
                    'effective_translation_m', 'effective_yaw_rad', 'wall_timeout_s'):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError('invalid benchmark parameter: ' + key)
        if not 0 < self.region_yaw_tolerance_rad <= math.pi or self.navigation_margin_m < 0:
            raise ValueError('invalid region yaw tolerance or navigation margin')
        for key in ('navigation_margin_m', 'navigation_clearance_weight', 'navigation_max_penetration_m'):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) < 0:
                raise ValueError('invalid ' + key)
        if self.local_candidate_radius_m is not None and self.local_candidate_radius_m <= 0:
            raise ValueError('local candidate radius must be positive or null')
        if self.max_episodes_per_scene < 0 or self.seed < 0 or self.horizon <= 0:
            raise ValueError('invalid episode count, seed or horizon')


def load_benchmark_config(path):
    return BenchmarkConfig(**read_config(path))


def reference_calibration(stations, trials, expected, binding, backend):
    for row in trials:
        if any(row.get(key) != value for key, value in binding.items()):
            raise ValueError('calibration trial binding mismatch')
    value = station_calibration(stations, trials, expected, binding,
                                SimpleNamespace(operation_backend=backend, mode_config=None))
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


def _counts(episodes, results, *, strict=False):
    expected = {e['episode_id'] for e in episodes}
    relevant = [r for r in results if r['episode_id'] in expected]
    counts = Counter(r['status'] for r in relevant)
    if strict:
        from .selection import mode_trial_evaluable
        valid_counts = Counter(r['status'] for r in relevant if mode_trial_evaluable(r))
    else:
        valid_counts = counts
    valid = sum(valid_counts[s] for s in ('success', 'failure', 'timeout'))
    unknown = len(episodes) - valid
    result = {'scheduled': len(episodes), 'reported': len(relevant), 'valid_task_outcomes': valid,
            'successes': valid_counts['success'], 'failures': valid_counts['failure'], 'timeouts': valid_counts['timeout'],
            'unknown_or_infra': unknown, 'not_executed': len(episodes) - len(relevant) + counts['unsupported_policy'],
            'status_counts': dict(counts),
            'coverage': valid / len(episodes) if episodes else 0.,
            'task_success_rate': valid_counts['success'] / len(episodes) if episodes and not unknown else None,
            'observed_task_success_rate': valid_counts['success'] / valid if valid else None,
            'complete': bool(episodes) and not unknown}
    if strict:
        result.update(recording_complete=sum(r.get('recording_complete') is True for r in relevant),
            execution_termination_counts=dict(Counter((r.get('execution_termination') or {}).get('status', 'unknown')
                                                     for r in relevant)),
            execution_phase_counts=dict(Counter((r.get('execution_termination') or {}).get('phase', 'unknown')
                                               for r in relevant)))
    return result


def summarize(episodes, results, groups=None):
    expected = {e['episode_id'] for e in episodes}
    ids = [r['episode_id'] for r in results]
    if len(expected) != len(episodes) or len(ids) != len(set(ids)) or set(ids) - expected:
        raise ValueError('duplicate or foreign benchmark episode/result')
    groups = groups or {}
    mode_run = any('labels' in groups.get(e['episode_id'], {}) for e in episodes)
    summary = _counts(episodes, results, strict=mode_run)
    summary['denominator_rule'] = ('full scheduled set; unknown/infra suppress official TSR; timeout requires a known failure verdict'
                                 if mode_run else 'full scheduled set; infra/unknown suppress official TSR; timeout is task failure')
    for field in (('label', 'proposal_intent', 'task', 'failure_family') if mode_run else ('case', 'task', 'failure_family')):
        labels = {}
        for episode in episodes:
            label = groups.get(episode['episode_id'], {}).get(field)
            if label is None:
                label = episode.get('task', {}).get('operation', 'unknown') if field == 'task' else 'unknown'
            values = groups.get(episode['episode_id'], {}).get('labels', []) if field == 'label' else [label]
            for value in sorted(set(values)):
                labels.setdefault(value, []).append(episode)
        table = {label: _counts(subset, results, strict=mode_run) for label, subset in sorted(labels.items())}
        summary['by_' + field] = table
        values = [row['task_success_rate'] for row in table.values()]
        summary['macro_by_' + field] = float(np.mean(values)) if values and all(v is not None for v in values) else None
    recovery = [r['recovery_success'] for r in results if isinstance(r.get('recovery_success'), bool)]
    summary['recovery'] = {'valid': len(recovery), 'successes': sum(recovery),
                           'coverage': len(recovery) / len(episodes) if episodes else 0.,
                           'success_rate': sum(recovery) / len(episodes) if episodes and len(recovery) == len(episodes) else None,
                           'observed_success_rate': sum(recovery) / len(recovery) if recovery else None}
    summary['primary_macro'] = 'macro_by_label' if mode_run else 'macro_by_case'
    if mode_run:
        summary['label_denominator_rule'] = 'unique episodes overall; label groups overlap and must not be summed'
    for subset, selected in [('all', results), ('task_success', [r for r in results if r['status'] == 'success'])]:
        metrics = {}
        for key in ('base_path_length_m', 'final_displacement_m', 'max_displacement_m', 'base_yaw_travel_rad'):
            values = [r[key] for r in selected if r.get(key) is not None]
            metrics[key] = {'n': len(values), 'mean': float(np.mean(values)) if values else None,
                            'min': min(values) if values else None, 'max': max(values) if values else None}
        summary['base_motion_' + subset] = metrics
    return summary
