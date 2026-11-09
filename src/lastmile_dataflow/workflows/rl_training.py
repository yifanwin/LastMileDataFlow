"""Off-policy vector sampling with explicit final observations and local reset."""
import json
import importlib.metadata
import os
from pathlib import Path
import random
import subprocess
import time

import numpy as np
import torch

from ..policies.sac.agent import SAC, SACConfig
from ..policies.sac.replay import Replay, Transition
from ..scenes.rl_observation import ARM_STATE_FIELDS, GraphObservation


def graph_observation(observations):
    return GraphObservation(**observations['policy'])


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def runtime_metadata(config, gpu):
    from ..config import REPO_ROOT
    visible = os.environ.get('CUDA_VISIBLE_DEVICES')
    revisions = {name: subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
                 for name, path in (('project', REPO_ROOT), ('mjlab', config['mjlab_source']))}
    return {'revisions': revisions,
            'versions': {name: importlib.metadata.version(name) for name in
                         ('torch', 'torch-geometric', 'mjlab', 'mujoco', 'mujoco-warp', 'warp-lang', 'numpy')},
            'gpu_argument': gpu, 'cuda_visible_devices': visible,
            'physical_gpu': visible.split(',')[gpu] if visible else gpu}


def run_cli(mode):
    import argparse
    from ..config import REPO_ROOT
    parser = argparse.ArgumentParser(description=f'{mode}: fixed-base mjlab SAC research')
    parser.add_argument('--config', default=str(REPO_ROOT / 'configs/rl/val103_cup30.json'))
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', type=int, default=None)
    if mode == 'train':
        parser.add_argument('--resume')
        parser.add_argument('--max-transitions', type=int)
        parser.add_argument('--num-envs', type=int)
    else:
        parser.add_argument('--checkpoint', required=True)
        if mode == 'evaluate':
            parser.add_argument('--scores', required=True)
            parser.add_argument('--baseline', action='store_true', help='evaluate initial.pt on the trained score selection')
    args = vars(parser.parse_args())
    config = json.loads(Path(args.pop('config')).read_text())
    if mode == 'train':
        args['seed'] = 42 if args['seed'] is None else args['seed']
        result = train(config, **args)
    else:
        from .rl_evaluation import evaluate, score
        result = (evaluate if mode == 'evaluate' else score)(config, **args)
    print(json.dumps(result, indent=2))


def train(config, *, gpu, output, seed=42, resume=None, max_transitions=None, num_envs=None):
    from ..runtime.rl.environment import StationEnv
    seed_all(seed)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    env = StationEnv(config, gpu=gpu, seed=seed, num_envs=num_envs)
    try:
        observations, _ = env.reset()
        obs = graph_observation(observations)
        state_dim = obs.arm_task_state.shape[-1]
        metadata = {'config': config, 'seed': seed, **runtime_metadata(config, gpu),
                    'node_capacity': obs.nodes.shape[1], 'state_dim': state_dim,
                    'arm_state_fields': ARM_STATE_FIELDS,
                    'node_fields': 'type4,center3,size3,rotation6,distance1,bottom1,linear3,angular3/5',
                    'stations': env.task.stations,
                    'resume': 'parameters/optimizers; fresh environment and replay'}
        if resume:
            agent, saved = SAC.load(resume, env.device, restore_rng=True)
            if saved['metadata']['config'] != config:
                raise ValueError('Resume requires the checkpoint effective configuration')
            transitions = saved['transitions']
        else:
            agent = SAC(state_dim, env.device, SACConfig(**config['sac']))
            transitions = 0
            agent.save(output / 'initial.pt', metadata=metadata, transitions=0)
        settings = config['training']
        replay = Replay(settings['replay_capacity'], obs)
        warmup = max(settings['warmup_min'], env.num_envs * env.max_episode_length)
        limit = max_transitions or settings['max_transitions']
        collected = 0
        episode_return = torch.zeros(env.num_envs, device=env.device)
        episode_components = torch.zeros(env.num_envs, 6, device=env.device)
        steps = torch.zeros(env.num_envs, device=env.device)
        started = time.perf_counter()
        log_at = transitions + settings['log_every']
        with (output / 'metrics.jsonl').open('a') as log:
            while transitions < limit:
                with torch.no_grad():
                    action = (torch.rand(env.num_envs, 7, device=env.device) * 2 - 1
                              if collected < warmup else agent.actor.sample(obs)[0])
                result, reward, terminated, truncated, _ = env.step(action)
                next_obs = graph_observation(result)
                # Twenty seconds is a task boundary, not an infinite-horizon cutoff.
                terminated = terminated | truncated
                truncated = torch.zeros_like(truncated)
                healthy = ~env.task.unhealthy
                if not healthy.any():
                    log.write(json.dumps({'event': 'infrastructure_failure', 'transitions': transitions,
                                          'env_ids': (~healthy).nonzero(as_tuple=True)[0].cpu().tolist(),
                                          'reason': 'all_environments_nonfinite'}) + '\n')
                    log.flush()
                    raise RuntimeError('All environments produced infrastructure failures')
                replay.add(Transition(obs[healthy], action[healthy], reward[healthy], next_obs[healthy],
                                      terminated[healthy], truncated[healthy],
                                      env.task.reward_components[healthy], env.task.station_ids[healthy]))
                count = int(healthy.sum())
                transitions += count
                collected += count
                episode_return += reward
                episode_components += env.task.reward_components
                steps += 1
                done = terminated | env.task.unhealthy
                for index in done.nonzero(as_tuple=True)[0].tolist():
                    log.write(json.dumps({'event': 'episode', 'transitions': transitions,
                                          'station_id': env.task.stations[int(env.task.station_ids[index])]['station_id'],
                                          'return': float(episode_return[index]), 'steps': int(steps[index]),
                                          'reward_components': episode_components[index].cpu().tolist(),
                                          'success': bool(env.task.success[index]),
                                          'collision_free': bool(env.task.collision_free[index]),
                                          'collision_time_s': float(env.task.collision_time[index]),
                                          'max_drift_m': float(env.task.max_drift[index]),
                                          'max_yaw_drift_rad': float(env.task.max_yaw_drift[index]),
                                          'max_lift_drift_m': float(env.task.max_lift_drift[index]),
                                          'infrastructure_failure': bool(env.task.unhealthy[index])}) + '\n')
                # Replay already owns terminal state. Reset returns the whole batch;
                # replace only done rows, preserving ongoing environments' history.
                obs = next_obs.clone()
                if done.any():
                    ids = done.nonzero(as_tuple=True)[0]
                    reset_obs, _ = env.reset(env_ids=ids)
                    obs.replace(ids, graph_observation(reset_obs))
                    episode_return[ids] = 0
                    episode_components[ids] = 0
                    steps[ids] = 0
                losses = {}
                if collected >= warmup and replay.size >= settings['batch_size']:
                    losses = agent.update(replay.sample(settings['batch_size']))
                if transitions >= log_at:
                    log.write(json.dumps({'event': 'learner', 'transitions': transitions,
                                          'updates': agent.updates, 'replay_size': replay.size,
                                          'transitions_per_s': collected / (time.perf_counter() - started),
                                          'updates_per_transition': 1 / env.num_envs,
                                          **{name: float(value) for name, value in losses.items()}}) + '\n')
                    log.flush()
                    agent.save(output / 'latest.pt', metadata=metadata, transitions=transitions)
                    log_at += settings['log_every']
        agent.save(output / 'latest.pt', metadata=metadata, transitions=transitions)
        return {'transitions': transitions, 'updates': agent.updates, 'checkpoint': str(output / 'latest.pt')}
    finally:
        env.close()
