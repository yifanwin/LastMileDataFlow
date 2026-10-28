#!/usr/bin/env python3
"""N3: which state a pre-close query rejects — start, IK goal, or a trajectory time step.

Rebuilds the frozen pre-close request of one station (`n1_family_reuse_check.frozen_request`:
prepared `state.npz`, task, grasp candidates, original trial seeds) and plans it once,
in-process, with a fresh planner. Observation hooks only, no extra search:
  - IK solves: the start state and every returned goal state evaluated with the IK
    metrics rollout (the phase's own constraints and contact allowance);
  - TrajOpt solves: for every seed of every optimization round, before seed ranking, the
    per-time-step feasibility of the optimized and the interpolated trajectory, the first
    infeasible step (0 = start, last = goal), positive constraint terms and colliding
    spheres by link;
  - the PRM graph seeding check behind `Start or End state in collision`: which of the
    lane x seed starts and goals failed, and on which terms.
Then, per lane, the least-violating seed of the last TrajOpt solve is restored in a fresh,
un-begun simulation at its worst infeasible step: every colliding sphere is matched to
the exported obstacle geoms it penetrates (per-geom SDF) and to the physical distance
between the robot collision geoms of that link and the obstacle (as p2_collision_diagnosis).
Output: one JSON.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import tempfile

import mujoco
import numpy as np

# Robot collision geoms (namespace stripped) that each planning link's spheres stand for.
LINK_GEOMS = {
    'omron_base': ('mobilebase0_pedestal_feet_col', 'mobilebase0_g0_col'),
    'panda_link0': ('robot0_link0_collision', 'mobilebase0_g1_col'),
    **{f'panda_link{i}': (f'robot0_link{i}_collision',) for i in range(1, 8)},
    'panda_gripper': ('gripper0_right_hand_collision',),
    'panda_finger1': ('gripper0_right_finger1_collision', 'gripper0_right_finger1_pad_collision'),
    'panda_finger2': ('gripper0_right_finger2_collision', 'gripper0_right_finger2_pad_collision'),
}
WORLDS = {'scene_collision': 'environment', 'target_collision': 'target', 'handle_collision': 'handle'}


class Recorder:
    def __init__(self):
        self.solves, self.graph, self.current, self.sphere_links = [], [], None, None
        self.link_spheres = None   # planner sphere table at the last solve, incl. carried-object spheres


def numpy(tensor):
    return tensor.detach().cpu().numpy()


def sphere_links(params):
    links = {}
    for link in params.link_name_to_idx_map:
        for i in params.get_sphere_index_from_link_name(link).tolist():
            links[i] = link
    return links


def term_rows(metrics):
    """Per evaluated state (row) and time step: positive constraint terms and their spheres."""
    cc = metrics.costs_and_constraints
    result = []
    for category in ('constraints', 'hybrid_costs_constraints'):
        terms = getattr(cc, category)
        for name, value in zip(terms.names, terms.values):
            v = numpy(value)
            result.append((f'{category}/{name}', v if v.ndim == 3 else v[..., None]))
    return result


def positive_terms(terms, row, t):
    out = {}
    for name, v in terms:
        cell = v[row, t]
        if (cell > 0).any():
            entry = {'max': round(float(cell.max()), 5)}
            if cell.shape[-1] > 1:
                spheres = np.flatnonzero(cell > 0)
                entry['spheres'] = spheres.tolist()
            out[name] = entry
    return out


def link_counts(entry, rec):
    return dict(Counter(rec.sphere_links[i] for i in entry.get('spheres', [])))


def summarize_trajopt(result, rec):
    """All seeds of one optimization round, before top-k selection."""
    B, S = result.batch_size, result.num_seeds
    success = numpy(result.success).reshape(B, S)
    position = numpy(result.position_error).reshape(B, S)
    rotation = numpy(result.rotation_error).reshape(B, S)
    round_ = {'success': success.tolist(), 'position_error': position.tolist(),
              'rotation_error': rotation.tolist(), 'trajectories': {}}
    for kind, metrics in (('optimized', result.metrics), ('interpolated', result.interpolated_metrics)):
        if metrics is None:
            continue
        cc = metrics.costs_and_constraints
        per_step = cc.get_sum_constraint(sum_horizon=False, include_all_hybrid=False)
        per_step = numpy(per_step).reshape(B * S, -1)
        terms = term_rows(metrics)
        position_q = numpy(metrics.state.joint_state.position).reshape(B * S, per_step.shape[1], -1)
        seeds = []
        for row in range(B * S):
            bad = np.flatnonzero(per_step[row] > 0)
            seed = {'lane': row // S, 'seed': row % S, 'horizon': int(per_step.shape[1]),
                    'feasible': bool(len(bad) == 0), 'violation_sum': round(float(per_step[row].clip(min=0).sum()), 5)}
            if len(bad):
                worst = int(np.argmax(per_step[row]))
                seed.update(first_bad_step=int(bad[0]), last_bad_step=int(bad[-1]), bad_steps=int(len(bad)),
                            worst_step=worst, start_bad=bool(per_step[row, 0] > 0),
                            goal_bad=bool(per_step[row, -1] > 0))
                worst_terms = positive_terms(terms, row, worst)
                for entry in worst_terms.values():
                    if 'spheres' in entry:
                        entry['links'] = link_counts(entry, rec)
                seed['worst_terms'] = worst_terms
                seed['bad_terms_any_step'] = sorted({name for name, v in terms if (v[row, bad] > 0).any()})
                seed['worst_q'] = position_q[row, worst].round(6).tolist()
            seeds.append(seed)
        round_['trajectories'][kind] = seeds
    return round_


def install_hooks(rec):
    import torch
    from curobo._src.graph_planner.graph_planner_prm import PRMGraphPlanner
    from curobo._src.solver.solver_trajopt_result import TrajOptSolverResult
    from lastmile_dataflow.planning.curobo import tracing

    trace_call = tracing.SolverTrace.__call__

    def traced(self, *args, **kwargs):
        owner = self.owner
        if rec.sphere_links is None:
            rec.sphere_links = sphere_links(owner.motion.kinematics.config.kinematics_config)
        start = kwargs.get('current_state')
        start = None if start is None else start.position.detach().clone()
        rec.link_spheres = owner.motion.kinematics.config.kinematics_config.link_spheres.detach().clone()
        rec.current = {'solver': self.kind, 'phase': owner.progress_phase, 'query_index': owner.query_index,
                       'solve_index': len(owner.solver_log) + 1, 'rounds': []}
        result = trace_call(self, *args, **kwargs)
        row, rec.current = rec.current, None
        if self.kind == 'ik' and result.solution is not None:
            rollout = self.solver.core.metrics_rollout
            dof = result.solution.shape[-1]
            names = [n for n in self.solver.joint_names if n in owner.names]
            assert len(names) == dof, (names, dof)
            goals = result.solution.reshape(-1, dof)
            # Evaluate in the solver's own seed batch shape; extra rows repeat the first state.
            batch = result.optimized_seeds.reshape(-1, dof).shape[0]
            states = goals if start is None else torch.cat([start.reshape(-1, dof)[:1], goals])
            padded = states[:1].repeat(batch, 1)
            padded[:len(states)] = states
            metrics = rollout.compute_metrics_from_action(padded.view(batch, 1, dof).contiguous())
            feasible = numpy(metrics.costs_and_constraints.get_feasible(include_all_hybrid=False, sum_horizon=True))
            terms = term_rows(metrics)
            offset = 0 if start is None else 1
            if start is not None:
                row['start'] = {'q': numpy(states[0]).round(6).tolist(), 'feasible': bool(feasible[0]),
                                'terms': positive_terms(terms, 0, 0)}
            row['goals_feasible'] = feasible[offset:offset + len(goals)].reshape(result.solution.shape[:-1]).tolist()
            row['goal_terms'] = {str(i): positive_terms(terms, i + offset, 0)
                                 for i in range(len(goals)) if not feasible[i + offset]}
            row['goal_q'] = {str(i): numpy(goals[i]).round(6).tolist()
                             for i in range(len(goals)) if not feasible[i + offset]}
            row['joint_names'] = names
        rec.solves.append(row)
        return result

    tracing.SolverTrace.__call__ = traced

    process = TrajOptSolverResult.process_metrics_and_rank_seeds

    def processed(self):
        process(self)
        if rec.current is not None and rec.current['solver'] == 'trajopt':
            rec.current['rounds'].append(summarize_trajopt(self, rec))

    TrajOptSolverResult.process_metrics_and_rank_seeds = processed

    find = PRMGraphPlanner._find_path_impl

    def find_path(self, x_start, x_goal):
        if rec.sphere_links is None:   # planner warmup, before any traced query
            return find(self, x_start, x_goal)
        nodes = torch.cat((x_start.unsqueeze(1), x_goal.unsqueeze(1)), dim=1).view(-1, self.action_dim)
        mask = numpy(self.check_samples_feasibility(nodes)).reshape(-1, 2)
        failed = {}
        size = self.config.feasibility_buffer_size
        for begin in range(0, len(nodes), size):
            chunk = nodes[begin:begin + size]
            self._max_act_buffer[:len(chunk), :, :] = chunk.unsqueeze(1)
            terms = term_rows(self.feasibility_rollout.compute_metrics_from_action(self._max_act_buffer))
            for i in range(len(chunk)):
                node = begin + i
                if not mask.reshape(-1)[node]:
                    entry = positive_terms(terms, i, 0)
                    for e in entry.values():
                        if 'spheres' in e:
                            e['links'] = link_counts(e, rec)
                    failed[f"{'start' if node % 2 == 0 else 'goal'}_{node // 2}"] = entry
        rec.graph.append({'pairs': len(mask), 'start_feasible': mask[:, 0].tolist(),
                          'goal_feasible': mask[:, 1].tolist(), 'failed_terms': failed})
        return find(self, x_start, x_goal)

    PRMGraphPlanner._find_path_impl = find_path


def set_configuration(sim, model, q):
    """Robot qpos for planner joints q (model.names order) in a fresh, un-begun simulation."""
    robot = sim.robot
    for group, values in (('base', model.base_target_world(q)), ('arm', model.arm_target(q))):
        for name, value in zip(robot.groups[group], values):
            sim.data.qpos[robot.addresses[name]] = value
    mujoco.mj_forward(sim.model, sim.data)
    return float(np.abs(model.current_joints() - q).max())


def state_geometry(sim, model, sdf, kinematics, q, names, worst_terms, rec, range_m):
    """Colliding spheres at q -> penetrated exported geoms -> physical robot/obstacle distance."""
    from curobo.types import JointState
    from lastmile_dataflow.planning.curobo.collision_world import exported_geoms
    from lastmile_dataflow.planning.robots.rby1m_mobile_torso import ordered_points
    from lastmile_dataflow.scenes.geometry import descendants
    from p2_collision_diagnosis import physical_distance
    q_model = np.asarray(ordered_points(q, names, list(model.names)))
    roundtrip = set_configuration(sim, model, q_model)
    spheres = kinematics.config.kinematics_config.link_spheres
    if rec.link_spheres is not None and spheres.shape == rec.link_spheres.shape:
        spheres.copy_(rec.link_spheres.to(spheres.device))   # the planner's carried-object spheres
    state = kinematics.compute_kinematics(JointState.from_position(
        sdf.device.to_device(np.asarray(ordered_points(q_model, list(model.names), list(kinematics.joint_names)),
                                        np.float32)).view(1, -1), joint_names=list(kinematics.joint_names)))
    local = numpy(state.robot_spheres.reshape(-1, 4)).astype(float)
    world = local[:, :3] @ model.base[:3, :3].T + model.base[:3, 3]
    m, d, ns = sim.model, sim.data, sim.robot.config.namespace
    target = descendants(m, sim.target_id)
    handle = descendants(m, sim.handle_id) if sim.handle_id else set()
    geoms = {'environment': lambda: exported_geoms(sim, model.base, range_m),
             'target': lambda: exported_geoms(sim, model.base, range_m, target_only=True, bodies=target - handle),
             'handle': lambda: exported_geoms(sim, model.base, range_m, target_only=True, bodies=handle)}
    rows = []
    for term, entry in worst_terms.items():
        world_name = WORLDS.get(term.split('/')[-1])
        if world_name is None or 'spheres' not in entry:
            continue
        spheres = entry['spheres']
        candidates = list(geoms[world_name]())
        for g, vertices, faces in candidates:
            near = [i for i in spheres
                    if np.linalg.norm(world[i] - d.geom_xpos[g]) - m.geom_rbound[g] - local[i, 3] < .05]
            if not near:
                continue
            centers = sdf.center_sdf(sdf.mesh(vertices, faces), world[near])
            hits = [(i, round(c - local[i, 3], 5)) for i, c in zip(near, centers) if c is not None and c - local[i, 3] < 0]
            if not hits:
                continue
            links = sorted({rec.sphere_links[i] for i, _ in hits})
            physics = {}
            for link in links:
                # Carried-object spheres stand for the grasped target's own collision geoms.
                robot_geoms = ([m.geom(ns + name).id for name in LINK_GEOMS.get(link, ())] if link != model.attached_link else
                               [k for k in range(m.ngeom) if m.geom_bodyid[k] in target and m.geom_contype[k] | m.geom_conaffinity[k]])
                for k in robot_geoms:
                    physics[m.geom(k).name] = physical_distance(m, d, k, g)['accepted_m']
            rows.append({'term': term, 'world': world_name, 'obstacle_geom': m.geom(g).name,
                         'obstacle_body': m.body(int(m.geom_bodyid[g])).name,
                         'spheres': [{'index': i, 'link': rec.sphere_links[i], 'clearance_m': c,
                                      'radius_m': round(float(local[i, 3]), 4)} for i, c in hits],
                         'physical_distance_m': physics})
    # Spheres the planner rejects but no single exported geom penetrates: compare the
    # planner's merged-world SDF with the nearest per-geom SDF (sign of a merged,
    # non-watertight mesh can depend on the frame of Warp's ray-parity test).
    unmatched = []
    hit = {(r['term'], s['index']) for r in rows for s in r['spheres']}
    for term, entry in worst_terms.items():
        world_name = WORLDS.get(term.split('/')[-1])
        if world_name is None or 'spheres' not in entry:
            continue
        missing = [i for i in entry['spheres'] if (term, i) not in hit]
        if not missing:
            continue
        candidates = list(geoms[world_name]())
        vertices = np.concatenate([v for _, v, _ in candidates])
        faces = np.concatenate([f + n for (_, _, f), n in zip(candidates, np.cumsum([0] + [len(v) for _, v, _ in candidates[:-1]]))])
        inverse = np.linalg.inv(model.base)
        merged_planning = sdf.mesh((np.c_[vertices, np.ones(len(vertices))] @ inverse.T)[:, :3], faces)
        merged_world = sdf.mesh(vertices, faces)
        for i in missing:
            nearest = []
            for g, v, f in candidates:
                if np.linalg.norm(world[i] - d.geom_xpos[g]) - m.geom_rbound[g] - local[i, 3] < .3:
                    c = sdf.center_sdf(sdf.mesh(v, f), world[i:i + 1])[0]
                    if c is not None:
                        nearest.append((round(c - local[i, 3], 5), m.geom(g).name))
            unmatched.append({'term': term, 'index': i, 'link': rec.sphere_links[i], 'radius_m': round(float(local[i, 3]), 4),
                              'merged_planning_frame_sdf_m': sdf.center_sdf(merged_planning, local[i:i + 1, :3])[0],
                              'merged_world_frame_sdf_m': sdf.center_sdf(merged_world, world[i:i + 1])[0],
                              'nearest_per_geom_clearance_m': sorted(nearest)[:3]})
    return {'roundtrip_error': round(roundtrip, 8), 'penetrations': rows, 'unmatched_spheres': unmatched}


def pose_request(batch, run, rollout):
    """The trial's recorded closed-hand pose query: measured state at the query step and its goal."""
    from dataclasses import replace
    from lastmile_dataflow.runtime.execution.protocol import PlanningRequest, SimulationState
    run_dir = Path('outputs') / run
    trial = next(t for t in map(json.loads, (run_dir / 'trials.jsonl').read_text().splitlines())
                 if t['rollout_id'] == rollout)
    plans = json.loads((run_dir / 'rollouts' / rollout / 'planning.json').read_text())
    entry = next(e for e in plans if e.get('goal_world') is not None and e.get('phase') == 'lift_actual')
    query = json.loads((Path(entry['diagnostics']['query_path']) / 'query.json').read_text())
    step = query['recording_step']
    with np.load(run_dir / 'rollouts' / rollout / 'trajectory.npz', allow_pickle=False) as data:
        state = SimulationState(data['integration_state'][step], int(data['state_spec']),
                                json.loads(str(data['control_json'][step])), float(data['clock_remainder'][step]))
    identity = next(i for i in batch.identities if i.seed == trial['seed'])
    config = batch.inputs['config']
    inputs = {**batch.inputs, 'side': 'right', 'phase': 'pose', 'attached': True,
              'goal': np.asarray(entry['goal_world']), 'recording_step': step}
    request = PlanningRequest(f'{rollout}-pose-replay', batch.model_dir, [identity], state, 'pose',
                              [replace(batch.budgets[0])], inputs)
    options = replace(config, operation_base_mode='fixed')   # PandaOmron locks the base after closing
    return request, {'state': state, 'options': options,
                     'summary': {'rollout': rollout, 'seed': trial['seed'], 'recording_step': step,
                                 'original_query_id': query['query_id'], 'phase': entry['phase'],
                                 'original_status': entry.get('status')}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--query', required=True, help='run:scene_id:station_id')
    parser.add_argument('--pose-rollout', help='rollout id: replay that trial\'s closed-hand pose query (lift) '
                        'from its recorded state and goal instead of the pre-close batch')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--assets-dir', required=True)
    args = parser.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import lastmile_dataflow
    from lastmile_dataflow.planning.pool import PlanningService
    from n1_family_reuse_check import frozen_request
    from p2_collision_diagnosis import SphereSDF, load_station
    rec = Recorder()
    install_hooks(rec)
    run, scene, station = args.query.split(':')
    with tempfile.TemporaryDirectory() as tmp:
        request, original = frozen_request(run, scene, station, Path(tmp), args.assets_dir, reuse=False)
        recorded = None
        if args.pose_rollout:
            request, recorded = pose_request(request, run, args.pose_rollout)
        service = PlanningService()
        try:
            responses = service.execute(request)
        finally:
            service.close()
    lanes = [{'seed': r.identity.seed, 'status': r.status, 'reason': r.diagnostics.get('reason'),
              'grasp_index': r.plan.get('index')} for r in responses]
    # Representative rejected state per lane: least-violating seed of the last TrajOpt solve.
    trajopt = [s for s in rec.solves if s['solver'] == 'trajopt' and s['rounds']]
    representative = []
    if trajopt:
        last = trajopt[-1]['rounds'][0]['trajectories']
        kind = 'interpolated' if 'interpolated' in last else 'optimized'
        for lane in range(len(lanes)):
            seeds = [s for s in last[kind] if s['lane'] == lane and not s['feasible']]
            if seeds and not lanes[lane]['status'] == 'success':
                representative.append({'lane': lane, 'trajectory': kind, 'solve_index': trajopt[-1]['solve_index'],
                                       **min(seeds, key=lambda s: s['violation_sum'])})
    if not representative:
        # No TrajOpt rejection (IK found no feasible goal): the least-violating returned IK goal.
        ik = [s for s in rec.solves if s['solver'] == 'ik' and s.get('goal_terms')]
        if ik:
            last = ik[-1]
            index, terms = min(last['goal_terms'].items(), key=lambda kv: sum(t['max'] for t in kv[1].values()))
            for entry in terms.values():
                if 'spheres' in entry:
                    entry['links'] = link_counts(entry, rec)
            representative.append({'lane': 0, 'trajectory': 'ik_goal', 'solve_index': last['solve_index'],
                                   'goal_index': int(index), 'worst_step': 0, 'worst_terms': terms,
                                   'worst_q': last['goal_q'][index]})
    geometry = []
    if representative:
        from curobo._src.robot.kinematics.kinematics import Kinematics
        from curobo._src.types.robot import RobotCfg
        sdf = SphereSDF()
        sim, task, config = load_station({'run': run, 'scene': scene, 'station': station})
        try:
            if recorded is not None:
                recorded['state'].initialize(sim)   # measured post-grasp state, not the prepared station
                config = recorded['options']
            with tempfile.TemporaryDirectory() as tmp:
                model = sim.robot.planning_model(sim, config, sim.robot.arms[0], tmp,
                                                 (task['anchor_world'], config.radius_m))
                kinematics = Kinematics(RobotCfg.create(model.cfg, device_cfg=sdf.device).kinematics)
                names = next(s['joint_names'] for s in rec.solves if s.get('joint_names'))
                for rep in representative:
                    geometry.append({'lane': rep['lane'], 'step': rep['worst_step'],
                                     **state_geometry(sim, model, sdf, kinematics, rep['worst_q'], names,
                                                      rep['worst_terms'], rec, 2 * config.radius_m + 1.)})
        finally:
            sim.close()
    report = {'query': args.query, 'pose_rollout': args.pose_rollout,
              'recorded_query': None if recorded is None else recorded['summary'],
              'source': lastmile_dataflow.__file__, 'original_trials': original,
              'lanes': lanes, 'graph_checks': rec.graph, 'solves': rec.solves,
              'representative_rejections': representative, 'rejection_geometry': geometry}
    args.output.write_text(json.dumps(report, indent=1))
    for row in rec.solves:
        if row['solver'] == 'ik':
            print(f"ik {row['solve_index']:2d} start_feasible={row.get('start', {}).get('feasible')} "
                  f"goals_feasible={sum(map(sum, row.get('goals_feasible', [])))}")
        else:
            for k, r in enumerate(row['rounds']):
                for kind, seeds in r['trajectories'].items():
                    bad = [s for s in seeds if not s['feasible']]
                    print(f"trajopt {row['solve_index']:2d} round {k} {kind:12s} infeasible={len(bad)}/{len(seeds)} "
                          f"start_bad={sum(s['start_bad'] for s in bad)} goal_bad={sum(s['goal_bad'] for s in bad)} "
                          f"terms={Counter(t for s in bad for t in s['bad_terms_any_step'])}")
    for g in rec.graph:
        print(f"graph check start_feasible={sum(g['start_feasible'])}/{g['pairs']} goal_feasible={sum(g['goal_feasible'])}/{g['pairs']}")
    for g in geometry:
        for p in g['penetrations']:
            print(f"lane {g['lane']} step {g['step']} {p['term']} {p['obstacle_geom'][-40:]} spheres="
                  f"{[(s['link'], s['clearance_m']) for s in p['spheres']][:4]} physical={p['physical_distance_m']}")
        for u in g.get('unmatched_spheres', []):
            print(f"lane {g['lane']} step {g['step']} {u['term']} sphere {u['index']} {u['link']}: merged SDF planning/world "
                  f"{u['merged_planning_frame_sdf_m']}/{u['merged_world_frame_sdf_m']} nearest per-geom {u['nearest_per_geom_clearance_m'][:2]}")
    print('lanes', lanes)


if __name__ == '__main__':
    main()
