"""Eight evidence-backed labels from one complete raw calibration, without rollouts."""
import numpy as np

from .selection import mode_station_pools, mode_trial_evaluable, select_mode_goal
from ...scenes.mode_config import MODE_NAMES


def verdict(status, reason, **metrics):
    return {'status': status, 'reason': reason, 'metrics': metrics}


def measured(condition, reason, **metrics):
    return verdict('matched' if condition else 'unmatched', reason, **metrics)


def phase_support(trials, planning, mode):
    failed = [r for r in trials if mode_trial_evaluable(r) and r['status'] != 'success']
    supported, unknown = [], []
    for trial in failed:
        evidence = planning.get(trial['rollout_id'], {})
        attempts = evidence.get('attempts', [])
        if not attempts:
            unknown.append(trial['rollout_id'])
            continue
        known = True
        hit = False
        for attempt in attempts:
            phases = {r['phase']: r for r in attempt['phase_evidence'] if r['input_eligible']}
            goalset = phases.get('goalset')
            if goalset is None:
                known = False
                continue
            if mode == 'arm_path_collision':
                hit |= (goalset['status'] == 'failure' and goalset['ik_found'] is True
                        and goalset['failure_component'] == 'trajopt')
                known &= goalset['failure_component'] != 'unknown'
            elif goalset['status'] == 'success':
                later = [phases[p] for p in ('pregrasp', 'grasp') if p in phases]
                hit |= any(r['status'] == 'failure' and r['failure_component'] in ('ik', 'trajopt') for r in later)
                known &= bool(later) and all(r['failure_component'] != 'unknown' for r in later)
        if hit:
            supported.append(trial['rollout_id'])
        elif not known:
            unknown.append(trial['rollout_id'])
    return {'supporting_trial_ids': supported, 'supporting_failures': len(supported),
            'failed_trials': len(failed), 'unresolved_trial_ids': unknown}


def label_relation(mode, scene, start, goal, measurements, trials, planning, probes, path, settings):
    """A known violated condition is unmatched; absent required evidence is unresolved."""
    if mode == 'articulated_interaction' and scene['task']['operation'] != 'open':
        return verdict('not_applicable', 'requires_open_task')
    if mode != 'visibility' and start['successes'] > 1:
        return verdict('unmatched', 'not_low_operation_rate')
    a, b = measurements.get(start['station_id'], {}), measurements.get(goal['station_id'], {})
    if mode in ('arm_path_collision', 'approach'):
        support = phase_support(trials[start['station_id']], planning, mode)
        if not support['supporting_failures']:
            return verdict('unresolved' if support['unresolved_trial_ids'] else 'unmatched',
                           'missing_phase_evidence' if support['unresolved_trial_ids'] else 'no_supporting_phase_failure', **support)
        return verdict('matched', 'measured_failed_trial_supports_phase_rule', **support)
    if mode == 'articulated_interaction':
        failures = [r['rollout_id'] for r in trials[start['station_id']]
                    if mode_trial_evaluable(r) and r['status'] != 'success']
        successes = [r for r in trials[goal['station_id']] if r['status'] == 'success']
        if not all(r['task_outcome'].get('reason') == 'physical_open' for r in successes):
            return verdict('unresolved', 'missing_complete_open_verdict')
        return measured(bool(failures), 'open_failure_and_full_joint_task_success', supporting_trial_ids=failures)
    if a.get('status') != 'measured' or b.get('status') != 'measured':
        return verdict('unresolved', 'missing_prepared_station_measurement')
    if mode == 'reachability':
        delta = a['target_distance_m'] - b['target_distance_m']
        lateral = abs(a['base_in_target_frame'][1] - b['base_in_target_frame'][1])
        if delta < settings.reach_improvement_m and lateral < settings.lateral_improvement_m:
            return verdict('unmatched', 'insufficient_target_relative_change', distance_gain_m=delta, lateral_change_m=lateral)
        probe = probes.get(start['station_id'], {})
        if probe.get('status') != 'measured' or probe.get('feasible_ratio') is None:
            return verdict('unresolved', probe.get('reason', 'missing_goalset_ik_denominator'))
        return measured(probe['feasible_ratio'] <= settings.ik_feasible_ratio_max, 'diagnostic_ik_and_target_relative_change',
                        ik_feasible_ratio=probe['feasible_ratio'], distance_gain_m=delta, lateral_change_m=lateral,
                        probe_ref=probe.get('query_ref'))
    if mode == 'ground_obstacle':
        navigation = path(start, goal)
        if navigation['status'] != 'measured':
            return verdict('unresolved', navigation['reason'])
        return {**measured(not navigation['segment_free'] and navigation['path_exists'],
                           'blocked_straight_line_and_reachable_s1', segment_free=navigation['segment_free'],
                           path_exists=navigation['path_exists']),
                'path_length_m': navigation['path_length_m']}
    if mode == 'narrow_space':
        clearance, gain = a['clearance_base_m'], b['clearance_base_m'] - a['clearance_base_m']
        return measured(clearance < settings.clearance_low_m and gain > settings.clearance_gain_m,
                        'initial_chassis_clearance_contrast', clearance_base_m=clearance, clearance_gain_m=gain)
    if mode == 'visibility':
        low, high = a['visibility'], b['visibility']
        if not low.get('complete') or low.get('value') is None:
            return verdict('unresolved', 's0_visibility_incomplete')
        if low['value'] >= settings.visibility_low:
            return verdict('unmatched', 's0_not_low_visibility')
        if high.get('known_max') is None:
            return verdict('unresolved', 's1_visibility_unknown')
        gain = high['known_max'] - low['value']
        if high['known_max'] <= settings.visibility_good or gain <= settings.visibility_gain:
            return verdict('unmatched', 'insufficient_visibility_improvement', visibility_gain=gain)
        navigation = path(start, goal)
        if navigation['status'] != 'measured':
            return verdict('unresolved', navigation['reason'])
        return {**measured(navigation['path_exists'], 'low_view_and_reachable_high_rate_better_view',
                           s0_visibility=low['value'], s1_visibility=high['known_max'], visibility_gain=gain),
                'path_length_m': navigation['path_length_m']}
    # Orientation uses requested non-base settings and measured prepared visibility.
    if start['xy_id'] != goal['xy_id'] or a['non_base_initial'] != b['non_base_initial']:
        return verdict('unmatched', 'not_same_xy_and_non_base_initialization')
    yaw_delta = abs(float(np.arctan2(np.sin(a['yaw'] - b['yaw']), np.cos(a['yaw'] - b['yaw']))))
    if yaw_delta <= 1e-6:
        return verdict('unmatched', 'same_yaw')
    if a['visibility'].get('known_max') is None or b['visibility'].get('known_max') is None:
        return verdict('unresolved', 'yaw_visibility_unknown')
    visible = min(a['visibility']['known_max'], b['visibility']['known_max']) > 0
    return measured(visible and a['initialization_valid'] and b['initialization_valid'],
                    'visible_same_xy_yaw_contrast', yaw_delta_rad=yaw_delta)


def diagnose_task(scene, calibration, trials, measurements, planning, probes, path, settings):
    """One result per variant; every candidate S0 is checked against all eight labels."""
    result = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id'],
        'proposal_intent': scene['proposal_intent'], 'dataset_split': scene['dataset_split'],
        'calibration_complete': calibration['complete'], 'task_eligibility': calibration['task_eligibility'],
        'intent_hit': None if not calibration['complete'] else False, 's0_results': []}
    if calibration['task_eligibility'] != 'eligible':
        result['reason'] = calibration['task_eligibility']
        return result
    pools = mode_station_pools(scene['stations'], calibration)
    rates = {r['station_id']: r for r in calibration['stations']}
    by_station = {r['station_id']: [] for r in scene['stations']}
    for trial in trials:
        by_station[trial['station_id']].append(trial)
    starts = {r['station_id']: r for r in pools['starts']}
    # Unknown views may still qualify for Visibility; they must not become false intent misses.
    for station in scene['stations']:
        view = measurements.get(station['station_id'], {}).get('visibility', {})
        if not view.get('complete') or view.get('value') is None or view['value'] < settings.visibility_low:
            starts.setdefault(station['station_id'], {**station, **rates[station['station_id']]})
    hints = set(scene['station_hints']) if scene['station_hints'] else set(starts)
    intended = []
    for start in sorted(starts.values(), key=lambda r: (r['successes'], r['station_id'])):
        labels = {}
        for mode in MODE_NAMES:
            if mode == 'articulated_interaction' and scene['task']['operation'] != 'open':
                labels[mode] = verdict('not_applicable', 'requires_open_task')
                continue
            relation = lambda goal: label_relation(mode, scene, start, goal, measurements, by_station,
                                                   planning, probes, path, settings)
            selected = select_mode_goal(pools['goals'], relation)
            labels[mode] = {**selected, 's1': selected['evidence_s1'], 's1_successes': selected.get('successes')}
            if selected['evidence_s1'] is not None:
                chosen = next(r for r in selected['checks'] if r['station_id'] == selected['evidence_s1'])
                labels[mode]['metrics'] = chosen['metrics']
                labels[mode]['s1_trial_ids'] = [r['rollout_id'] for r in by_station[selected['evidence_s1']]]
        final = [mode for mode in MODE_NAMES if labels[mode]['status'] == 'matched']
        expected = [mode for mode in final if mode == scene['proposal_intent']]
        is_hint = start['station_id'] in hints
        result['s0_results'].append({'s0': start['station_id'], 'successes': start['successes'], 'trials': 3,
            'final_labels': final, 'expected_labels': expected, 'additional_labels': [mode for mode in final if mode not in expected],
            'additional_discovery': expected if not is_hint else [],
            'is_intended_start': is_hint, 'labels': labels,
            's0_trial_ids': [r['rollout_id'] for r in by_station[start['station_id']]]})
        if is_hint:
            intended.append(labels[scene['proposal_intent']]['status'])
    result['intent_hit'] = (True if 'matched' in intended else None if 'unresolved' in intended else False)
    result['recovery_eligible'] = any(r['final_labels'] for r in result['s0_results'])
    return result


def load_diagnoses(calibration_root, scenes, mode_settings):
    """Consume saved classification under its original thresholds; never relabel downstream."""
    from pathlib import Path
    from ...utils.io import read_json, rows
    root = Path(calibration_root)
    saved = read_json(root / 'config.json')
    if saved['mode_settings'] != mode_settings:
        raise ValueError('diagnosis threshold configuration mismatch')
    path = root / 'diagnosis.jsonl'
    if not path.is_file():
        raise ValueError('task-mode downstream requires evaluate-stations diagnosis.jsonl')
    records = rows(path)
    result = {row['scene_id']: row for row in records}
    if len(result) != len(records):
        raise ValueError('duplicate task-mode diagnosis')
    for scene in scenes:
        record = result[scene['scene_id']]
        if (record['task_id'] != scene['task']['task_id'] or record['dataset_split'] != scene['dataset_split']
                or record['proposal_intent'] != scene['proposal_intent']):
            raise ValueError('diagnosis candidate binding mismatch')
    return {scene['scene_id']: result[scene['scene_id']] for scene in scenes}


def diagnosed_pairs(scene, diagnosis):
    """Union the stored per-label qualified S1 sets, with one pair per S0/S1."""
    if not diagnosis['calibration_complete'] or diagnosis['task_eligibility'] != 'eligible':
        return []
    pairs = {}
    ids = {row['station_id'] for row in scene['stations']}
    for start in diagnosis['s0_results']:
        for label in start['final_labels']:
            evidence = start['labels'][label]
            if evidence['status'] != 'matched':
                raise ValueError('final label lacks matched evidence')
            for goal in evidence['qualifying_s1_ids']:
                if start['s0'] not in ids or goal not in ids:
                    raise ValueError('diagnosed station outside source scene')
                pair = pairs.setdefault((start['s0'], goal), {'s0': start['s0'], 's1': goal,
                    's0_success_rate': start['successes'] / 3, 's1_success_rate': evidence['s1_successes'] / 3,
                    'labels': [], 's1_tiers': {}})
                pair['labels'].append(label)
                pair['s1_tiers'][label] = evidence['s1_successes']
    return sorted(pairs.values(), key=lambda row: (row['s0_success_rate'], row['s0'], row['s1']))


def diagnosed_starts(scene, diagnosis):
    """One episode per S0, with its own union of qualified recovery anchors."""
    pairs = diagnosed_pairs(scene, diagnosis)
    grouped = {}
    for pair in pairs:
        grouped.setdefault(pair['s0'], set()).add(pair['s1'])
    points = {r['station_id']: r for r in scene['stations']}
    return [(points[start['s0']], {'successes': start['successes'], 'trials': 3,
        'success_rate': start['successes'] / 3, 'high_station_ids': sorted(grouped[start['s0']]),
        'labels': list(start['final_labels']), 's1_by_label': {
            label: list(start['labels'][label]['qualifying_s1_ids']) for label in start['final_labels']}})
        for start in diagnosis['s0_results'] if start['s0'] in grouped]
