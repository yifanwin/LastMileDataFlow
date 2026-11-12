"""Research dependencies are installed by scripts/install_rl.sh."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

import torch

from lastmile_dataflow.scenes.rl_observation import GraphObservation, graph_edges
from lastmile_dataflow.policies.sac.replay import Replay, Transition


def observation(batch=2):
    nodes = torch.zeros(batch, 5, 24)
    nodes[:, 2, 0] = 1  # Goal need not be first.
    nodes[:, 0, 1] = 1
    nodes[:, 4, 3] = 1
    nodes[:, :, 4:] = torch.randn(batch, 5, 20)
    mask = torch.tensor([[True, False, True, False, True]]).repeat(batch, 1)
    return GraphObservation(nodes, mask, torch.randn(batch, 9))


class ReplayTests(unittest.TestCase):
    def test_padding_goal_and_edge_direction(self):
        obs = observation()
        nodes, edges, attributes, goals = graph_edges(obs)
        self.assertEqual(goals.tolist(), [1, 4])
        self.assertEqual(edges[:, :4].tolist(), [[0, 2, 3, 5], [1, 1, 4, 4]])
        self.assertEqual(attributes[:4].argmax(-1).tolist(), [1, 3, 1, 3])
        self.assertEqual(len(nodes), 6)

    def test_replay_owns_terminal_observation_and_wraps(self):
        obs = observation()
        nxt = obs.clone()
        nxt.arm_task_state.fill_(17)
        replay = Replay(3, obs)
        transition = Transition(
            obs,
            torch.zeros(2, 7),
            torch.tensor([1.0, 2.0]),
            nxt,
            torch.tensor([False, True]),
            torch.tensor([True, False]),
        )
        replay.add(transition)
        nxt.arm_task_state.zero_()  # Simulate partial reset overwriting env buffers.
        obs.nodes.zero_()
        self.assertTrue(torch.all(replay.next_obs.arm_task_state[:2] == 17))
        self.assertTrue(torch.all(replay.obs.nodes[:2, 2, 0] == 1))
        replay.add(transition)
        self.assertEqual((replay.size, replay.cursor), (3, 1))


@unittest.skipUnless(importlib.util.find_spec("torch_geometric"), "requires RL dependency group")
class SACTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.previous_threads)

    def test_finite_horizon_and_external_truncation_target(self):
        from lastmile_dataflow.policies.sac.agent import bellman_target

        target = bellman_target(
            torch.ones(2), torch.tensor([True, False]), torch.full((2,), 3.0), torch.full((2,), -2.0), 0.1, 0.5
        )
        torch.testing.assert_close(target, torch.tensor([1.0, 2.6]))

    def test_goal_embedding_is_invariant_to_padding_and_node_order(self):
        from lastmile_dataflow.policies.sac.networks import GraphEncoder

        obs = observation()
        encoder = GraphEncoder().eval()
        permutation = torch.tensor([4, 3, 0, 1, 2])
        changed = GraphObservation(obs.nodes[:, permutation], obs.node_mask[:, permutation], obs.arm_task_state)
        torch.testing.assert_close(encoder(obs), encoder(changed))

    def test_update_independence_polyak_and_checkpoint(self):
        from lastmile_dataflow.policies.sac.agent import SAC

        torch.manual_seed(3)
        obs = observation()
        agent = SAC(9, "cpu")
        before = [p.clone() for p in agent.target1.parameters()]
        self.assertNotEqual(next(agent.q1.parameters()).data_ptr(), next(agent.q2.parameters()).data_ptr())
        batch = Transition(
            obs,
            torch.rand(2, 7) * 2 - 1,
            torch.ones(2),
            obs.clone(),
            torch.tensor([True, False]),
            torch.tensor([False, True]),
        )
        losses = agent.update(batch)
        self.assertTrue(all(torch.isfinite(value) for value in losses.values()))
        for old, target, online in zip(before, agent.target1.parameters(), agent.q1.parameters()):
            torch.testing.assert_close(target, old.lerp(online, agent.config.tau))
        self.assertTrue(any(p.grad is not None and p.grad.abs().sum() > 0 for p in agent.actor.parameters()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            agent.save(path, metadata={"test": True}, transitions=42)
            restored, saved = SAC.load(path, "cpu", restore_rng=True)
            torch.testing.assert_close(agent.actor.sample(obs, True)[0], restored.actor.sample(obs, True)[0])
            self.assertEqual(saved["transitions"], 42)
            self.assertEqual(restored.updates, 1)

    def test_saturated_tanh_log_probability_remains_finite(self):
        from lastmile_dataflow.policies.sac.networks import Actor

        actor = Actor(9)
        with torch.no_grad():
            actor.head[-1].weight.zero_()
            actor.head[-1].bias[:7].fill_(30)
        action, log_prob = actor.sample(observation())
        self.assertTrue(torch.isfinite(log_prob).all())
        self.assertTrue((action <= 1).all())
