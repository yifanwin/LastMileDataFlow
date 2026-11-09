"""Explicit real val103/mjlab stepping and scripted-control smoke.

This is separate from unittest. Scripted actions pass through the identical
7D/DLS/servo/physics path as the Actor, without planner prechecks.
"""
from lastmile_dataflow.gpu import isolate_from_argv

isolate_from_argv()  # before any CUDA library loads

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import time

import numpy as np
import torch

from lastmile_dataflow.config import REPO_ROOT
from lastmile_dataflow.runtime.rl.environment import StationEnv
from lastmile_dataflow.workflows.rl_evaluation import reset_stations
from lastmile_dataflow.workflows.rl_training import graph_observation, seed_all
from lastmile_dataflow.policies.sac.agent import SAC, SACConfig
from lastmile_dataflow.policies.sac.replay import Replay, Transition


def upstream_smoke(args):
    from mjlab.envs import ManagerBasedRlEnv
    from mjlab.tasks.manipulation.config.yam.env_cfgs import yam_lift_cube_env_cfg
    cfg = yam_lift_cube_env_cfg(play=True)
    cfg.scene.num_envs = args.num_envs
    env = ManagerBasedRlEnv(cfg, device=f'cuda:{args.gpu}')
    try:
        env.reset()
        action = torch.zeros(env.num_envs, env.action_manager.total_action_dim, device=env.device)
        for _ in range(args.steps):
            env.step(action)
        assert torch.isfinite(env.sim.data.qpos).all()
        result = {'upstream': 'yam_lift_cube_env_cfg', 'gpu': args.physical_gpu,
                  'num_envs': env.num_envs, 'steps': args.steps, 'finite_qpos': True}
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        env.close()


PREGRASP, APPROACH, CLOSE, LIFT = range(4)
PHASES = ('pregrasp', 'approach', 'close', 'lift')


def scripted_action(env, config, phase, closed_at, step):
    '''Per-environment phases, so one vector batch can cover many stations.'''
    dp, dr = env.task.goal_error()
    pre_dp, pre_dr = env.task.goal_error(-.08)
    before = (phase == PREGRASP)[:, None]
    dp, dr = torch.where(before, pre_dp, dp), torch.where(before, pre_dr, dr)
    action = torch.zeros(env.num_envs, 7, device=env.device)
    action[:, :3] = (dp / config['action']['translation_m']).clamp(-1, 1)
    action[:, 3:6] = (dr / config['action']['rotation_rad']).clamp(-1, 1)
    action[:, 6] = 1
    position, rotation = dp.norm(dim=-1), dr.norm(dim=-1)
    at_pregrasp = (phase == PREGRASP) & (position < .01) & (rotation < .12)
    at_grasp = (phase == APPROACH) & (position < .008) & (rotation < .1)
    phase[at_pregrasp] = APPROACH
    phase[at_grasp] = CLOSE
    closed_at[at_grasp] = step
    closing = phase == CLOSE
    action[closing, 6] = -1
    phase[closing & (step - closed_at >= 20)] = LIFT
    lifting = phase == LIFT
    action[lifting] = 0
    action[lifting, 2] = (step - closed_at[lifting] < 40).float()
    action[lifting, 6] = -1
    return action


def collision_label(prepared, name):
    kind, _, index = name.rpartition('_')
    if not index.isdigit():
        return name
    source = {'environment': prepared.scene_spec.worldbody.bodies, 'environment_world': prepared.scene_spec.worldbody.geoms,
              'nonfinger': prepared.robot_spec.bodies}[kind]
    return f'{kind}:{source[int(index)].name}'


def scripted_batch(env, config, args):
    '''Report which physically valid stations the same 7D/DLS path can pick from.'''
    candidates = [i for i, s in enumerate(env.task.stations) if s['valid'] and s['split'] == args.split]
    chosen = np.random.default_rng(args.station_seed).choice(candidates, min(env.num_envs, len(candidates)), replace=False).tolist()
    chosen += chosen[:1] * (env.num_envs - len(chosen))
    warmup(env, torch.tensor(chosen, device=env.device), args.warmup_steps)
    phase = torch.zeros(env.num_envs, dtype=torch.long, device=env.device)
    closed_at = torch.zeros_like(phase)
    rows, finished = [None] * env.num_envs, torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
    best_position = torch.full((env.num_envs,), float('inf'), device=env.device)
    start = time.perf_counter()
    for step in range(env.max_episode_length):
        action = scripted_action(env, config, phase, closed_at, step)
        action[finished] = 0
        _, _, terminated, truncated, _ = env.step(action)
        best_position = torch.minimum(best_position, env.task.goal_error()[0].norm(dim=-1))
        done = (terminated | truncated) & ~finished
        for i in done.nonzero(as_tuple=True)[0].tolist():
            hits = env.task.collision_hits[i]
            station = env.task.stations[chosen[i]]
            rows[i] = {**station, 'success': bool(env.task.success[i]), 'failed': bool(env.task.failed[i]),
                       'control_failed': bool(env.task.control_failed[i]), 'unhealthy': bool(env.task.unhealthy[i]),
                       'collision_free': bool(env.task.collision_free[i]), 'phase': PHASES[int(phase[i])], 'steps': step + 1,
                       'best_position_error_m': float(best_position[i]), 'max_drift_m': float(env.task.max_drift[i]),
                       'max_yaw_drift_rad': float(env.task.max_yaw_drift[i]), 'max_lift_drift_m': float(env.task.max_lift_drift[i]),
                       'collision_time_s': float(env.task.collision_time[i]), 'collisions': {
                           collision_label(env.prepared, env.task.collision_names[j]): float(hits[j])
                           for j in hits.nonzero(as_tuple=True)[0].tolist()}}
        finished |= done
        if finished.all():
            break
        # mjlab requires ended slots to reset; finished slots idle until the batch ends.
        ended = (terminated | truncated).nonzero(as_tuple=True)[0]
        if len(ended):
            reset_stations(env, [chosen[i] for i in ended.tolist()], env_ids=ended)
    healthy = [r for r in rows if not r['unhealthy']]
    sensors = Counter(name for r in healthy for name in r['collisions'])
    summary = {'gpu': args.physical_gpu, 'split': args.split, 'num_envs': env.num_envs, 'seconds': time.perf_counter() - start,
               'vector_steps': step + 1, 'healthy': len(healthy), 'success': sum(r['success'] for r in healthy),
               'collision_free_success': sum(r['success'] and r['collision_free'] for r in healthy),
               'collision_free': sum(r['collision_free'] for r in healthy),
               'final_phase': dict(Counter(r['phase'] for r in healthy)),
               'control_failed': sum(r['control_failed'] for r in healthy),
               'success_yaw_bins': dict(Counter(r['station_id'][-3:] for r in healthy if r['success'])),
               'trial_yaw_bins': dict(Counter(r['station_id'][-3:] for r in healthy)),
               'collision_sensors': sensors.most_common(30), 'overflow_bits': env.sim.data.overflow.cpu().tolist()[:8],
               'peak_memory_bytes': torch.cuda.max_memory_allocated(), 'rows': rows}
    Path(args.output).write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != 'rows'}, indent=2))


def warmup(env, station_ids, steps):
    action = torch.zeros(env.num_envs, 7, device=env.device)
    for _ in range(steps):
        _, _, terminated, truncated, _ = env.step(action)
        if (terminated | truncated).any():
            reset_stations(env, station_ids)
    return reset_stations(env, station_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(REPO_ROOT / 'configs/rl/val103_cup30.json'))
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--num-envs', type=int, default=1)
    parser.add_argument('--steps', type=int, default=5)
    parser.add_argument('--scripted', action='store_true')
    parser.add_argument('--upstream', action='store_true', help='run the shipped YAM lift-cube environment')
    parser.add_argument('--scripted-batch', action='store_true', help='scripted pick from one sampled station per environment')
    parser.add_argument('--split', default='train', choices=('train', 'validation'))
    parser.add_argument('--station-seed', type=int, default=42)
    parser.add_argument('--station-id')
    parser.add_argument('--approach-offset', type=float, help='diagnostic override of grasp.approach_offset_m')
    parser.add_argument('--grasp-row', type=int, help='diagnostic override of grasp.row')
    parser.add_argument('--warmup-steps', type=int, default=2)
    parser.add_argument('--random-actions', action='store_true')
    parser.add_argument('--learner-updates', type=int, default=0)
    parser.add_argument('--monitor', action='store_true', help='validate independent evaluation and all four metric figures')
    parser.add_argument('--output', default='/tmp/lastmile-rl-smoke.json')
    args = parser.parse_args()
    if args.monitor and args.learner_updates <= 0:
        parser.error('--monitor requires --learner-updates > 0')
    args.physical_gpu = os.environ.get('CUDA_VISIBLE_DEVICES')
    if args.upstream:
        upstream_smoke(args)
        return
    config = json.loads(Path(args.config).read_text())
    if args.approach_offset is not None:
        config['grasp']['approach_offset_m'] = args.approach_offset
    if args.grasp_row is not None:
        config['grasp']['row'] = args.grasp_row
    seed_all(42)
    env = StationEnv(config, gpu=args.gpu, seed=42, num_envs=args.num_envs)
    try:
        if args.scripted_batch:
            scripted_batch(env, config, args)
            return
        if args.station_id:
            index = next(i for i, s in enumerate(env.task.stations) if s['station_id'] == args.station_id and s['valid'])
            obs = reset_stations(env, [index] * env.num_envs)
        else:
            observations, _ = env.reset()
            obs = graph_observation(observations)
        station_ids = env.task.station_ids.clone()
        obs = warmup(env, station_ids, args.warmup_steps)
        initial_arm = env.task.robot.data.joint_pos[:, env.task.arm].clone()
        replay = Replay(max(args.steps * env.num_envs, 1), obs) if args.learner_updates else None
        torch.cuda.synchronize()
        start = time.perf_counter()
        completed = 0
        phase_ids = torch.zeros(env.num_envs, dtype=torch.long, device=env.device)
        closed_at = torch.zeros_like(phase_ids)
        trace = {name: [] for name in ('qpos', 'qvel', 'action', 'position_error', 'rotation_error',
                                       'joint_command', 'limit_fraction', 'forces', 'support', 'hold_s')}
        for step in range(env.max_episode_length if args.scripted else args.steps):
            action = torch.zeros(env.num_envs, 7, device=env.device)
            action[:, 6] = 1
            if args.random_actions:
                action = torch.rand_like(action) * 2 - 1
            if args.scripted:
                action = scripted_action(env, config, phase_ids, closed_at, step)
            observations, reward, terminated, truncated, _ = env.step(action)
            if args.scripted:
                controller = env.action_manager.get_term('tcp')
                position_error, rotation_error = env.task.goal_error()
                for name, value in (
                    ('qpos', env.sim.data.qpos), ('qvel', env.sim.data.qvel), ('action', action),
                    ('position_error', position_error), ('rotation_error', rotation_error),
                    ('joint_command', controller.q_command), ('limit_fraction', controller.limit_fraction),
                    ('forces', env.task.forces), ('support', env.task.support), ('hold_s', env.task.hold)):
                    trace[name].append(value[0].detach().cpu().numpy().copy())
            next_obs = graph_observation(observations)
            if replay is not None:
                healthy = ~env.task.unhealthy
                replay.add(Transition(obs[healthy], action[healthy], reward[healthy], next_obs[healthy],
                                      (terminated | truncated)[healthy], torch.zeros_like(truncated[healthy]),
                                      env.task.reward_components[healthy], env.task.station_ids[healthy]))
            obs = next_obs.clone()
            assert torch.isfinite(obs.nodes).all() and torch.isfinite(obs.arm_task_state).all()
            assert torch.isfinite(reward).all()
            assert not env.task.unhealthy.any(), 'physics overflow or nonfinite state'
            completed += env.num_envs
            done = terminated | truncated
            if done.any():
                break
        torch.cuda.synchronize()
        summary = {'gpu': args.physical_gpu, 'num_envs': env.num_envs, 'transitions': completed,
                   'seconds': time.perf_counter() - start, 'node_capacity': env.task.graph.capacity,
                   'state_dim': obs.arm_task_state.shape[-1], 'valid_stations': sum(s['valid'] for s in env.task.stations),
                   'valid_splits': dict(Counter(s['split'] for s in env.task.stations if s['valid'])),
                   'selected_stations': [env.task.stations[int(i)] for i in station_ids],
                   'success': env.task.success.cpu().tolist(), 'failed': env.task.failed.cpu().tolist(),
                   'max_drift_m': env.task.max_drift.cpu().tolist(), 'hold_s': env.task.hold.cpu().tolist(),
                   'max_yaw_drift_rad': env.task.max_yaw_drift.cpu().tolist(),
                   'max_lift_drift_m': env.task.max_lift_drift.cpu().tolist(),
                   'arm_displacement_rad': (env.task.robot.data.joint_pos[:, env.task.arm] - initial_arm).norm(dim=-1).cpu().tolist(),
                   'finger_forces_n': env.task.forces.cpu().tolist(),
                   'support': env.task.support.cpu().tolist(),
                   'overflow_bits': env.sim.data.overflow.cpu().tolist(),
                   'lift_m': (env.sim.data.xpos[:, env.task.target, 2] - env.task.height_reference).cpu().tolist(),
                   'collision_free': env.task.collision_free.cpu().tolist(),
                   'peak_memory_bytes': torch.cuda.max_memory_allocated(),
                   'scripted': args.scripted, 'phase': PHASES[int(phase_ids[0])] if args.scripted else 'sampling'}
        position_error, rotation_error = env.task.goal_error()
        summary['position_error_m'] = position_error.norm(dim=-1).cpu().tolist()
        summary['rotation_error_rad'] = rotation_error.norm(dim=-1).cpu().tolist()
        if args.scripted:
            replay_path = Path(args.output).with_name(Path(args.output).stem + '-control.npz')
            np.savez_compressed(replay_path, **{name: np.asarray(values) for name, values in trace.items()})
            summary['control_trace'] = str(replay_path)
        if replay is not None:
            agent = SAC(obs.arm_task_state.shape[-1], env.device, SACConfig(**config['sac']))
            for _ in range(args.learner_updates):
                losses = agent.update(replay.sample(min(32, replay.size)))
            summary['learner_updates'] = agent.updates
            summary['learner_losses'] = {name: float(value) for name, value in losses.items()}
        # Exercise local reset without replacing ongoing observations.
        if not args.scripted:
            before = env.sim.data.qpos.clone()
            caches = {name: getattr(env.task, name).clone() for name in
                      ('elapsed', 'window_start', 'last_action', 'reference_position', 'fixed_reference')}
            ids = torch.unique(torch.cat((done.nonzero(as_tuple=True)[0], torch.tensor([0], device=env.device))))
            ongoing = torch.ones(env.num_envs, dtype=torch.bool, device=env.device)
            ongoing[ids] = False
            env.reset(env_ids=ids)
            torch.testing.assert_close(before[ongoing], env.sim.data.qpos[ongoing], rtol=0, atol=0)
            for name, cached in caches.items():
                torch.testing.assert_close(cached[ongoing], getattr(env.task, name)[ongoing], rtol=0, atol=0)
            summary['partial_reset_preserved_other_qpos'] = True
            summary['partial_reset_ongoing_envs_checked'] = int(ongoing.sum())
        if args.monitor:
            from lastmile_dataflow.workflows.rl_evaluation import monitor_validation
            from lastmile_dataflow.data.visualization.sac_monitor import training_curves
            output_dir = Path(args.output).parent
            event = monitor_validation(agent, env, config, completed, output_dir)
            rows = json.loads((output_dir / 'analysis' / f'validation-{completed:012d}.json').read_text())
            with (output_dir / 'metrics.jsonl').open('w') as stream:
                stream.write(json.dumps({'event': 'learner', 'transitions': completed, **summary['learner_losses']}) + '\n')
                stream.write(json.dumps(event) + '\n')
                for trial in rows['trials']:
                    stream.write(json.dumps({'event': 'episode', 'transitions': completed,
                                              'return': trial['episode_return'],
                                              'infrastructure_failure': trial['infrastructure_failure']}) + '\n')
            training_curves(output_dir / 'metrics.jsonl', output_dir / 'views')
            summary['monitor'] = event
            summary['monitor_interpretation'] = 'untrained software smoke with diagnostic updates, not a training result'
        Path(args.output).write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        env.close()


if __name__ == '__main__':
    main()
