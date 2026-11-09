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
    revisions = {'project': os.environ.get('LASTMILE_SOURCE_REVISION') or subprocess.check_output(
        ['git', '-C', str(REPO_ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                 'mjlab': subprocess.check_output(['git', '-C', config['mjlab_source'], 'rev-parse', 'HEAD'], text=True).strip()}
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


def record_episodes(log, env, done, transitions, returns, components, steps):
    for index in done.nonzero(as_tuple=True)[0].tolist():
        log.write(json.dumps({'event': 'episode', 'transitions': transitions,
                              'station_id': env.task.stations[int(env.task.station_ids[index])]['station_id'],
                              'return': float(returns[index]), 'steps': int(steps[index]),
                              'reward_components': components[index].cpu().tolist(),
                              'success': bool(env.task.success[index]),
                              'collision_free': bool(env.task.collision_free[index]),
                              'collision_time_s': float(env.task.collision_time[index]),
                              'max_drift_m': float(env.task.max_drift[index]),
                              'max_yaw_drift_rad': float(env.task.max_yaw_drift[index]),
                              'max_lift_drift_m': float(env.task.max_lift_drift[index]),
                              'infrastructure_failure': bool(env.task.unhealthy[index])}) + '\n')
    returns[done] = 0
    components[done] = 0
    steps[done] = 0


def validation_checkpoint(agent, env, config, transitions, output, log, metadata, best_score):
    from .rl_evaluation import monitor_validation
    from ..data.visualization.sac_monitor import training_curves
    started = time.perf_counter()
    event = monitor_validation(agent, env, config, transitions, output)
    event['seconds'] = time.perf_counter() - started
    log.write(json.dumps(event) + '\n')
    log.flush()
    if not event['healthy_trials']:
        raise RuntimeError('Independent validation produced no healthy trials')
    score = (event['evaluation_success_rate'], event['episode_return_mean'])
    if best_score is None or score > tuple(best_score):
        best_score = score
        metadata['best_validation_score'] = list(score)
        agent.save(Path(output) / 'best.pt', metadata=metadata, transitions=transitions)
    agent.save(Path(output) / 'latest.pt', metadata=metadata, transitions=transitions)
    training_curves(Path(output) / 'metrics.jsonl', Path(output) / 'views')
    return best_score


def train(config, *, gpu, output, seed=42, resume=None, max_transitions=None, num_envs=None):
    from ..runtime.rl.environment import StationEnv
    from ..data.visualization.sac_monitor import training_curves
    seed_all(seed)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    env = StationEnv(config, gpu=gpu, seed=seed, num_envs=num_envs)
    evaluation_env = None
    try:
        settings = config['training']
        evaluation_env = StationEnv(config, gpu=gpu, seed=config['evaluation']['seed'],
                                    num_envs=settings['validation_stations'])
        seed_all(seed)
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
            best_score = saved['metadata'].get('best_validation_score')
        else:
            agent = SAC(state_dim, env.device, SACConfig(**config['sac']))
            transitions = 0
            best_score = None
            agent.save(output / 'initial.pt', metadata=metadata, transitions=0)
        metadata['best_validation_score'] = best_score
        replay = Replay(settings['replay_capacity'], obs)
        warmup = max(settings['warmup_min'], env.num_envs * env.max_episode_length)
        limit = max_transitions or settings['max_transitions']
        collected = 0
        episode_return = torch.zeros(env.num_envs, device=env.device)
        episode_components = torch.zeros(env.num_envs, 6, device=env.device)
        steps = torch.zeros(env.num_envs, device=env.device)
        started = time.perf_counter()
        log_at = transitions + settings['log_every']
        evaluate_at = transitions + settings['eval_every']
        loss_sums = {}
        loss_count = 0
        last_evaluation = None
        with (output / 'metrics.jsonl').open('a') as log:
            best_score = validation_checkpoint(agent, evaluation_env, config, transitions, output, log, metadata, best_score)
            last_evaluation = transitions
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
                                          'reason': 'all_environments_infrastructure_failure'}) + '\n')
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
                record_episodes(log, env, done, transitions, episode_return, episode_components, steps)
                # Replay already owns terminal state. Reset returns the whole batch;
                # replace only done rows, preserving ongoing environments' history.
                obs = next_obs.clone()
                if done.any():
                    ids = done.nonzero(as_tuple=True)[0]
                    reset_obs, _ = env.reset(env_ids=ids)
                    obs.replace(ids, graph_observation(reset_obs))
                if collected >= warmup and replay.size >= settings['batch_size']:
                    for _ in range(settings['updates_per_step']):
                        losses = agent.update(replay.sample(settings['batch_size']))
                        loss_count += 1
                        for name, value in losses.items():
                            if name not in loss_sums:
                                loss_sums[name] = torch.zeros_like(value)
                            loss_sums[name] += value
                if transitions >= log_at:
                    log.write(json.dumps({'event': 'learner', 'transitions': transitions,
                                          'updates': agent.updates, 'replay_size': replay.size,
                                          'transitions_per_s': collected / (time.perf_counter() - started),
                                          'updates_per_transition': settings['updates_per_step'] / env.num_envs,
                                          **{name: float(value / loss_count) for name, value in loss_sums.items()}}) + '\n')
                    log.flush()
                    agent.save(output / 'latest.pt', metadata=metadata, transitions=transitions)
                    training_curves(output / 'metrics.jsonl', output / 'views')
                    for value in loss_sums.values():
                        value.zero_()
                    loss_count = 0
                    log_at += settings['log_every']
                if transitions >= evaluate_at:
                    best_score = validation_checkpoint(agent, evaluation_env, config, transitions, output, log, metadata, best_score)
                    last_evaluation = transitions
                    evaluate_at += settings['eval_every']
            if last_evaluation != transitions:
                validation_checkpoint(agent, evaluation_env, config, transitions, output, log, metadata, best_score)
        agent.save(output / 'latest.pt', metadata=metadata, transitions=transitions)
        return {'transitions': transitions, 'updates': agent.updates, 'checkpoint': str(output / 'latest.pt')}
    finally:
        if evaluation_env is not None:
            evaluation_env.close()
        env.close()
