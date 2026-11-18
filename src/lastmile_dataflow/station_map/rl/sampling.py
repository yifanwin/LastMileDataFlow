"""Actor-only sampling; CPU payloads and interruptible bounded-queue writes."""

import io
import pickle
from queue import Full, Empty
import time

import torch

from .devices import VISIBLE_DEVICE
from .sac.networks import Actor
from .sac.replay import Transition
from .training import record_episodes
from .sac.agent import seed_all
from .env.observation import policy_observation
from .env.observation import REWARD_COMPONENTS, CONTACT_REWARD_COMPONENTS


def sample(config, seed, num_envs, worker, channel, actor_queue, queue, stop):
    from .env.environment import StationEnv

    seed_all(seed)
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=seed, num_envs=num_envs)
    try:
        observations, _ = env.reset()
        mode = config.get("observation", {}).get("mode", "graph")
        obs = policy_observation(observations, mode)
        state_dim = obs.shape[-1] if mode == "flat" else obs.arm_task_state.shape[-1]
        action_dim = env.action_manager.total_action_dim
        actor = Actor(state_dim, action_dim, mode=mode).to(env.device)
        version, random_actions, paused = 0, True, False
        random_episodes = torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
        returns = torch.zeros(env.num_envs, device=env.device)
        reward_names = CONTACT_REWARD_COMPONENTS if mode == "flat" else REWARD_COMPONENTS
        components = torch.zeros(env.num_envs, len(reward_names), device=env.device)
        steps = torch.zeros(env.num_envs, device=env.device)
        while not stop.is_set():
            try:
                version, random_actions, state = pickle.loads(actor_queue.get_nowait())
                actor.load_state_dict(state)
            except Empty:
                pass
            while channel.poll() or paused:
                if stop.is_set():
                    return
                if not channel.poll(0.1):
                    continue
                command, _ = channel.recv()
                if command == "pause":
                    paused = True
                    channel.send("paused")
                elif command == "resume":
                    paused = False
            with torch.no_grad():
                action = (
                    torch.rand(env.num_envs, action_dim, device=env.device) * 2 - 1 if random_actions else actor.sample(obs)[0]
                )
                observations, reward, terminated, truncated, _ = env.step(action)
                nxt = policy_observation(observations, mode)
                terminated = terminated | truncated
                healthy = ~env.task.unhealthy
                if not healthy.any():
                    raise RuntimeError("All sampler environments unhealthy")
                batch = Transition(
                    obs[healthy],
                    action[healthy],
                    reward[healthy],
                    nxt[healthy],
                    terminated[healthy],
                    torch.zeros_like(truncated[healthy]),
                    env.task.reward_components[healthy],
                    env.task.station_ids[healthy],
                ).to("cpu")
                random_episodes |= random_actions
                returns += reward
                components += env.task.reward_components
                steps += 1
                done = terminated | env.task.unhealthy
                episodes = io.StringIO()
                record_episodes(episodes, env, done, 0, returns, components, steps, random_episodes)
                obs = nxt.clone()
                if done.any():
                    ids = done.nonzero(as_tuple=True)[0]
                    random_episodes[ids] = False
                    reset, _ = env.reset(env_ids=ids)
                    reset_obs = policy_observation(reset, mode)
                    if mode == "flat":
                        obs[ids] = reset_obs[ids] if len(reset_obs) == env.num_envs else reset_obs
                    else:
                        obs.replace(ids, reset_obs)
            # Messages own their bytes: no producer tensor lifetime or CUDA IPC.
            for start in range(0, len(batch.reward), 256):
                payload = pickle.dumps(
                    (
                        worker,
                        version,
                        random_actions,
                        batch[start : start + 256],
                        episodes.getvalue() if start == 0 else "",
                    )
                )
                blocked = time.perf_counter()
                while not stop.is_set():
                    try:
                        queue.put((payload, time.perf_counter() - blocked), timeout=0.1)
                        break
                    except Full:
                        # Pause is acknowledged only after this finite step is delivered.
                        # Stop remains interruptible even with a full queue.
                        continue
    finally:
        env.close()
        queue.cancel_join_thread()
        channel.close()
