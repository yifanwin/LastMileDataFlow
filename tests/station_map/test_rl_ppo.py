"""PPO numerical contracts, global batches, checkpoints and synchronized transport."""

from contextlib import ExitStack
import json
import multiprocessing as mp
from pathlib import Path
import pickle
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import torch

from lastmile_dataflow.station_map.rl.checkpoint import load_agent
from lastmile_dataflow.station_map.rl.monitor import TrainingMonitor
from lastmile_dataflow.station_map.rl.config import PPOConfig
from lastmile_dataflow.station_map.rl.ppo.agent import PPO, approximate_kl, clipped_policy_loss
from lastmile_dataflow.station_map.rl.ppo.rollout import RolloutBatch, gae, normalize_advantages
from lastmile_dataflow.station_map.rl.ppo.sampling import SynchronousSamplers
from lastmile_dataflow.station_map.rl.sac.agent import SAC
from lastmile_dataflow.station_map.rl.station_q import select_acceptance_stations, summarize_trials
from tests.station_map.test_rl_sac import observation


def make_batch(agent, count=11):
    obs = torch.randn(count, agent.state_dim) if agent.mode == "flat" else observation(count)
    with torch.no_grad():
        raw, log = agent.actor.sample_raw(obs)
        value = agent.value(obs)
    advantages = torch.linspace(-2, 4, count)
    return RolloutBatch(obs, raw, log, value, advantages, value + advantages)


def transport_worker(channel, index):
    channel.send_bytes(pickle.dumps(("ready", 3)))
    while True:
        command, payload = pickle.loads(channel.recv_bytes())
        if command == "close":
            break
        version, actor, value, quota = payload
        data = torch.ones(quota)
        batch = RolloutBatch(torch.full((quota, 2), float(index)), torch.zeros(quota, 7),
                             data, data, data, data)
        channel.send_bytes(pickle.dumps((version, batch, str(index))))
    channel.close()


class PPOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.previous_threads)

    def test_algorithm_switch_preserves_default_sac_and_overrides_config(self):
        from lastmile_dataflow.station_map.rl.training import train

        kwargs = dict(gpus=[0], output="unused", seed=42)
        with patch("lastmile_dataflow.station_map.rl.training.train_sac", return_value="sac") as sac, patch(
            "lastmile_dataflow.station_map.rl.training.train_ppo", return_value="ppo"
        ) as ppo:
            self.assertEqual(train({}, **kwargs), "sac")
            self.assertEqual(sac.call_args.args[0], {})
            original = {"algorithm": "sac"}
            self.assertEqual(train(original, algorithm="ppo", **kwargs), "ppo")
            self.assertEqual(ppo.call_args.args[0]["algorithm"], "ppo")
            self.assertEqual(original, {"algorithm": "sac"})
            self.assertEqual(train({"algorithm": "ppo"}, algorithm="sac", **kwargs), "sac")
        with self.assertRaisesRegex(ValueError, "Unknown RL algorithm"):
            train({}, algorithm="unknown", **kwargs)

    def test_gae_ends_and_invalid_steps_break_recursion_but_rollout_end_bootstraps(self):
        rewards = torch.tensor([[1., 10.], [2., float("nan")], [3., 30.]])
        values = torch.zeros_like(rewards)
        next_values = torch.ones_like(rewards) * 4
        done = torch.tensor([[False, False], [True, True], [False, False]])
        valid = torch.tensor([[True, True], [True, False], [True, True]])
        advantage, returns = gae(rewards, values, next_values, done, valid, 0.5, 1.)
        torch.testing.assert_close(advantage, torch.tensor([[4., 12.], [2., 0.], [5., 32.]]))
        torch.testing.assert_close(returns, advantage)

    def test_global_normalization_uses_all_valid_workers_once_and_preserves_targets(self):
        agent = PPO(4, "cpu", PPOConfig(epochs=2, minibatch_size=3, target_kl=100), mode="flat")
        first, second = make_batch(agent, 4), make_batch(agent, 7)
        first.advantages[:] = torch.tensor([-10., -5., 0., 5.])
        second.advantages[:] = torch.arange(7.) + 10
        batch = RolloutBatch.concatenate([first, second])
        target = batch.returns.clone()
        raw_adv = batch.advantages.clone()
        with patch("lastmile_dataflow.station_map.rl.ppo.agent.normalize_advantages", wraps=normalize_advantages) as normalize:
            result = agent.update(batch)
        normalize.assert_called_once()
        torch.testing.assert_close(batch.advantages, normalize_advantages(raw_adv))
        self.assertAlmostEqual(float(batch.advantages.mean()), 0., places=6)
        self.assertAlmostEqual(float(batch.advantages.std(correction=0)), 1., places=6)
        torch.testing.assert_close(batch.returns, target)
        self.assertEqual(result["minibatches"], 8)  # Includes both tail minibatches.
        self.assertTrue(torch.equal(normalize_advantages(torch.ones(3)), torch.zeros(3)))

    def test_clipped_objective_and_nonnegative_kl(self):
        ratios = torch.tensor([1.5, 0.5, 0.5, 1.5])
        adv = torch.tensor([1., -1., 1., -1.])
        torch.testing.assert_close(clipped_policy_loss(ratios.log(), adv, 0.2), torch.tensor(0.15))
        self.assertEqual(float(approximate_kl(torch.zeros(4))), 0.)
        self.assertGreater(float(approximate_kl(ratios.log())), 0.)

    def test_separate_actor_value_gradient_clipping_epochs_shuffle_and_checkpoint(self):
        cfg = PPOConfig(epochs=3, minibatch_size=4, target_kl=100,
                        actor_max_grad_norm=0.03, value_max_grad_norm=0.07)
        for mode, dim in (("flat", 65), ("graph", 9)):
            with self.subTest(mode=mode):
                agent = PPO(dim, "cpu", cfg, mode=mode)
                batch = make_batch(agent)
                old_logs, old_values, old_raw = (x.clone() for x in (batch.old_log_prob, batch.old_value, batch.raw_action))
                old_actor = [p.clone() for p in agent.actor.parameters()]
                old_value = [p.clone() for p in agent.value.parameters()]
                permutations = []
                real_randperm = torch.randperm
                def shuffle(count):
                    result = real_randperm(count)
                    permutations.append(result.clone())
                    return result
                with patch("torch.nn.utils.clip_grad_norm_", wraps=torch.nn.utils.clip_grad_norm_) as clip, patch(
                    "lastmile_dataflow.station_map.rl.ppo.agent.torch.randperm", side_effect=shuffle
                ):
                    result = agent.update(batch)
                self.assertEqual(result["epochs_completed"], 3)
                self.assertEqual(result["minibatches"], 9)
                self.assertEqual(clip.call_count, 18)
                self.assertEqual([call.args[1] for call in clip.call_args_list], [0.03, 0.07] * 9)
                self.assertEqual(len(permutations), 3)
                self.assertFalse(torch.equal(permutations[0], permutations[1]))
                for network, bound in ((agent.actor, 0.03), (agent.value, 0.07)):
                    norm = torch.cat([p.grad.flatten() for p in network.parameters() if p.grad is not None]).norm()
                    self.assertLessEqual(float(norm), bound + 1e-6)
                self.assertTrue(any(not torch.equal(a, b) for a, b in zip(old_actor, agent.actor.parameters())))
                self.assertTrue(any(not torch.equal(a, b) for a, b in zip(old_value, agent.value.parameters())))
                torch.testing.assert_close(batch.old_log_prob, old_logs)
                torch.testing.assert_close(batch.old_value, old_values)
                torch.testing.assert_close(batch.raw_action, old_raw)
                self.assertTrue(all(torch.isfinite(torch.tensor(v)) for v in result.values()))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "model.pt"
                    agent.save(path, metadata={"config": {}}, transitions=123)
                    loaded, saved = load_agent(path, "cpu", restore_rng=True)
                    torch.testing.assert_close(loaded.actor.sample(batch.obs, deterministic=True)[0],
                                               agent.actor.sample(batch.obs, deterministic=True)[0])
                    torch.testing.assert_close(loaded.value(batch.obs), agent.value(batch.obs))
                    self.assertEqual((loaded.updates, loaded.optimizer_steps, saved["transitions"]), (1, 9, 123))
                    loaded.update(make_batch(loaded))
                    self.assertEqual(loaded.updates, 2)
                    with self.assertRaisesRegex(ValueError, "Cannot resume"):
                        load_agent(path, "cpu", algorithm="sac")

    def test_kl_exceeding_threshold_stops_current_and_remaining_epochs_before_update(self):
        agent = PPO(4, "cpu", PPOConfig(epochs=5, minibatch_size=3), mode="flat")
        batch = make_batch(agent)
        batch.old_log_prob += 2
        before = [p.clone() for p in agent.actor.parameters()]
        result = agent.update(batch)
        self.assertEqual(result["kl_early_stop"], 1)
        self.assertEqual(result["kl_early_stops"], 1)
        self.assertEqual(result["epochs_completed"], 0)
        self.assertEqual(result["minibatches"], 0)
        self.assertGreater(result["max_minibatch_kl"], agent.config.target_kl)
        self.assertGreater(result["global_kl"], agent.config.target_kl)
        self.assertTrue(all(torch.equal(a, b) for a, b in zip(before, agent.actor.parameters())))

    def test_saturated_actions_have_reproducible_finite_log_probability(self):
        agent = PPO(4, "cpu", mode="flat")
        obs, raw = torch.zeros(2, 4), torch.full((2, 7), 30.)
        log = agent.actor.log_probability(obs, raw)
        self.assertTrue(torch.isfinite(log).all())
        torch.testing.assert_close(agent.actor.log_probability(obs, raw) - log, torch.zeros(2))

    def test_legacy_sac_without_algorithm_loads_and_rejects_ppo_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sac.pt"
            SAC(4, "cpu", mode="flat").save(path, metadata={}, transitions=2)
            saved = torch.load(path, weights_only=False)
            del saved["algorithm"]
            torch.save(saved, path)
            agent, _ = load_agent(path, "cpu")
            self.assertEqual(agent.algorithm, "sac")
            with self.assertRaisesRegex(ValueError, "Cannot resume"):
                load_agent(path, "cpu", algorithm="ppo")

    def test_synchronous_workers_receive_actor_value_and_exact_tail_budget(self):
        workers = SynchronousSamplers.__new__(SynchronousSamplers)
        workers.channels, workers.processes = [], []
        workers.num_envs, workers.steps = 3, 2
        ctx = mp.get_context("spawn")
        for index in range(2):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=transport_worker, args=(child, index))
            process.start()
            child.close()
            workers.channels.append(parent)
            workers.processes.append(process)
        try:
            workers.ready()
            agent = PPO(2, "cpu", mode="flat")
            agent.updates = 7
            results = workers.collect(agent, 9)
            self.assertEqual([len(row[1].returns) for row in results], [6, 3])
            self.assertEqual([row[0] for row in results], [0, 1])
            self.assertEqual([len(row[1].returns) for row in workers.collect(agent, 1)], [1])
        finally:
            workers.close()
        self.assertTrue(all(p.exitcode == 0 for p in workers.processes))

    def test_v_station_scores_aggregate_grasps_and_drive_ranking(self):
        from lastmile_dataflow.station_map.rl.evaluation import station_scores
        env = SimpleNamespace(device="cpu", num_envs=2, task=SimpleNamespace(
            stations=[{"station_id": "s0", "valid": True}], grasp_pools=[[0, 1]],
            grasps=[dict(source="test", row=i) for i in range(2)],
        ))
        agent = SimpleNamespace(algorithm="ppo", value=lambda obs: torch.tensor([1., 5.]))
        with patch("lastmile_dataflow.station_map.rl.evaluation.reset_stations"):
            scores = station_scores(agent, env, 16)
        self.assertEqual(scores[0]["v_mean"], 3.)
        self.assertEqual(scores[0]["v_std"], 2.)
        self.assertNotIn("q_mean", scores[0])
        rows = [dict(station_id=str(i), valid=True, v_mean=float(i)) for i in range(4)]
        selected = select_acceptance_stations(rows, dict(seed=42, top=1, middle=0, low=0, uniform=0))
        self.assertEqual(selected[0]["station_id"], "3")
        summary = summarize_trials(selected, [dict(station_id="3", infrastructure_failure=False,
                                                   success=True, collision_free=True, **{"return": 1.})], seed=42)
        self.assertIn("v_success_spearman", summary)
        self.assertNotIn("q_success_spearman", summary)

    def test_rolling_success_saves_generated_policy_before_ppo_update(self):
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
        from lastmile_dataflow.station_map.rl.ppo.training import train_ppo

        config = {"ppo": {}, "observation": {"mode": "flat"}, "evaluation": {"seed": 42},
                  "training": {"max_transitions": 2, "num_envs": 1, "validation_stations": 1, "log_every": 100}}
        clock = Mock(return_value=0)
        agent = Mock(updates=0, optimizer_steps=0)
        selected = []

        def save(path, *, metadata, transitions):
            if path.name == "best.pt":
                best = metadata["best_training_success_rate"]
                selected.append((transitions, agent.updates, best["rate"], best["count"]))

        def update(batch):
            agent.updates += 1
            agent.optimizer_steps += 1
            return {"actor_loss": float(agent.updates)}

        def collect(policy, quota):
            clock.return_value += 30
            event = json.dumps({"success": agent.updates > 0, "infrastructure_failure": False})
            return [(0, SimpleNamespace(returns=torch.zeros(1)), event)]

        agent.save.side_effect = save
        agent.update.side_effect = update
        prefix = "lastmile_dataflow.station_map.rl.ppo.training."
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch("lastmile_dataflow.station_map.rl.monitor.time", SimpleNamespace(monotonic=clock)))
            stack.enter_context(patch(prefix + "resolve_config", side_effect=lambda value: value))
            stack.enter_context(patch(prefix + "seed_all"))
            stack.enter_context(patch(prefix + "training_metadata", return_value={"state_dim": 4}))
            stack.enter_context(patch(prefix + "policy_observation", return_value=torch.zeros(1, 4)))
            stack.enter_context(patch(prefix + "PPO", return_value=agent))
            stack.enter_context(patch(prefix + "RolloutBatch.concatenate"))
            stack.enter_context(patch(prefix + "torch.cuda.memory_allocated", return_value=0))
            env = Mock(device="cpu")
            env.reset.return_value = ({}, {})
            stack.enter_context(patch("lastmile_dataflow.station_map.rl.env.environment.StationEnv", return_value=env))
            workers = stack.enter_context(patch(prefix + "SynchronousSamplers")).return_value
            workers.collect.side_effect = collect
            result = train_ppo(config, gpus=[0], output=directory, seed=42)
            self.assertEqual(result["transitions"], 2)
            self.assertEqual(selected, [(1, 0, 0, 1), (2, 1, 0.5, 2)])
            self.assertEqual(agent.update.call_count, 2)
            reader = EventAccumulator(str(Path(directory) / "tensorboard")).Reload()
            self.assertEqual([p.value for p in reader.Scalars("train_interval/success")], [0, 1])
            self.assertEqual([p.value for p in reader.Scalars("train/success")][:2], [0, 0.5])
            self.assertEqual([p.value for p in reader.Scalars("ppo/actor_loss")], [1, 2])
            workers.close.assert_called_once()
            env.close.assert_called_once()

    def test_ppo_tensorboard_omits_sac_alpha(self):
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
        with tempfile.TemporaryDirectory() as directory:
            monitor = TrainingMonitor(directory, every_seconds=30)
            monitor.record_update({"global_kl": 0.1, "actor_grad_norm": 2.}, algorithm="ppo")
            monitor.close(128)
            reader = EventAccumulator(str(Path(directory) / "tensorboard")).Reload()
            self.assertEqual(reader.Scalars("ppo/global_kl")[0].step, 128)
            self.assertNotIn("policy/alpha", reader.Tags()["scalars"])


if __name__ == "__main__":
    unittest.main()
