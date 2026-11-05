#!/usr/bin/env python3
"""N1 ordered Pick/Open queries: one resident planning service vs fresh planners.

Each query is a frozen pre-close request rebuilt from an existing run: its
prepared `state.npz`, task, grasp candidates and the original trial seeds.
The queries are sent in order to ONE PlanningService (as a GPU worker sees
them), then each again to a fresh planner (`reuse_planners=False`). Checked:
  - whether the resident service actually reused a solver family;
  - the target/handle worlds the planner exported equal the fresh planner's;
  - every ContactManager holds the planner's own context and checkers, the
    handle cost exists iff the task has a handle, contact masks match links;
  - carried-object spheres are detached after the query;
  - planning results (success, chosen grasp, stage waypoints) per lane.
Planning only: no physical execution. Output: one JSON.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

import numpy as np


def run_dataset(run_dir):
    """The run's input dataset. A run moved together with its outputs/ tree keeps the old
    absolute path in config.json; resolve it under the outputs/ root the run now lives in."""
    path = Path(json.loads((Path(run_dir) / 'config.json').read_text())['dataset'])
    if not path.exists() and '/outputs/' in str(path):
        path = Path(str(Path(run_dir).resolve()).split('/outputs/')[0]) / 'outputs' / str(path).split('/outputs/', 1)[1]
    return path.resolve()


def frozen_request(run, scene_id, station_id, root, assets_dir, *, reuse, overrides=None):
    from lastmile_dataflow.config import robot_config
    from lastmile_dataflow.runtime.execution.protocol import PlanningRequest, SimulationState, TrialBudget, TrialIdentity
    from lastmile_dataflow.runtime.execution_config import ExecutionConfig
    from lastmile_dataflow.scenes.dataset import load_scenes
    from lastmile_dataflow.scenes.tasks.discovery import grasp_candidates

    run_dir = Path('outputs') / run
    saved = json.loads((run_dir / 'config.json').read_text())
    scene = next(s for s in load_scenes(run_dataset(run_dir)) if s['scene_id'] == scene_id)
    index = next(i for i, s in enumerate(scene['stations']) if s['station_id'] == station_id)
    trials = sorted((json.loads(line) for line in (run_dir / 'trials.jsonl').read_text().splitlines()),
                    key=lambda t: t['trial'])
    trials = [t for t in trials if t['scene_id'] == scene_id and t['station_id'] == station_id]
    preparation = run_dir / 'preparations' / scene_id / station_id
    with np.load(preparation / 'state.npz', allow_pickle=False) as state:
        frozen = SimulationState(state['state'], int(state['state_spec']), json.loads(str(state['control_json'])),
                                 float(state['clock_remainder']))
    config = ExecutionConfig(**{**saved['config'], **(overrides or {})})
    identities = [TrialIdentity(scene_id, index, t['trial'], f"{t['rollout_id']}-{'reuse' if reuse else 'fresh'}", t['seed'])
                  for t in trials]
    robot = robot_config(json.loads((Path(scene['scene_path']) / 'config.json').read_text())['robot'])
    request = PlanningRequest(f"{scene['house']}-{station_id}-{'reuse' if reuse else 'fresh'}", scene['scene_path'],
        identities, frozen, 'preclose', [TrialBudget(config.attempt_timeout_s) for _ in identities],
        {'task': scene['task'], 'robot': robot, 'config': config, 'assets_dir': assets_dir, 'root': str(root),
         'candidates': grasp_candidates(scene['task'], assets_dir), 'preparation_path': str(preparation.resolve()),
         'reuse_planners': reuse})
    original = [{'seed': t['seed'], 'status': t['status'], 'reason': t['reason']} for t in trials]
    return request, original


def binding_checks(planner):
    """Retained planner after release: context, cost manager references, masks, attachment."""
    import torch
    from lastmile_dataflow.planning.curobo.costs import ContactManager
    context = planner.context
    params = planner.motion.kinematics.config.kinematics_config
    expected = {name: sorted(sum((params.get_sphere_index_from_link_name(l).tolist() for l in links), []))
                for name, links in (('contact', context.contact_links), ('handle', context.handle_contact_links))}
    managers = []
    for solver in (planner.motion.ik_solver, planner.motion.trajopt_solver):
        for rollout in solver.core.get_all_rollout_instances():
            for attribute in ('cost_manager', 'constraint_manager', 'hybrid_cost_constraint_manager',
                              'convergence_manager', 'metrics_cost_manager', 'metrics_constraint_manager',
                              'metrics_hybrid_cost_constraint_manager', 'metrics_convergence_manager'):
                manager = getattr(rollout, attribute, None)
                if isinstance(manager, ContactManager):
                    managers.append(manager)
    problems = []
    for manager in managers:
        if manager.context is not context:
            problems.append('manager context is not the planner context')
        if manager.target_cost is not None and manager.target_cost.config.scene_collision_checker is not context.target_checker:
            problems.append('target cost holds a different checker')
        # Only managers with a scene collision config (hence a target cost) carry a handle cost.
        if (manager.handle_cost is not None) != (context.handle_checker is not None and manager.target_cost is not None):
            problems.append('handle cost presence differs from handle checker')
        if manager.handle_cost is not None and manager.handle_cost.config.scene_collision_checker is not context.handle_checker:
            problems.append('handle cost holds a different checker')
        if sorted(manager.contact_indices) != expected['contact'] or sorted(manager.handle_indices) != expected['handle']:
            problems.append('contact mask indices differ from contact links')
    # link_spheres: [n_configs, num_spheres, 4]; detach restores the reference spheres.
    attached = params.get_sphere_index_from_link_name(planner.model.attached_link)
    current, reference = params.link_spheres[:, attached], params.reference_link_spheres[:, attached]
    return {'contact_managers': len(managers), 'with_target_cost': sum(m.target_cost is not None for m in managers),
            'with_handle_cost': sum(m.handle_cost is not None for m in managers),
            'handle_checker': context.handle_checker is not None, 'context_attached': context.attached,
            'carried_spheres_active': int((current[..., 3] > 0).sum().item()),
            'carried_spheres_reset': bool(torch.equal(current, reference)), 'problems': sorted(set(problems))}


def worlds(directory):
    result = {}
    for name in ('collision_world.json', 'target_world.json', 'handle_world.json'):
        path = Path(directory) / name
        result[name] = json.loads(path.read_text())['geoms'] if path.exists() else None
    return result


def lanes(responses):
    rows = []
    for response in responses:
        plan = response.plan
        rows.append({'seed': response.identity.seed, 'status': response.status,
                     'reason': response.diagnostics.get('reason'), 'grasp_index': plan.get('index'),
                     'stages': {k: np.asarray(v).tolist() for k, v in plan.get('stages', {}).items()}})
    return rows


def compare(reused, fresh):
    rows = []
    for a, b in zip(reused, fresh):
        difference = None
        if a['stages'].keys() == b['stages'].keys() and a['stages']:
            difference = max(float(np.max(np.abs(np.asarray(a['stages'][k]) - np.asarray(b['stages'][k]))))
                             if np.shape(a['stages'][k]) == np.shape(b['stages'][k]) else float('inf')
                             for k in a['stages'])
        rows.append({'seed': a['seed'], 'status': [a['status'], b['status']], 'grasp_index': [a['grasp_index'], b['grasp_index']],
                     'same_status': a['status'] == b['status'], 'same_grasp': a['grasp_index'] == b['grasp_index'],
                     'max_waypoint_difference': difference})
    return rows



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--queries', nargs='+', required=True, help='run:scene_id:station_id in order')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--assets-dir', required=True)
    parser.add_argument('--environment-representation', help='override the run config (A/B of the environment world)')
    args = parser.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)
    from lastmile_dataflow.planning.pool import PlanningService
    args.output_dir.mkdir(parents=True, exist_ok=False)
    specs = [q.split(':') for q in args.queries]
    overrides = {'environment_representation': args.environment_representation} if args.environment_representation else {}
    report = {'queries': args.queries, 'gpu': args.gpu, 'overrides': overrides, 'resident': [], 'fresh': []}
    service = PlanningService()
    try:
        for run, scene, station in specs:
            request, original = frozen_request(run, scene, station, args.output_dir / 'resident', args.assets_dir, reuse=True,
                                               overrides=overrides)
            start = time.perf_counter()
            responses = service.execute(request)
            elapsed = round(time.perf_counter() - start, 2)
            planner = next(reversed(service.planners.planners.values()))
            directory = args.output_dir / 'resident' / 'planning' / request.query_id
            reuse = directory / 'planner_reuse.json'
            report['resident'].append({'query': f'{run}:{scene}:{station}', 'query_id': request.query_id,
                'operation': request.inputs['task']['operation'],
                'target_is_handle': request.inputs['task']['target_body'] == request.inputs['task'].get('handle_body'),
                'solver_reused': json.loads(reuse.read_text()).get('solver_reused', False) if reuse.exists() else False,
                'retained_families': len(service.planners.planners),
                'worlds': worlds(directory), 'binding': binding_checks(planner),
                'lanes': lanes(responses), 'original_trials': original, 'elapsed_s': elapsed})
    finally:
        service.close()
    for run, scene, station in specs:
        service = PlanningService()
        try:
            request, _ = frozen_request(run, scene, station, args.output_dir / 'fresh', args.assets_dir, reuse=False,
                                        overrides=overrides)
            start = time.perf_counter()
            responses = service.execute(request)
            elapsed = round(time.perf_counter() - start, 2)
        finally:
            service.close()
        report['fresh'].append({'query': f'{run}:{scene}:{station}', 'query_id': request.query_id,
                                'worlds': worlds(args.output_dir / 'fresh' / 'planning' / request.query_id),
                                'lanes': lanes(responses), 'elapsed_s': elapsed})
    for resident, fresh in zip(report['resident'], report['fresh']):
        resident['same_worlds_as_fresh'] = resident['worlds'] == fresh['worlds']
        resident['versus_fresh'] = compare(resident['lanes'], fresh['lanes'])
        print(f"{resident['query_id']:28s} {resident['operation']:5s} reused={resident['solver_reused']!s:5s} "
              f"worlds_equal={resident['same_worlds_as_fresh']!s:5s} problems={resident['binding']['problems']} "
              f"reuse/fresh={[r['status'] for r in resident['versus_fresh']]}")
    (args.output_dir / 'family_reuse_check.json').write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
