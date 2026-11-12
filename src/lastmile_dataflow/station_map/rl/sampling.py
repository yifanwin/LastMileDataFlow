"""Actor-only sampling; CPU payloads and interruptible bounded-queue writes."""

import io
import pickle
from queue import Full, Empty
import time

import torch

from .sac.networks import Actor
from .sac.replay import Transition
from .training import record_episodes
from .sac.agent import seed_all
from .env.observation import graph_observation
from .env.observation import REWARD_COMPONENTS


def sample(config, seed, num_envs, worker, channel, actor_queue, queue, stop):
    from .env.environment import StationEnv

    seed_all(seed)
    env = StationEnv(config, gpu=0, seed=seed, num_envs=num_envs)
    try:
        observations, _ = env.reset()
        obs = graph_observation(observations)
        actor = Actor(obs.arm_task_state.shape[-1]).to(env.device)
        version, random_actions, paused = 0, True, False
        returns = torch.zeros(env.num_envs, device=env.device)
        components = torch.zeros(env.num_envs, len(REWARD_COMPONENTS), device=env.device)
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
                    torch.rand(env.num_envs, 7, device=env.device) * 2 - 1 if random_actions else actor.sample(obs)[0]
                )
                observations, reward, terminated, truncated, _ = env.step(action)
                nxt = graph_observation(observations)
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
                returns += reward
                components += env.task.reward_components
                steps += 1
                done = terminated | env.task.unhealthy
                episodes = io.StringIO()
                record_episodes(episodes, env, done, 0, returns, components, steps)
                obs = nxt.clone()
                if done.any():
                    ids = done.nonzero(as_tuple=True)[0]
                    reset, _ = env.reset(env_ids=ids)
                    obs.replace(ids, graph_observation(reset))
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
