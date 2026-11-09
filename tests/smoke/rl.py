"""Explicit real val103/mjlab stepping and scripted-control smoke.

This is separate from unittest. Scripted actions pass through the identical
7D/DLS/servo/physics path as the Actor, without planner prechecks.
"""
import argparse
from collections import Counter
import json
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
        result = {'upstream': 'yam_lift_cube_env_cfg', 'gpu': args.gpu,
                  'num_envs': env.num_envs, 'steps': args.steps, 'finite_qpos': True}
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        env.close()


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
    parser.add_argument('--station-id')
    parser.add_argument('--warmup-steps', type=int, default=2)
    parser.add_argument('--random-actions', action='store_true')
    parser.add_argument('--learner-updates', type=int, default=0)
    parser.add_argument('--output', default='/tmp/lastmile-rl-smoke.json')
    args = parser.parse_args()
    if args.upstream:
        upstream_smoke(args)
        return
    config = json.loads(Path(args.config).read_text())
    seed_all(42)
    env = StationEnv(config, gpu=args.gpu, seed=42, num_envs=args.num_envs)
    try:
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
        phase = 'approach'
        closed_at = 0
        trace = {name: [] for name in ('qpos', 'qvel', 'action', 'position_error', 'rotation_error',
                                       'joint_command', 'limit_fraction', 'forces', 'support', 'hold_s')}
        for step in range(env.max_episode_length if args.scripted else args.steps):
            action = torch.zeros(env.num_envs, 7, device=env.device)
            action[:, 6] = 1
            if args.random_actions:
                action = torch.rand_like(action) * 2 - 1
            if args.scripted:
                dp, dr = env.task.goal_error()
                action[:, :3] = (dp / config['action']['translation_m']).clamp(-1, 1)
                action[:, 3:6] = (dr / config['action']['rotation_rad']).clamp(-1, 1)
                if phase == 'approach' and bool((dp.norm(dim=-1) < .008).all() & (dr.norm(dim=-1) < .1).all()):
                    phase, closed_at = 'close', step
                if phase == 'close':
                    action[:, 6] = -1
                    if step - closed_at >= 20:
                        phase = 'lift'
                if phase == 'lift':
                    action[:, :3] = 0
                    action[:, 2] = 1 if step - closed_at < 40 else 0
                    action[:, 3:6] = 0
                    action[:, 6] = -1
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
        summary = {'gpu': args.gpu, 'num_envs': env.num_envs, 'transitions': completed,
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
                   'scripted': args.scripted, 'phase': phase}
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
        Path(args.output).write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        env.close()


if __name__ == '__main__':
    main()
