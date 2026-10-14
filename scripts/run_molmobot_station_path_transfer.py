"""Navigation stop-point ablation on the frozen S0009->S0023 segment, then MolmoBot.

Companion to run_molmobot_station_transfer.py (E02). E02 navigated the whole
segment and operated at the far endpoint. This entry point navigates the same
segment but stops at a requested fraction of it, keeping every other part of the
protocol identical: same frozen scene, same single S0009 initial state, no reset,
no teleport, no snapshot restore after begin(), same five seeds, same budgets.

The stop point is not a sampled station (the station grid has 0.2218 m spacing,
so the fractions fall between grid cells). It is an arithmetic interpolation of
the segment endpoints, exactly the "arithmetic midpoint" convention already used
for augmented-station sampling; it is verified here by the same style of full
robot static collision check E02 used for its connector, and is not claimed to be
a sampled/validated station.
"""
import argparse
import copy
from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess
import uuid
import numpy as np
from lastmile_dataflow.config import RobotConfig, CollectionConfig
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.io import read_json, write_json, file_digest
from lastmile_dataflow.navigation.astar import Grid, search
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
from lastmile_dataflow.policies.molmobot import MolmoBotClient
from lastmile_dataflow.workflows.no_edit import task_seed

TRIAL_KEY = '{start}-{goal}'          # same key as E02 => identical five seeds
PLANNER = 'straight-segment interpolation from the frozen start station'


def shortest_yaw(value):
    return float((value + np.pi) % (2 * np.pi) - np.pi)


def interpolated_goal(start, goal, fraction):
    """Arithmetic point on the start->goal segment, no new initial state invented."""
    xy = np.asarray(start['xy'], dtype=float) + fraction * (
        np.asarray(goal['xy'], dtype=float) - np.asarray(start['xy'], dtype=float))
    span = shortest_yaw(goal['base'][2] - start['base'][2])
    yaw = shortest_yaw(start['base'][2] + fraction * span)
    point = copy.deepcopy(start)
    point['station_id'] = f'{start["station_id"]}-{goal["station_id"]}-f{int(round(fraction * 100)):03d}'
    point['xy'] = [float(v) for v in xy]
    point['base'] = [float(xy[0]), float(xy[1]), yaw]
    # Only the base changes. Head/arms/torso stay at the frozen start initial state,
    # which is what makes each fraction a single-variable comparison against E02.
    point['initial'] = copy.deepcopy(start['initial'])
    point['initial']['base'] = list(point['base'])
    point['grid_index'] = None
    point['geometry'] = 'interpolated_segment_point'
    point['segment_fraction_from_start'] = fraction
    point['provenance'] = PLANNER
    return point


def segment_path(start, stop, count=101):
    """Full robot static collision check per point; never marks blocked cells free."""
    xy = np.linspace(start['xy'], stop['xy'], count)
    yaw = np.linspace(start['base'][2], stop['base'][2], count)
    return xy, yaw


def build_path(baseline, task, start, stop, out, count=101):
    scene_grid = np.load(task['_grid_path'])
    grid = Grid(scene_grid['origin'], float(scene_grid['resolution_m']), scene_grid['free'])
    path = search(grid, start['xy'], stop['xy'])
    if path:
        return path, None
    xy, yaw = segment_path(start, stop, count)
    checks = []
    from lastmile_dataflow.runtime.no_edit_execution import clone_sim
    from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station, robot_contacts
    for point, angle in zip(xy, yaw):
        probe = clone_sim(baseline, target=task['target_body'])
        try:
            station = copy.deepcopy(start)
            station['initial']['base'] = [float(point[0]), float(point[1]), float(angle)]
            place_frozen_station(probe, station)
            collisions = robot_contacts(probe)
            checks.append({'base': station['initial']['base'], 'collisions': collisions})
        finally:
            probe.close()
    path = {'xy': xy.tolist(), 'length_m': float(np.linalg.norm(np.asarray(stop['xy']) - start['xy'])),
            'planner': f'straight connector with {count} full robot static collision checks',
            'grid_status': 'endpoints blocked by conservative enclosing-circle support inflation',
            'static_checks_only': True}
    return path, checks


def run_fraction(baseline, task, start, goal, stop, config, collection, out, policy, path, trials):
    """Retain local failures, but never reuse a failed shared model service."""
    results = []
    for i in range(trials):
        seed = task_seed(config.seed, 'val_103', task['task_id'],
                         TRIAL_KEY.format(start=start['station_id'], goal=goal['station_id']), i)
        attempt = (f'path-{start["station_id"]}-{goal["station_id"]}'
                   f'-f{int(round(stop["segment_fraction_from_start"] * 100)):03d}'
                   f'-t{i}-{uuid.uuid4().hex[:8]}')
        print(f'Trial {i + 1}/{trials} fraction={stop["segment_fraction_from_start"]:.4f} seed={seed}', flush=True)
        row = run_vla_attempt(baseline, task, start, config, collection, out, attempt,
                              seed=seed, policy=policy, path=path, goal=stop)
        row['trial'] = i + 1
        results.append(row)
        write_json(out / 'trials.json', results)

        def navigation(result):
            return result.get('navigation') or {'status': 'unknown', 'reason': result.get('reason', 'unavailable')}

        nav = sum(navigation(r)['status'] == 'success' for r in results)
        success = sum(r['status'] == 'success' and navigation(r)['status'] == 'success' for r in results)
        shared_failure = row['status'] == 'infrastructure_error' or row.get('failure_scope') == 'shared'
        summary = {'completed_trials': len(results), 'requested_trials': trials,
                   'stop_fraction_from_start': stop['segment_fraction_from_start'],
                   'navigation_successes': nav,
                   'end_to_end_successes': success, 'end_to_end_success_rate': success / len(results),
                   'navigation_success_rate': nav / len(results),
                   'operation_success_rate_given_arrival': success / nav if nav else None,
                   'stopped_early': shared_failure and len(results) < trials,
                   'stop_reason': row['reason'] if shared_failure else None,
                   'statuses': [{'trial': r['trial'], 'status': r['status'], 'reason': r['reason'],
                                 'navigation': navigation(r)} for r in results]}
        write_json(out / 'summary.json', summary)
        print(summary, flush=True)
        if shared_failure:
            break
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--start', default='S0009')
    p.add_argument('--goal', default='S0023')
    p.add_argument('--fraction', type=float, required=True,
                   help='stop point as a fraction of the start->goal segment')
    p.add_argument('--trials', type=int, default=5)
    p.add_argument('--static-checks', type=int, default=101)
    p.add_argument('--dry-run', action='store_true', help='geometry and evidence only, no model')
    args = p.parse_args()
    if not 0.0 < args.fraction < 1.0:
        raise ValueError('fraction must be strictly inside (0, 1)')
    source = args.source.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    scene = source / 'scenes/val_103'
    taskdir = scene / 'tasks/cup2'
    frozen = read_json(source / 'frozen_config.json')
    stations = read_json(taskdir / 'stations.json')
    start = next(s for s in stations if s['station_id'] == args.start.upper())
    goal = next(s for s in stations if s['station_id'] == args.goal.upper())
    if start['geometry'] != 'valid' or goal['geometry'] != 'valid':
        raise ValueError('invalid stations')
    task = read_json(taskdir / 'task.json')
    task['_grid_path'] = str(taskdir / 'navigation_grid.npz')
    config = NoEditConfig(**frozen['config'])
    robot = RobotConfig(**frozen['robot'])
    collection = CollectionConfig(**frozen['collection'])
    baseline = Simulation.from_snapshot(scene / 'scene', robot)
    shutil.copytree(scene / 'scene', out / 'scene')
    stop = interpolated_goal(start, goal, args.fraction)
    path, checks = build_path(baseline, task, start, stop, out, args.static_checks)
    if checks is not None:
        write_json(out / 'connector_collision_checks.json', checks)
        if any(c['collisions'] for c in checks):
            raise RuntimeError('planning_no_solution_exact_geometry')
    segment_m = float(np.linalg.norm(np.asarray(goal['xy']) - np.asarray(start['xy'])))
    write_json(out / 'experiment.json', {
        'source': str(source), 'source_config_sha256': file_digest(source / 'frozen_config.json'),
        'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'hypothesis': (f'Stopping at {args.fraction:.4f} of the S0009->S0023 segment and operating there '
                       f'differs from operating at the far endpoint (E02, 3/5).'),
        'expected_successes': [0, 5], 'trials': args.trials,
        'start': start, 'goal': goal, 'stop': stop, 'path': path,
        'segment': {'start_xy': start['xy'], 'goal_xy': goal['xy'], 'length_m': segment_m,
                    'stop_xy': stop['xy'], 'stop_distance_m': segment_m * args.fraction,
                    'stop_distance_to_target_m': float(np.linalg.norm(
                        np.asarray(stop['xy']) - np.asarray(task['anchor_world'][:2])))},
        'seed_key': TRIAL_KEY.format(start=start['station_id'], goal=goal['station_id']),
        'seeds_shared_with_e02': True,
        'initial_state_policy': ('single frozen S0009 initial state; the stop point changes only the base '
                                 'pose, so this is not a strict station-initial-state control'),
        'baseline': [s for s in read_json(taskdir / 'station_statistics.json')
                     if s['station_id'] in (start['station_id'], goal['station_id'])],
        'config': asdict(config), 'collection': asdict(collection),
        'stop_criteria': f'{args.trials} trials; infrastructure errors recorded separately; '
                         'no teleport/reset after begin.',
        'retention': 'All outcomes, actions, physics, replay, videos and frozen input retained here.'})
    if args.dry_run:
        baseline.close()
        print(f'dry run ok: stop={stop["base"]} length_m={path["length_m"]:.6f}', flush=True)
        return
    logs = out.parents[1] / 'logs'
    logs.mkdir(exist_ok=True)
    policy = None
    try:
        policy = MolmoBotClient(config.policy_config, logs / (out.name + '-model.log'))
        write_json(out / 'model_service.json', policy.metadata)
        run_fraction(baseline, task, start, goal, stop, config, collection, out, policy, path, args.trials)
    finally:
        if policy:
            policy.close()
        baseline.close()


if __name__ == '__main__':
    main()
