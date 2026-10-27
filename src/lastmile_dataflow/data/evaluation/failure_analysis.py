"""Read-only failure and target statistics for one or more calibration runs."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import numpy as np

from ...utils.io import read_json, rows
from .selection import mode_calibration, mode_trial_evaluable


IK_TOLERANCE = {'position_m': 0.005, 'rotation_rad': 0.05}
PHASE_LABELS = {'goalset': 'goalset', 'pregrasp': 'approach', 'grasp': 'grasp', 'lift': 'lift'}


def _load_jsonl(path):
    return list(rows(Path(path))) if Path(path).is_file() else []


def _resolve(root, value):
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def _load_planning(trial, run_root):
    path = _resolve(run_root, trial.get('planning_ref'))
    if path is None or not path.is_file():
        return []
    value = json.loads(path.read_text())
    entries = value if isinstance(value, list) else [value]
    normalized = []
    for entry in entries:
        diagnostics = entry.get('diagnostics') or {}
        normalized.append({**diagnostics, **entry, 'diagnostics': diagnostics})
    return normalized


def _load_solver(entry, run_root):
    path = _resolve(run_root, entry.get('solver_trace_ref'))
    if path is None or not path.is_file():
        query = entry.get('query_path')
        path = _resolve(run_root, Path(query) / 'solver_trace.json') if query else None
    if path is None or not path.is_file():
        return []
    value = json.loads(path.read_text())
    return value if isinstance(value, list) else []


def _lane_slot(entry, trial):
    seeds = entry.get('batch_seeds') or []
    if seeds and trial.get('seed') in seeds:
        return seeds.index(trial['seed'])
    return int(entry.get('batch_slot') or 0)


def _lane_row(value, slot):
    array = np.asarray(value)
    if array.ndim == 0:
        array = array.reshape(1, 1)
    elif array.ndim == 1:
        array = array.reshape(1, -1)
    return array[slot] if 0 <= slot < array.shape[0] else array[0]


def _first_failure(entry):
    for row in entry.get('phase_evidence') or []:
        if row.get('input_eligible') and row.get('status') == 'failure':
            return row
    return None


def _goal_count(entry, query_index):
    for query in entry.get('queries') or []:
        if query.get('query_index') == query_index:
            return query.get('goal_count')
    return None


def _ik_details(solver, entry, phase_row, trial):
    phase, query_index = phase_row.get('phase'), phase_row.get('query_index')
    slot = _lane_slot(entry, trial)
    converged_any, feasible_any, observed = False, False, False
    min_position, min_rotation = None, None
    positive = set()
    links = defaultdict(float)
    for row in solver:
        if row.get('solver') != 'ik' or row.get('phase') != phase or row.get('query_index') != query_index:
            continue
        if row.get('position_error') is None or row.get('rotation_error') is None:
            continue
        observed = True
        position = _lane_row(row.get('position_error'), slot).astype(float)
        rotation = _lane_row(row.get('rotation_error'), slot).astype(float)
        feasible_value = row.get('feasible') if row.get('feasible') is not None else row.get('success')
        feasible = _lane_row(feasible_value, slot).astype(bool)
        converged = (position < IK_TOLERANCE['position_m']) & (rotation < IK_TOLERANCE['rotation_rad'])
        converged_any = converged_any or bool(converged.any())
        feasible_any = feasible_any or bool((converged & feasible).any())
        min_position = float(position.min()) if min_position is None else min(min_position, float(position.min()))
        candidate = float(rotation[converged].min()) if converged.any() else float(rotation.min())
        min_rotation = candidate if min_rotation is None else min(min_rotation, candidate)
        for name, terms in (row.get('constraint_terms') or {}).items():
            if int(terms.get('positive_count', 0)) > 0:
                positive.add(name)
            for link, costs in (terms.get('selected_link_costs') or {}).items():
                value = max(costs) if costs else 0.
                if value > 0:
                    links[link] = max(links[link], float(value))
    return {'ik_observed': observed, 'ik_unreachable': observed and not converged_any,
            'ik_constraint_infeasible': observed and converged_any and not feasible_any,
            'ik_feasible': feasible_any, 'min_position_error_m': min_position,
            'min_rotation_error_rad': min_rotation, 'positive_constraints': sorted(positive),
            'constraint_links': dict(sorted(links.items(), key=lambda item: -item[1])[:8]),
            'lane_slot': slot}


def _planning_failure(trial, run_root):
    """Return the first failed planning phase and its component, or None."""
    for entry in _load_planning(trial, run_root):
        phase_row = _first_failure(entry)
        if phase_row is None:
            continue
        solver = _load_solver(entry, run_root)
        component = phase_row.get('failure_component') or 'unknown'
        details = _ik_details(solver, entry, phase_row, trial)
        if component == 'ik' and details['ik_observed'] and details['ik_constraint_infeasible']:
            component = 'ik_constraint'
        elif component == 'ik' and details['ik_observed'] and details['ik_unreachable']:
            component = 'ik_unreachable'
        elif component == 'trajopt' and details['ik_observed'] and details['ik_unreachable']:
            component = 'ik_unreachable'
        elif component == 'trajopt' and details['ik_observed'] and details['ik_constraint_infeasible']:
            component = 'ik_constraint'
        phase = PHASE_LABELS.get(phase_row.get('phase'), phase_row.get('phase'))
        if component == 'ik_unreachable':
            label = f'IK ({phase}) 目标位姿不可达'
        elif component == 'ik_constraint':
            label = f'IK ({phase}) 位姿收敛但约束不可行'
        elif component == 'trajopt':
            label = f'TrajOpt ({phase}) IK 有解、轨迹不可行'
        elif component == 'fov':
            label = f'FOV ({phase}) 约束失败'
        else:
            label = f'规划未知 ({phase})'
        index = entry.get('index', entry.get('grasp_index'))
        return {'row': label, 'stage': 'planning', 'phase': phase_row.get('phase'),
                'component': component, 'query_index': phase_row.get('query_index'),
                'solve_index': None, 'grasp_index': index, 'goal_count': _goal_count(entry, phase_row.get('query_index')),
                'arm': entry.get('side') or entry.get('arm'), 'seed': entry.get('seed', trial.get('seed')),
                'failure_reason': entry.get('failure_reason'), **details}
    return None


def _control_evidence(trial, run_root):
    path = _resolve(run_root, Path('rollouts') / str(trial.get('rollout_id', '')) / 'task_evidence_initial.json')
    if path is None or not path.is_file():
        return {}
    value = json.loads(path.read_text())
    return value.get('control') or {}


def _execution_failure(trial, run_root, planning=None):
    outcome = trial.get('task_outcome') or {}
    termination = trial.get('execution_termination') or {}
    attribution = trial.get('attribution') or {}
    control = _control_evidence(trial, run_root)
    reason = trial.get('reason') or outcome.get('reason') or 'unknown'
    phase = termination.get('phase') or outcome.get('first_failure_phase') or 'unknown'
    retries = trial.get('retry_history') or []
    navigation = trial.get('navigation') or {}
    return {'row': reason, 'stage': 'execution', 'phase': phase, 'component': 'execution',
            'status': trial.get('status'), 'reason': reason,
            'attribution_category': attribution.get('category'), 'attribution_reason': attribution.get('reason'),
            'task_outcome_reason': outcome.get('reason'), 'first_failure_phase': outcome.get('first_failure_phase'),
            'first_failure_time_s': outcome.get('first_failure_time_s'),
            'arm': control.get('arm') or (planning or {}).get('arm'),
            'grasp_row': control.get('grasp_row'), 'grasp_source': control.get('grasp_source'),
            'seed': trial.get('seed'), 'executed_steps': trial.get('executed_steps'),
            'retry_count': len(retries), 'retry_reasons': [row.get('reason') for row in retries],
            'navigation_status': navigation.get('status'), 'navigation_reason': navigation.get('reason'),
            'recording_complete': trial.get('recording_complete'),
            'collisions': attribution.get('collisions') or []}


def _classify_trial(trial, run_root):
    if not mode_trial_evaluable(trial):
        return {'row': '基础设施/未知', 'stage': 'infrastructure', 'status': trial.get('status'),
                'reason': trial.get('reason')}
    if trial.get('status') == 'success':
        return {'row': 'success', 'stage': 'success', 'status': 'success', 'reason': trial.get('reason')}
    planning = _planning_failure(trial, run_root)
    if trial.get('status') == 'planning_no_solution' and not trial.get('executed_steps'):
        return planning or {'row': '规划未知', 'stage': 'planning', 'status': trial.get('status')}
    if trial.get('status') in ('planning_no_solution', 'failure') and trial.get('reason') == 'planning_no_solution':
        if planning:
            label = '执行 抓住物体后 lift 规划无解' if planning['phase'] in ('lift', 'lift_actual', 'hold') \
                else f"执行 中途规划无解 ({planning['phase']})"
            return {**planning, 'row': label, 'stage': 'execution',
                    'reason': planning.get('failure_reason') or 'planning_no_solution'}
    return _execution_failure(trial, run_root, planning)


def _task_rates(scene, scene_trials):
    return mode_calibration(scene['stations'], scene_trials,
                            {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}, 'curobo')


def _s0_s1(rates):
    rows = [row for row in rates['stations'] if row.get('trials') == 3]
    s0 = [row['station_id'] for row in rows if row['successes'] == 0]
    if not s0:
        s0 = [row['station_id'] for row in rows if row['successes'] == 1]
    s1 = [row['station_id'] for row in rows if row['successes'] == 3]
    if not s1:
        s1 = [row['station_id'] for row in rows if row['successes'] == 2]
    return sorted(s0), sorted(s1)


def _scene_summary(scene, scene_trials):
    rates = _task_rates(scene, scene_trials)
    s0, s1 = _s0_s1(rates)
    total_trials = sum(row['trials'] for row in rates['stations'])
    successes = sum(row['successes'] for row in rates['stations'])
    complete = rates['complete']
    all_success = complete and total_trials > 0 and successes == total_trials
    all_fail = complete and total_trials > 0 and successes == 0
    eligible = rates['task_eligibility'] == 'eligible'
    valid = eligible and bool(s0) and bool(s1)
    task = scene['task']
    return {'scene_id': scene['scene_id'], 'task_id': task['task_id'], 'house': scene.get('house'),
            'operation': task.get('operation'), 'category': task.get('category') or task.get('asset_id'),
            'instance_id': task.get('instance_id'), 'asset_id': task.get('asset_id'),
            'complete': complete, 'eligible': eligible, 'valid_scene': valid,
            'all_fail': all_fail, 'all_success': all_success,
            'success_no_pair': complete and successes > 0 and not valid,
            'trials': total_trials, 'successes': successes,
            'success_rate': successes / total_trials if total_trials else None,
            'stations': rates['stations'], 's0': s0, 's1': s1,
            'eligibility': rates['task_eligibility'],
            'station_lookup': {row['station_id']: row for row in scene['stations']}}


def _load_run(calibration):
    root = Path(calibration)
    config = read_json(root / 'config.json')
    dataset = Path(config['dataset'])
    scenes = [row for row in _load_jsonl(dataset / 'scenes.jsonl') if row.get('status') == 'accepted']
    trials = _load_jsonl(root / 'trials.jsonl')
    by_scene = defaultdict(list)
    for trial in trials:
        by_scene[trial['scene_id']].append(trial)
    return root, dataset, scenes, trials, by_scene


def target_stats(calibrations, *, min_trials=30, min_houses=2):
    """Lightweight cumulative category statistics; no planning evidence parsing."""
    summaries, seen = [], set()
    for calibration in calibrations:
        root, dataset, scenes, trials, by_scene = _load_run(calibration)
        for scene in scenes:
            if scene['scene_id'] in seen:
                continue
            seen.add(scene['scene_id'])
            summary = _scene_summary(scene, by_scene.get(scene['scene_id'], []))
            summary['calibration'] = str(root)
            summaries.append(summary)
    categories = defaultdict(lambda: {'tasks': 0, 'houses': set(), 'stations': 0, 'trials': 0,
                                      'successes': 0, 'eligible': 0, 'valid': 0,
                                      'all_fail': 0, 'all_success': 0, 'operations': set()})
    for summary in summaries:
        item = categories[(summary['category'], summary['operation'])]
        item['tasks'] += 1
        item['houses'].add(summary['house'])
        item['stations'] += len(summary['stations'])
        item['trials'] += summary['trials']
        item['successes'] += summary['successes']
        item['eligible'] += int(summary['eligible'])
        item['valid'] += int(summary['valid_scene'])
        item['all_fail'] += int(summary['all_fail'])
        item['all_success'] += int(summary['all_success'])
        item['operations'].add(summary['operation'])
    rows, excluded = [], set()
    for (category, operation), item in sorted(categories.items(), key=lambda pair: (str(pair[0][0]), str(pair[0][1]))):
        row = {**item, 'category': category, 'operation': operation,
               'house_ids': sorted(str(house) for house in item['houses']),
               'houses': len(item['houses']),
               'operations': sorted(item['operations']),
               'success_rate': item['successes'] / item['trials'] if item['trials'] else None}
        row.pop('houses', None)
        row['houses'] = len(item['houses'])
        rows.append(row)
        if item['trials'] >= min_trials and len(item['houses']) >= min_houses and item['successes'] == 0:
            excluded.add(category)
    return {'counts': {'houses': len({summary['house'] for summary in summaries}),
                       'tasks': len(summaries),
                       'stations': sum(len(summary['stations']) for summary in summaries),
                       'trials': sum(summary['trials'] for summary in summaries)},
            'task_classes': {'all_fail': sum(summary['all_fail'] for summary in summaries),
                             'all_success': sum(summary['all_success'] for summary in summaries),
                             'valid_scene': sum(summary['valid_scene'] for summary in summaries),
                             'success_no_pair': sum(summary['success_no_pair'] for summary in summaries),
                             'incomplete': sum(not summary['complete'] for summary in summaries)},
            'target_categories': rows, 'exclude_categories': sorted(excluded)}


def valid_scene_replays(calibrations):
    """One S0 and one S1 rollout per valid scene; no planning evidence parsing."""
    rows = []
    for calibration in calibrations:
        root, dataset, scenes, trials, by_scene = _load_run(calibration)
        trial_map = defaultdict(list)
        for trial in trials:
            trial_map[(trial['scene_id'], trial['station_id'])].append(trial)
        for scene in scenes:
            summary = _scene_summary(scene, by_scene.get(scene['scene_id'], []))
            if not summary['valid_scene']:
                continue
            for kind, station_ids in (('S0', summary['s0']), ('S1', summary['s1'])):
                for station_id in station_ids:
                    candidates = [item for item in trial_map.get((scene['scene_id'], station_id), [])
                                  if (item.get('executed_steps') or 0) > 0
                                  and item.get('recording_complete', True)]
                    if kind == 'S1':
                        trial = next((item for item in candidates if item.get('status') == 'success'),
                                     candidates[0] if candidates else None)
                    else:
                        trial = next((item for item in candidates if item.get('status') != 'success'),
                                     candidates[0] if candidates else None)
                    if trial and trial.get('rollout_id'):
                        rows.append({'calibration': str(root), 'scene_id': scene['scene_id'],
                                     'task_id': scene['task']['task_id'], 'station_id': station_id,
                                     'trial': trial.get('trial'), 'rollout_id': trial['rollout_id'],
                                     'executed_steps': trial.get('executed_steps'),
                                     'kind': kind, 'reason': summary['eligibility']})
                        break
    return rows


def analyze_runs(calibrations, output, *, min_trials=30, min_houses=2):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    all_trials, planning_rows, execution_rows = [], [], []
    summaries, seen_scenes, trial_lookup = [], {}, {}
    for calibration in calibrations:
        root, dataset, scenes, trials, by_scene = _load_run(calibration)
        all_trials.extend(trials)
        for scene in scenes:
            if scene['scene_id'] in seen_scenes:
                continue
            seen_scenes[scene['scene_id']] = calibration
            scene_trials = by_scene.get(scene['scene_id'], [])
            for trial in scene_trials:
                trial_lookup.setdefault((str(root), scene['scene_id'], trial.get('station_id')), []).append(trial)
            summary = _scene_summary(scene, scene_trials)
            summary['calibration'] = str(root)
            summaries.append(summary)
            for trial in scene_trials:
                classified = _classify_trial(trial, root)
                row = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id'],
                       'operation': scene['task'].get('operation'), 'category': summary['category'],
                       'station_id': trial.get('station_id'), 'station_index': trial.get('station_index'),
                       'trial': trial.get('trial'), 'attempt_id': trial.get('rollout_id'),
                       'complete': summary['complete'],
                       'calibration': str(root), 'planning_ref': trial.get('planning_ref'), **classified}
                if classified.get('stage') == 'planning':
                    planning_rows.append(row)
                elif classified.get('stage') == 'execution':
                    execution_rows.append(row)
    # Target category aggregation
    categories = defaultdict(lambda: {'tasks': 0, 'houses': set(), 'stations': 0, 'trials': 0,
                                      'successes': 0, 'eligible': 0, 'valid': 0,
                                      'all_fail': 0, 'all_success': 0, 'operations': set()})
    for summary in summaries:
        key = (summary['category'], summary['operation'])
        item = categories[key]
        item['tasks'] += 1
        item['houses'].add(summary['house'])
        item['stations'] += len(summary['stations'])
        item['trials'] += summary['trials']
        item['successes'] += summary['successes']
        item['eligible'] += int(summary['eligible'])
        item['valid'] += int(summary['valid_scene'])
        item['all_fail'] += int(summary['all_fail'])
        item['all_success'] += int(summary['all_success'])
        item['operations'].add(summary['operation'])
    category_rows = []
    excluded = set()
    for (category, operation), item in sorted(categories.items(), key=lambda pair: (str(pair[0][0]), str(pair[0][1]))):
        rate = item['successes'] / item['trials'] if item['trials'] else None
        category_rows.append({**item, 'category': category, 'operation': operation,
                              'houses': len(item['houses']), 'operations': sorted(item['operations']),
                              'success_rate': rate})
        if item['trials'] >= min_trials and len(item['houses']) >= min_houses and item['successes'] == 0:
            excluded.add(category)
    # Main table
    counts = Counter((row['row'], row['operation']) for row in planning_rows + execution_rows
                     if row.get('complete'))
    complete_summaries = [summary for summary in summaries if summary['complete']]
    success_counts = Counter(summary['operation'] for summary in complete_summaries
                             for _ in range(summary['successes']))
    total_success = sum(success_counts.values())
    main = defaultdict(lambda: {'pick': 0, 'open': 0, '合计': 0})
    for (label, operation), count in counts.items():
        main[label][operation] += count
        main[label]['合计'] += count
    for operation, count in success_counts.items():
        main['success'][operation] += count
        main['success']['合计'] += count
    evaluable_total = sum(summary['trials'] for summary in complete_summaries)
    table = []
    for label, row in sorted(main.items(), key=lambda pair: (-pair[1]['合计'], pair[0])):
        table.append({'row': label, **row,
                      'share': row['合计'] / evaluable_total if evaluable_total else None})
    # Detailed execution table
    execution_detail = Counter((row.get('phase'), row.get('reason'), row.get('attribution_category'))
                               for row in execution_rows)
    # Constraint terms
    constraint_counts = Counter()
    for row in planning_rows:
        if not row.get('ik_constraint_infeasible'):
            continue
        for name in row.get('positive_constraints') or []:
            constraint_counts[name] += 1
    # Representative rollouts for the later offline replay pass.
    representatives, seen_rows = [], set()
    for row in planning_rows + execution_rows:
        if not row.get('attempt_id') or not row.get('station_id'):
            continue
        if (row.get('executed_steps') or 0) <= 0:
            continue
        key = (row.get('operation'), row['row'])
        if key in seen_rows:
            continue
        seen_rows.add(key)
        representatives.append({'calibration': row['calibration'], 'scene_id': row['scene_id'],
                                'task_id': row['task_id'], 'station_id': row['station_id'],
                                'trial': row.get('trial'), 'rollout_id': row['attempt_id'],
                                'kind': 'failure', 'reason': row['row']})
    for operation in ('pick', 'open'):
        chosen = None
        for summary in summaries:
            if summary['operation'] != operation or summary['successes'] <= 0:
                continue
            for rate in summary['stations']:
                if rate.get('successes', 0) <= 0:
                    continue
                key = (summary['calibration'], summary['scene_id'], rate['station_id'])
                trial = next((item for item in trial_lookup.get(key, [])
                              if item.get('status') == 'success' and (item.get('executed_steps') or 0) > 0), None)
                if trial:
                    chosen = {'calibration': summary['calibration'], 'scene_id': summary['scene_id'],
                              'task_id': summary['task_id'], 'station_id': rate['station_id'],
                              'trial': trial.get('trial'), 'rollout_id': trial.get('rollout_id'),
                              'kind': 'success', 'reason': 'success'}
                    break
            if chosen:
                break
        if chosen:
            representatives.append(chosen)
    for summary in summaries:
        if not summary['valid_scene']:
            continue
        for kind, station_ids in (('S0', summary['s0']), ('S1', summary['s1'])):
            if not station_ids:
                continue
            station_id = station_ids[0]
            candidates = [item for item in trial_lookup.get(
                (summary['calibration'], summary['scene_id'], station_id), [])
                if (item.get('executed_steps') or 0) > 0]
            if kind == 'S1':
                trial = next((item for item in candidates if item.get('status') == 'success'),
                             candidates[0] if candidates else None)
            else:
                trial = next((item for item in candidates if item.get('status') != 'success'),
                             candidates[0] if candidates else None)
            if trial and trial.get('rollout_id'):
                representatives.append({'calibration': summary['calibration'], 'scene_id': summary['scene_id'],
                                        'task_id': summary['task_id'], 'station_id': station_id,
                                        'trial': trial.get('trial'), 'rollout_id': trial['rollout_id'],
                                        'kind': kind, 'reason': summary['eligibility']})
    result = {
        'runs': [str(Path(item).resolve()) for item in calibrations],
        'counts': {'houses': len({summary['house'] for summary in summaries}),
                   'tasks': len(summaries),
                   'stations': sum(len(summary['stations']) for summary in summaries),
                   'trials': evaluable_total},
        'task_classes': {
            'all_fail': sum(summary['all_fail'] for summary in summaries),
            'all_success': sum(summary['all_success'] for summary in summaries),
            'valid_scene': sum(summary['valid_scene'] for summary in summaries),
            'success_no_pair': sum(summary['success_no_pair'] for summary in summaries),
            'incomplete': sum(not summary['complete'] for summary in summaries)},
        'main_table': table,
        'target_categories': category_rows,
        'exclude_categories': sorted(excluded),
        'planning_failures': planning_rows,
        'execution_failures': execution_rows,
        'execution_detail': [{'phase': phase, 'reason': reason, 'attribution': attribution, 'count': count}
                             for (phase, reason, attribution), count in execution_detail.most_common()],
        'constraint_terms': dict(constraint_counts.most_common()),
        'replay_manifest': representatives,
        'tasks': summaries,
    }
    (output / 'failure_summary.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
    (output / 'planning_failures.jsonl').write_text(
        ''.join(json.dumps(row, default=str) + '\n' for row in planning_rows))
    (output / 'execution_failures.jsonl').write_text(
        ''.join(json.dumps(row, default=str) + '\n' for row in execution_rows))
    (output / 'target_success_rates.json').write_text(
        json.dumps({'categories': category_rows, 'tasks': summaries, 'exclude_categories': sorted(excluded)},
                   indent=2, default=str) + '\n')
    (output / 'replay_manifest.jsonl').write_text(
        ''.join(json.dumps(row, default=str) + '\n' for row in representatives))
    _write_markdown(output / 'failure_table.md', result)
    return result


def _pct(value):
    return f'{100 * value:.2f}%' if value is not None else '—'


def _write_markdown(path, result):
    lines = ['# 无编辑标定失败与目标成功率汇总', '',
             f"- 来源 run：{len(result['runs'])} 个",
             f"- house：{result['counts']['houses']}；task：{result['counts']['tasks']}；"
             f"station：{result['counts']['stations']}；有效 trial：{result['counts']['trials']}", '',
             '## 任务分类', '',
             '| 类别 | 数量 |', '|---|---:|']
    for key, value in result['task_classes'].items():
        lines.append(f'| {key} | {value} |')
    lines += ['', '## 目标类别成功率', '',
              '| 目标类别 | 操作 | tasks | houses | stations | trials | 成功 | 成功率 | eligible | 有效场景 | 全失败 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in result['target_categories']:
        lines.append(f"| {row['category']} | {row['operation']} | {row['tasks']} | {row['houses']} | "
                     f"{row['stations']} | {row['trials']} | {row['successes']} | {_pct(row['success_rate'])} | "
                     f"{row['eligible']} | {row['valid']} | {row['all_fail']} |")
    lines += ['', '## 后续批次排除建议', '',
              '排除条件：累计有效 trial ≥ 阈值、覆盖 ≥ 2 个 house、成功数为 0。', '',
              ', '.join(result['exclude_categories']) if result['exclude_categories'] else '无', '',
              '## trial 级失败原因主表', '',
              '| 阶段/原因 | pick | open | 合计 | 占全部 |', '|---|---:|---:|---:|---:|']
    for row in result['main_table']:
        lines.append(f"| {row['row']} | {row['pick']} | {row['open']} | {row['合计']} | {_pct(row['share'])} |")
    lines += ['', '## 执行失败细分', '',
              '| phase | reason | attribution | 数量 |', '|---|---|---|---:|']
    for row in result['execution_detail']:
        lines.append(f"| {row['phase']} | {row['reason']} | {row['attribution']} | {row['count']} |")
    lines += ['', '## IK 约束项', '',
              '| constraint | positive trials |', '|---|---:|']
    for name, count in result['constraint_terms'].items():
        lines.append(f'| {name} | {count} |')
    lines += ['', '## 目标任务成功率（前 100 行）', '',
              '| task_id | 类别 | 操作 | 成功/有效 trial | 成功率 | S0 | S1 | 资格 |', '|---|---|---|---:|---:|---:|---:|---|']
    for summary in sorted(result['tasks'], key=lambda item: (-(item['success_rate'] or 0), item['task_id']))[:100]:
        lines.append(f"| {summary['task_id']} | {summary['category']} | {summary['operation']} | "
                     f"{summary['successes']}/{summary['trials']} | {_pct(summary['success_rate'])} | "
                     f"{len(summary['s0'])} | {len(summary['s1'])} | {summary['eligibility']} |")
    lines += ['', '站级视角成功率见 `target_success_rates.json` 的 `tasks[].stations`。', '',
              '## 边界', '',
              '- 规划无解、未执行、未知 timeout 不写成任务失败。',
              '- 失败明细只来自原始 trial、planning 证据和 rollout 元数据。',
              '- `selected_link_costs` 只覆盖 top-12 解，不是完整 seed 分母。']
    Path(path).write_text('\n'.join(lines) + '\n')
