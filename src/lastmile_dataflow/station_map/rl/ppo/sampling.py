"""Synchronous rollout workers; every collection command fixes Actor and V."""

import io
import math
import multiprocessing as mp
import pickle

import torch

from ..checkpoint import seed_all
from ..training_common import record_episodes
from ..devices import VISIBLE_DEVICE, ppo_sampler_entry
from ..env.observation import CONTACT_REWARD_COMPONENTS, REWARD_COMPONENTS, policy_observation
from ..networks import Actor, Value
from ..config import PPOConfig
from .rollout import RolloutBatch, cat_observations, gae, map_observation


def sample(config, seed, num_envs, channel):
    from ..env.environment import StationEnv

    seed_all(seed)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=num_envs)
    try:
        observations, _ = env.reset()
        mode = config.get("observation", {}).get("mode", "graph")
        obs = policy_observation(observations, mode)
        dim = obs.shape[-1] if mode == "flat" else obs.arm_task_state.shape[-1]
        actor, value = Actor(dim, mode=mode).to(env.device), Value(dim, mode=mode).to(env.device)
        cfg = PPOConfig(**config.get("ppo", {}))
        returns = torch.zeros(env.num_envs, device=env.device)
        names = CONTACT_REWARD_COMPONENTS if mode == "flat" else REWARD_COMPONENTS
        components = torch.zeros(env.num_envs, len(names), device=env.device)
        episode_steps = torch.zeros(env.num_envs, device=env.device)
        channel.send_bytes(pickle.dumps(("ready", env.num_envs)))
        while True:
            command, payload = pickle.loads(channel.recv_bytes())
            if command == "close":
                break
            version, actor_state, value_state, quota = payload
            actor.load_state_dict(actor_state)
            value.load_state_dict(value_state)
            observations_saved, raws, logs, values, rewards, next_values, terminals, dones, masks = ([] for _ in range(9))
            episodes = io.StringIO()
            used = 0
            with torch.no_grad():
                for _ in range(min(cfg.rollout_steps, math.ceil(quota / env.num_envs))):
                    observations_saved.append(map_observation(obs, lambda x: x.detach().clone()))
                    raw, old_log = actor.sample_raw(obs)
                    old_value = value(obs)
                    observations, reward, terminated, truncated, _ = env.step(raw.tanh())
                    nxt = policy_observation(observations, mode)
                    done = terminated | truncated | env.task.unhealthy
                    valid = ~env.task.unhealthy
                    ids = valid.nonzero(as_tuple=True)[0]
                    valid[ids[max(0, quota - used):]] = False
                    used += int(valid.sum())
                    raws.append(raw)
                    logs.append(old_log)
                    values.append(old_value)
                    rewards.append(reward.clone())
                    next_values.append(value(nxt))
                    terminals.append((terminated | env.task.unhealthy).clone())
                    dones.append(done.clone())
                    masks.append(valid.clone())
                    returns += reward
                    components += env.task.reward_components
                    episode_steps += 1
                    record_episodes(episodes, env, done & (valid | env.task.unhealthy), 0, returns, components, episode_steps)
                    returns[done] = 0
                    components[done] = 0
                    episode_steps[done] = 0
                    obs = nxt.clone()
                    if done.any():
                        ids = done.nonzero(as_tuple=True)[0]
                        reset, _ = env.reset(env_ids=ids)
                        reset_obs = policy_observation(reset, mode)
                        if mode == "flat":
                            obs[ids] = reset_obs[ids] if len(reset_obs) == env.num_envs else reset_obs
                        else:
                            obs.replace(ids, reset_obs)
                mask = torch.stack(masks)
                advantages, targets = gae(
                    torch.stack(rewards), torch.stack(values), torch.stack(next_values),
                    torch.stack(terminals), torch.stack(dones), mask, cfg.gamma, cfg.gae_lambda,
                )
                flat_mask = mask.flatten()
                batch = RolloutBatch(
                    cat_observations(observations_saved)[flat_mask],
                    torch.cat(raws)[flat_mask], torch.cat(logs)[flat_mask], torch.cat(values)[flat_mask],
                    advantages.flatten()[flat_mask], targets.flatten()[flat_mask],
                ).to("cpu")
            channel.send_bytes(pickle.dumps((version, batch, episodes.getvalue())))
    finally:
        env.close()
        channel.close()


class SynchronousSamplers:
    """One fixed worker per GPU and a single collection barrier per rollout."""

    def __init__(self, config, devices, seed, num_envs):
        self.channels, self.processes = [], []
        self.num_envs = num_envs or config["environment"]["num_envs"]
        self.steps = PPOConfig(**config.get("ppo", {})).rollout_steps
        ctx = mp.get_context("spawn")
        for index, device in enumerate(devices):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=ppo_sampler_entry, args=(device, config, seed + index, num_envs, child))
            process.start()
            child.close()
            self.channels.append(parent)
            self.processes.append(process)

    def receive(self, index):
        channel, process = self.channels[index], self.processes[index]
        while not channel.poll(0.2):
            if process.exitcode is not None:
                raise RuntimeError(f"PPO sampler {index} exited: {process.exitcode}")
        return pickle.loads(channel.recv_bytes())

    def ready(self):
        for index in range(len(self.channels)):
            if self.receive(index) != ("ready", self.num_envs):
                raise RuntimeError("Unexpected PPO sampler initialization")

    def collect(self, agent, remaining):
        states = [{k: v.detach().cpu().clone() for k, v in network.state_dict().items()}
                  for network in (agent.actor, agent.value)]
        active = []
        for index, channel in enumerate(self.channels):
            quota = min(self.steps * self.num_envs, remaining)
            if quota:
                channel.send_bytes(pickle.dumps(("collect", (agent.updates, *states, quota))))
                active.append(index)
                remaining -= quota
        results = []
        for index in active:
            version, batch, episodes = self.receive(index)
            if version != agent.updates:
                raise RuntimeError("PPO rollout policy version mismatch")
            results.append((index, batch, episodes))
        return results

    def close(self):
        for channel, process in zip(self.channels, self.processes):
            if process.is_alive():
                channel.send_bytes(pickle.dumps(("close", None)))
        for process in self.processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        for channel in self.channels:
            channel.close()
