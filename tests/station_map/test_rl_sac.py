"""Research dependencies are installed by scripts/install/rl.sh."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

import torch

from lastmile_dataflow.station_map.rl.env.observation import GraphObservation, graph_edges, FLAT_FIELDS, CONTACT_REWARD_COMPONENTS
from lastmile_dataflow.station_map.rl.sac.replay import Replay, Transition


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
        from lastmile_dataflow.station_map.rl.sac.agent import bellman_target

        target = bellman_target(
            torch.ones(2), torch.tensor([True, False]), torch.full((2,), 3.0), torch.full((2,), -2.0), 0.1, 0.5
        )
        torch.testing.assert_close(target, torch.tensor([1.0, 2.6]))

    def test_goal_embedding_is_invariant_to_padding_and_node_order(self):
        from lastmile_dataflow.station_map.rl.networks import GraphEncoder

        obs = observation()
        encoder = GraphEncoder().eval()
        permutation = torch.tensor([4, 3, 0, 1, 2])
        changed = GraphObservation(obs.nodes[:, permutation], obs.node_mask[:, permutation], obs.arm_task_state)
        torch.testing.assert_close(encoder(obs), encoder(changed))

    def test_update_independence_polyak_and_checkpoint(self):
        from lastmile_dataflow.station_map.rl.sac.agent import SAC

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
        from lastmile_dataflow.station_map.rl.networks import Actor

        actor = Actor(9)
        with torch.no_grad():
            actor.head[-1].weight.zero_()
            actor.head[-1].bias[:7].fill_(30)
        action, log_prob = actor.sample(observation())
        self.assertTrue(torch.isfinite(log_prob).all())
        self.assertTrue((action <= 1).all())

    def test_flat_65d_replay_update_checkpoint_and_old_input_rejection(self):
        from lastmile_dataflow.station_map.rl.sac.agent import SAC

        dim = sum(size for _, size, _ in FLAT_FIELDS)
        obs = torch.randn(4, dim)
        components = torch.randn(4, len(CONTACT_REWARD_COMPONENTS))
        transition = Transition(obs, torch.zeros(4, 7), components.sum(-1), obs + 1,
                                torch.tensor([True, False, False, True]), torch.zeros(4, dtype=torch.bool), components.clone())
        replay = Replay(8, obs)
        replay.add(transition.to("cpu"))
        transition.reward_components.zero_()
        torch.testing.assert_close(replay.reward_components[:4], components)
        agent = SAC(dim, "cpu", mode="flat")
        losses = agent.update(replay.sample(4))
        self.assertTrue(all(torch.isfinite(value) for value in losses.values()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            agent.save(path, metadata={}, transitions=4)
            restored, _ = SAC.load(path, "cpu")
            torch.testing.assert_close(agent.actor.sample(obs, True)[0], restored.actor.sample(obs, True)[0])
            saved = torch.load(path, weights_only=False)
            old_scales = {"target_position_error_base": 1., "target_rotation_error_base": "pi"}
            old_fields = tuple((name, size, old_scales.get(name, scale)) for name, size, scale in FLAT_FIELDS)
            saved["input_definition"] = ("flat", old_fields)
            torch.save(saved, path)
            with self.assertRaisesRegex(ValueError, "observation definition"):
                SAC.load(path, "cpu")
            saved["state_dim"] = 45
            saved["input_definition"] = ("flat", (("old_contact_input", 45, 1.),))
            torch.save(saved, path)
            with self.assertRaisesRegex(ValueError, "observation definition"):
                SAC.load(path, "cpu")

    def test_flat_69d_update_and_checkpoint_records_world_fields(self):
        from lastmile_dataflow.station_map.rl.env.observation import WORLD_STATE_FIELDS
        from lastmile_dataflow.station_map.rl.sac.agent import SAC

        agent = SAC(69, "cpu", mode="flat")
        obs = torch.randn(4, 69)
        transition = Transition(obs, torch.zeros(4, 7), torch.ones(4), obs + 0.1,
                                torch.zeros(4, dtype=torch.bool), torch.zeros(4, dtype=torch.bool))
        self.assertTrue(all(torch.isfinite(v) for v in agent.update(transition).values()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            agent.save(path, metadata={}, transitions=4)
            restored, saved = SAC.load(path, "cpu")
            self.assertEqual(saved["input_definition"], ("flat", FLAT_FIELDS + WORLD_STATE_FIELDS))
            torch.testing.assert_close(agent.actor.sample(obs, True)[0], restored.actor.sample(obs, True)[0])


@unittest.skipUnless(importlib.util.find_spec("torch_geometric"), "requires RL dependency group")
class ActorNoiseTests(unittest.TestCase):
    def test_supplied_noise_is_batched_and_keeps_sampling_contract(self):
        from lastmile_dataflow.station_map.rl.networks import Actor

        torch.manual_seed(5)
        actor = Actor(9)
        obs = observation()
        noise = torch.randn(2, 7)
        action, log_prob = actor.sample(obs, noise=noise)
        mu, log_std = actor(obs)
        torch.testing.assert_close(action, (mu + log_std.exp() * noise).tanh())
        single, _ = actor.sample(obs[1:2], noise=noise[1:2])
        torch.testing.assert_close(single[0], action[1])
        self.assertTrue(torch.isfinite(log_prob).all())
        torch.testing.assert_close(actor.sample(obs, True, noise=noise)[0], mu.tanh())
