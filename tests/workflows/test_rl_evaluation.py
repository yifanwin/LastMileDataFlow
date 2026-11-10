import importlib.util
from types import SimpleNamespace
import unittest

import torch


def observations(state):
    nodes = torch.zeros(2, 1, 24)
    nodes[:, :, 0] = 1
    return {'policy': {'nodes': nodes, 'node_mask': torch.ones(2, 1, dtype=torch.bool),
                       'arm_task_state': torch.tensor(state, dtype=torch.float32)[:, None]}}


class ObservationActor:
    def sample(self, observation, deterministic=False):
        return observation.arm_task_state.expand(-1, 7).clone(), torch.zeros(2)


class TwoEpisodeEnv:
    num_envs = 2
    device = 'cpu'

    def __init__(self):
        self.stage = 0
        self.pending = torch.zeros(2, dtype=torch.bool)
        self.actions = []
        self.task = SimpleNamespace(stations=[{'station_id': 'first'}, {'station_id': 'second'}],
                                    unhealthy=torch.zeros(2, dtype=torch.bool),
                                    success=torch.zeros(2, dtype=torch.bool),
                                    collision_free=torch.ones(2, dtype=torch.bool),
                                    collision_time=torch.zeros(2), max_drift=torch.zeros(2),
                                    max_yaw_drift=torch.zeros(2), max_lift_drift=torch.zeros(2),
                                    target=0, height_reference=torch.zeros(2), forces=torch.zeros(2, 2),
                                    hold=torch.zeros(2), goal_error=lambda: (torch.ones(2, 3), torch.ones(2, 3)))
        self.sim = SimpleNamespace(data=SimpleNamespace(xpos=torch.zeros(2, 1, 3)))

    def reset(self, env_ids=None):
        if env_ids is None:
            self.pending.zero_()
            return observations([1, 2]), {}
        self.pending[env_ids] = False
        return observations([-99, -99]), {}  # Whole-batch return is not ongoing history.

    def step(self, action):
        if self.pending.any():
            raise RuntimeError('manual reset required')
        self.actions.append(action.clone())
        self.stage += 1
        self.task.success.zero_()
        if self.stage == 1:
            reward, done = torch.tensor([2., 3.]), torch.tensor([True, False])
            self.task.success[0] = True
        else:
            reward, done = torch.tensor([100., 4.]), torch.tensor([False, True])
        self.pending[:] = done
        return observations([self.stage + 1, self.stage + 2]), reward, done, torch.zeros_like(done), {}


@unittest.skipUnless(importlib.util.find_spec('torch_geometric'), 'requires RL dependencies')
class ValidationTests(unittest.TestCase):
    def test_async_terminal_records_once_and_preserves_ongoing_observation(self):
        from lastmile_dataflow.workflows.rl_evaluation import validation_rollout
        env = TwoEpisodeEnv()
        rows = validation_rollout(SimpleNamespace(actor=ObservationActor()), env, [0, 1], 42, .5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['episode_return'], 2.)
        self.assertEqual(rows[1]['episode_return'], 7.)
        self.assertEqual(rows[1]['return'], 5.)
        self.assertTrue(rows[0]['success'])
        self.assertFalse(rows[1]['success'])
        self.assertEqual(float(env.actions[1][1, 0]), 3.)
        self.assertFalse(rows[0]["both_fingers"])
        self.assertAlmostEqual(rows[0]["best_position_error_m"], 3 ** .5, places=6)
