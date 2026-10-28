#!/usr/bin/env python3
"""N4 chassis-sphere A/B: one layered row per trial, read from existing runs only.

Layers are reported separately, never merged into one success flag:
  geometry  - initial-state planner verdict of the chassis spheres and the physical
              chassis-to-obstacle distance (p2_collision_diagnosis JSON of A and B);
  planning  - furthest planning stage reached and the failing phase/component;
  execution - executed steps and execution termination;
  task      - task outcome from the trial record;
  timing    - planning wall time per query and phase, trial planning/execution
              time and use of the active budget; the pre-close batch has identical
              inputs in A and B, so it is the cleanest cost comparison;
  contacts  - executed states replayed in a fresh, un-begun simulation (recorded
              qpos, mj_forward): robot contacts with anything that is neither the
              robot, the floor under the base, nor the target subtree.
Output: one JSON; a Markdown table is printed.
"""
import argparse
import json
from pathlib import Path

import mujoco
import numpy as np


def geometry_rows(path):
    if path is None:
        return {}
    rows = {}
    for s in json.loads(Path(path).read_text())['stations']:
        chassis = [p for p in s['pairs'] if p['part'] == 'chassis' and p['planner']['min_m'] is not None]
        worst = min(chassis, key=lambda p: p['planner']['min_m'], default=None)
        verdict = s['planner_world_verdict']
        rows[(s['scene'], s['station'])] = {
            'colliding_spheres': {part: sum(w['colliding_spheres'] for w in v.values()) for part, v in verdict.items()},
            'chassis_sphere_min_m': None if worst is None else worst['planner']['min_m'],
            'same_obstacle_physical_m': None if worst is None else worst['physics']['accepted_m'],
            'obstacle': None if worst is None else worst['obstacle_geom'],
            'chassis_box_max_uncovered_m': s.get('chassis_box_max_uncovered_m')}
    return rows


def planning_row(run, trial):
    """Last recorded planning stage of the trial and its failing phase evidence."""
    stages = []
    if trial['planning_ref']:
        for entry in json.loads((run / trial['planning_ref']).read_text()):
            evidence = entry.get('phase_evidence') or entry.get('diagnostics', {}).get('phase_evidence') or []
            stages.append({'stage': entry.get('stage') or entry.get('phase') or entry.get('diagnostics', {}).get('stage'),
                           'failed': [(e['phase'], e.get('failure_component')) for e in evidence
                                      if e.get('status') == 'failure']})
    later = ((trial.get('attribution') or {}).get('planning_diagnostic') or {})
    for e in later.get('phase_evidence', []):
        if e.get('status') == 'failure':
            stages.append({'stage': later.get('stage'), 'failed': [(e['phase'], e.get('failure_component'))]})
    failed = next((s for s in reversed(stages) if s['failed']), None)
    budget = json.loads((run / 'rollouts' / trial['rollout_id'] / 'execution_diagnostics.json').read_text())
    return {'failed_stage': None if failed is None else failed['stage'],
            'failed_phase': None if failed is None else failed['failed'],
            'planning_s': round(budget['active_budget']['planning_s'], 1)}


def timing_row(run, trial):
    """Planning wall time by query and phase, and use of the attempt's active budget."""
    queries, phases = [], {}
    if trial['planning_ref']:
        for entry in json.loads((run / trial['planning_ref']).read_text()):
            body = entry.get('diagnostics') or entry
            if body.get('planning_s') is None:
                continue
            queries.append({'kind': entry.get('stage') or entry.get('phase'), 'planning_s': round(body['planning_s'], 3)})
            for q in body.get('queries', []):
                phases.setdefault(q['phase'], []).append(round(q['wall_time_s'], 3))
    later = ((trial.get('attribution') or {}).get('planning_diagnostic') or {})
    if later.get('planning_s') is not None:
        queries.append({'kind': later.get('stage'), 'planning_s': round(later['planning_s'], 3)})
        for q in later.get('queries', []):
            phases.setdefault(q['phase'], []).append(round(q['wall_time_s'], 3))
    budget = json.loads((run / 'rollouts' / trial['rollout_id'] / 'execution_diagnostics.json').read_text())['active_budget']
    # The pre-close batch is planned once for all lanes and repeated in each lane's log.
    preclose = next((q['planning_s'] for q in queries if q['kind'] in ('preclose', 'preclose_batch')), None)
    return {'preclose_batch_s': preclose, 'queries': queries, 'phase_wall_s': phases,
            'planning_s': round(budget['planning_s'], 2), 'execution_s': round(budget['execution_s'], 2),
            'active_s': round(budget['planning_s'] + budget['execution_s'], 2), 'limit_s': budget['limit_s']}


def timing_table(rows):
    """Per station: medians over trials, B/A ratio of the same-work pre-close batch and per phase."""
    print('\n| 站位 | pre-close 批量查询 A / B s | 比值 | 分阶段中位耗时 A → B s（查询数） | trial 规划耗时中位 A / B s | 活动预算最高占用 A / B |')
    print('|---|---|---:|---|---|---|')
    summary = {}
    for label in dict.fromkeys(r['label'] for r in rows):
        arms = {arm: [r['timing'] for r in rows if r['label'] == label and r['arm'] == arm] for arm in 'AB'}
        pre = {arm: next((t['preclose_batch_s'] for t in ts if t['preclose_batch_s'] is not None), None) for arm, ts in arms.items()}
        phase = {}
        for arm, ts in arms.items():
            for t in ts:
                for name, values in t['phase_wall_s'].items():
                    phase.setdefault(name, {}).setdefault(arm, []).extend(values)
        median = lambda v: round(float(np.median(v)), 2) if v else None
        phases = {name: {arm: (median(v.get(arm, [])), len(v.get(arm, []))) for arm in 'AB'} for name, v in phase.items()}
        row = {'preclose_batch_s': pre, 'ratio': None if None in pre.values() else round(pre['B'] / pre['A'], 2),
               'phase_median_s': phases,
               'trial_planning_median_s': {arm: median([t['planning_s'] for t in ts]) for arm, ts in arms.items()},
               'max_budget_use': {arm: max((t['active_s'] / t['limit_s'] for t in ts), default=None) for arm, ts in arms.items()}}
        summary[label] = row
        text = '; '.join(f"{n} {v['A'][0]}→{v['B'][0]} ({v['A'][1]}/{v['B'][1]})" for n, v in phases.items())
        use = ' / '.join('—' if v is None else f'{v:.0%}' for v in row['max_budget_use'].values())
        print(f"| {label} | {pre['A']} / {pre['B']} | {row['ratio']} | {text} | "
              f"{row['trial_planning_median_s']['A']} / {row['trial_planning_median_s']['B']} | {use} |")
    return summary


def replay_contacts(run, trial, sims):
    """Robot contacts with non-target, non-floor-base bodies over the executed states."""
    from lastmile_dataflow.runtime.outcomes.contacts import ContactSampler
    path = run / 'rollouts' / trial['rollout_id'] / 'trajectory.npz'
    if trial['executed_steps'] == 0 or not path.exists():
        return None
    sim, target = sims(trial['scene_id'])
    sampler = ContactSampler(sim)
    hits = {}
    with np.load(path, allow_pickle=False) as data:
        qpos = data['qpos']
    for frame, q in enumerate(qpos):
        sim.data.qpos[:] = q
        mujoco.mj_forward(sim.model, sim.data)
        for contact in sim.data.contact:
            pair = sampler.pair(int(contact.geom1), int(contact.geom2))
            if pair.robot[0] == pair.robot[1] or pair.floor_base or pair.same_hand:
                continue
            other = pair.bodies[1] if pair.robot[0] else pair.bodies[0]
            if other in target:
                continue
            key = tuple(sorted(pair.geom_names))
            depth = max(0., -float(contact.dist))
            row = hits.setdefault(key, {'geoms': list(key), 'frames': 0, 'first_frame': frame, 'max_penetration_m': 0.})
            row['frames'] += 1
            row['max_penetration_m'] = round(max(row['max_penetration_m'], depth), 5)
    return {'frames': len(qpos), 'robot_environment_contacts': sorted(hits.values(), key=lambda r: -r['max_penetration_m'])}


def station_sims():
    from lastmile_dataflow.config import robot_config
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.scenes.geometry import descendants
    cache = {}

    def get(run_config):
        def load(scene_id):
            if scene_id not in cache:
                dataset = Path(run_config['dataset'])
                scene = next(s for s in map(json.loads, (dataset / 'scenes.jsonl').read_text().splitlines())
                             if s['scene_id'] == scene_id)
                baseline = (dataset / scene['scene']).resolve()
                cfg = robot_config(json.loads((baseline / 'config.json').read_text())['robot'])
                sim = Simulation.from_snapshot(baseline, cfg, target=scene['task']['target_body'])
                target = descendants(sim.model, sim.target_id)
                if scene['task'].get('handle_body'):
                    target |= descendants(sim.model, sim.model.body(scene['task']['handle_body']).id)
                cache[scene_id] = (sim, target)
            return cache[scene_id]
        return load
    return get, cache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pairs', nargs='+', required=True, help='label:A_run:B_run (run ids under outputs/)')
    parser.add_argument('--geometry-a', type=Path)
    parser.add_argument('--geometry-b', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    geometry = {'A': geometry_rows(args.geometry_a), 'B': geometry_rows(args.geometry_b)}
    sims, cache = station_sims()
    rows = []
    try:
        for spec in args.pairs:
            label, *runs = spec.split(':')
            for arm, name in zip('AB', runs):
                run = Path('outputs') / name
                config = json.loads((run / 'config.json').read_text())
                load = sims(config)
                for trial in sorted(map(json.loads, (run / 'trials.jsonl').read_text().splitlines()),
                                    key=lambda t: (t['station_id'], t['seed'])):
                    rows.append({
                        'label': label, 'arm': arm, 'run': name, 'station': trial['station_id'], 'seed': trial['seed'],
                        'geometry': geometry[arm].get((trial['scene_id'], trial['station_id'])),
                        'status': trial['status'], 'reason': trial['reason'],
                        'planning': planning_row(run, trial),
                        'execution': {'executed_steps': trial['executed_steps'], **trial['execution_termination']},
                        'task': trial['task_outcome'], 'timing': timing_row(run, trial),
                        'contacts': replay_contacts(run, trial, load)})
    finally:
        for sim, _ in cache.values():
            sim.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print('| 站位 | 组 | seed | 初始底盘球最小/实体（m） | 规划失败处 | 执行步数 | 终止 | 任务 | 规划耗时 s | 机器人-环境接触（最大穿透 m） |')
    print('|---|---|---:|---|---|---:|---|---|---:|---|')
    for r in rows:
        g = r['geometry'] or {}
        geo = '—' if not g else f"{g['chassis_sphere_min_m']:+.3f} / {g['same_obstacle_physical_m']:+.4f}"
        p = r['planning']
        failed = '—' if not p['failed_phase'] else f"{p['failed_stage']}: {p['failed_phase']}"
        c = r['contacts']
        contacts = '未执行' if c is None else (
            '无' if not c['robot_environment_contacts'] else
            '; '.join(f"{'/'.join(n.split('/')[-1][:28] for n in h['geoms'])} {h['max_penetration_m']:.4f}"
                      for h in c['robot_environment_contacts'][:2]))
        print(f"| {r['label']} {r['station']} | {r['arm']} | {r['seed']} | {geo} | {failed} | {r['execution']['executed_steps']} "
              f"| {r['execution']['status']}/{r['execution']['reason']} | {r['task']['status']} | {p['planning_s']} | {contacts} |")
    timing = timing_table(rows)
    args.output.write_text(json.dumps({'trials': rows, 'timing': timing}, indent=1))


if __name__ == '__main__':
    main()
