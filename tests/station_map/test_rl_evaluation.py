import importlib.util
import json
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
from types import SimpleNamespace
import unittest

import torch


def observations(state):
    nodes = torch.zeros(2, 1, 24)
    nodes[:, :, 0] = 1
    return {
        "policy": {
            "nodes": nodes,
            "node_mask": torch.ones(2, 1, dtype=torch.bool),
            "arm_task_state": torch.tensor(state, dtype=torch.float32)[:, None],
        }
    }


class ObservationActor:
    def sample(self, observation, deterministic=False):
        return observation.arm_task_state.expand(-1, 7).clone(), torch.zeros(2)


class TwoEpisodeEnv:
    num_envs = 2
    device = "cpu"
    step_dt = 0.05

    def __init__(self):
        self.stage = 0
        self.pending = torch.zeros(2, dtype=torch.bool)
        self.actions = []
        self.task = SimpleNamespace(
            stations=[{"station_id": "first"}, {"station_id": "second"}],
            unhealthy=torch.zeros(2, dtype=torch.bool),
            success=torch.zeros(2, dtype=torch.bool),
            collision_free=torch.ones(2, dtype=torch.bool),
            collision_time=torch.zeros(2),
            max_drift=torch.zeros(2),
            max_yaw_drift=torch.zeros(2),
            max_lift_drift=torch.zeros(2),
            failed=torch.zeros(2, dtype=torch.bool),
            contact_bonus_paid=torch.zeros(2, dtype=torch.bool),
            reward_components=torch.zeros(2, 13),
            target=0,
            height_reference=torch.zeros(2),
            forces=torch.zeros(2, 2),
            hold=torch.zeros(2),
            goal_error=lambda: (torch.ones(2, 3), torch.ones(2, 3)),
        )
        self.sim = SimpleNamespace(data=SimpleNamespace(xpos=torch.zeros(2, 1, 3)))

    def reset(self, env_ids=None):
        if env_ids is None:
            self.pending.zero_()
            return observations([1, 2]), {}
        self.pending[env_ids] = False
        return observations([-99, -99]), {}  # Whole-batch return is not ongoing history.

    def step(self, action):
        if self.pending.any():
            raise RuntimeError("manual reset required")
        self.actions.append(action.clone())
        self.stage += 1
        self.task.success.zero_()
        if self.stage == 1:
            reward, done = torch.tensor([2.0, 3.0]), torch.tensor([True, False])
            self.task.success[0] = True
        else:
            reward, done = torch.tensor([100.0, 4.0]), torch.tensor([False, True])
        self.pending[:] = done
        return observations([self.stage + 1, self.stage + 2]), reward, done, torch.zeros_like(done), {}


@unittest.skipUnless(importlib.util.find_spec("torch_geometric"), "requires RL dependencies")
class ValidationTests(unittest.TestCase):
    def test_async_terminal_records_once_and_preserves_ongoing_observation(self):
        from lastmile_dataflow.station_map.rl.evaluation import validation_rollout

        env = TwoEpisodeEnv()
        env.task.grasps = [dict(source="fixture", row=0)]
        env.task.grasp_ids = torch.zeros(2, dtype=torch.long)
        progress = Mock()
        rows = validation_rollout(SimpleNamespace(actor=ObservationActor()), env, [0, 1], 42, 0.5,
                                  on_progress=progress)
        self.assertEqual([call.args[1] for call in progress.call_args_list], [1, 2])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["episode_return"], 2.0)
        self.assertEqual(rows[1]["episode_return"], 7.0)
        self.assertEqual(rows[1]["return"], 5.0)
        self.assertTrue(rows[0]["success"])
        self.assertFalse(rows[1]["success"])
        self.assertEqual(float(env.actions[1][1, 0]), 3.0)
        self.assertFalse(rows[0]["both_fingers"])
        self.assertAlmostEqual(rows[0]["best_position_error_m"], 3**0.5, places=6)

    def test_cube_evaluation_reports_clearance_and_configured_contact_threshold(self):
        from lastmile_dataflow.station_map.rl.evaluation import validation_rollout

        env = TwoEpisodeEnv()
        env.research_config = {"success": {"finger_force_min_n": 0.5},
                               "scene": {"cube": {"size_m": 0.04}}}
        task = env.task
        task.contact_protocol = True
        task.table_height_m = 0.75
        task.height_reference.fill_(0.77)
        task.forces[:] = torch.tensor([[0.5, 1.], [0.6, 0.7]])
        env.sim.data.xpos[:, 0, 2] = 0.785
        tilt = torch.tensor([[[1., 0., 0.], [0., 2**-0.5, -2**-0.5],
                              [0., 2**-0.5, 2**-0.5]]]).expand(2, -1, -1)
        task.poses = lambda: (None, None, None, None, env.sim.data.xpos[:, 0], tilt)
        task.grasps = [dict(source="fixture", row=0)]
        task.grasp_ids = torch.zeros(2, dtype=torch.long)
        rows = validation_rollout(SimpleNamespace(actor=ObservationActor()), env, [0, 1], 42, 0.5)
        self.assertFalse(rows[0]["both_fingers"])
        self.assertTrue(rows[1]["both_fingers"])
        for row in rows:
            self.assertAlmostEqual(row["max_lift_m"], 0.785 - 0.02 * 2**0.5 - 0.75, places=6)
        self.assertEqual(rows[0]["stage_seconds"]["LIFT"], 0.)
        self.assertAlmostEqual(rows[1]["stage_seconds"]["LIFT"], 0.1)

    def test_fixed_station_evaluation_uses_training_pose_and_records_its_scope(self):
        from lastmile_dataflow.station_map.rl.evaluation import monitor_validation
        from lastmile_dataflow.utils.io import read_config

        config = read_config("configs/station_map/rl/val103_cup30_s0246_y04.yaml")
        config["training"]["validation_rollouts_per_station"] = 2
        row = {"station_id": "S0246-y04", "split": "train", "valid": True, "q_mean": 1.0}
        trial = {"station_id": row["station_id"], "success": True, "collision_free": True,
                 "infrastructure_failure": False, "return": 2.0, "episode_return": 3.0}
        env = SimpleNamespace(num_envs=2, device="cpu", task=SimpleNamespace(stations=[row], pool=[0]))
        agent = SimpleNamespace(actor=Mock(), q1=Mock(), q2=Mock())
        prefix = "lastmile_dataflow.station_map.rl.evaluation."
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch(prefix + "torch.random.fork_rng", return_value=nullcontext()))
            scores = stack.enter_context(patch(prefix + "station_scores", return_value=[row]))
            rollout = stack.enter_context(patch(prefix + "validation_rollout", return_value=[trial, trial]))
            stack.enter_context(patch(prefix + "station_evaluation"))
            stack.enter_context(patch(prefix + "RolloutCapture", side_effect=lambda *a, **k: SimpleNamespace(videos=[])))
            event = monitor_validation(agent, env, config, 100, directory, force_video=True)
            selection = json.loads((Path(directory) / "analysis/validation-selection.json").read_text())
        self.assertEqual(event["scope"], "fixed_station")
        self.assertEqual(event["split"], "train")
        self.assertEqual(event["healthy_trials"], 2)
        self.assertEqual(event["evaluation_success_rate"], 1.0)
        self.assertIsNone(event["value_success_correlation"])
        self.assertEqual(selection["scope"], "fixed_station")
        self.assertEqual(selection["stations"][0]["group"], "fixed_station")
        scores.assert_called_once_with(agent, env, config["evaluation"]["action_samples"], [0])
        self.assertEqual(rollout.call_count, 3)  # One parallel trial batch and two video batches.
        self.assertNotIn("capture", rollout.call_args_list[0].kwargs)
        self.assertTrue(all(call.kwargs["capture"] is not None for call in rollout.call_args_list[1:]))
        self.assertTrue(all(call.args[2] == [0, 0] for call in rollout.call_args_list))

    def test_cached_scores_are_reused_without_reset_or_network_calls(self):
        from lastmile_dataflow.station_map.rl.evaluation import station_scores

        row = {"station_id": "S0246", "valid": True, "q_mean": 1.5, "q_std": 0.2, "K": 16}
        env = SimpleNamespace(task=SimpleNamespace(stations=[row]))
        with patch("lastmile_dataflow.station_map.rl.evaluation.reset_stations") as reset:
            result = station_scores(None, env, 16, initial_scores=[row])
        self.assertEqual(result, [row])
        reset.assert_not_called()

    def test_partial_q_cache_preserves_mask_and_only_scores_missing_station(self):
        from lastmile_dataflow.station_map.rl.evaluation import station_scores

        cached = {"station_id": "S0", "valid": True, "q_mean": 9.0, "q_std": 0.2, "K": 16}
        env = SimpleNamespace(device="cpu", num_envs=2, task=SimpleNamespace(
            stations=[{"station_id": "S0", "valid": True}, {"station_id": "S1", "valid": True},
                      {"station_id": "S2", "valid": False}],
            grasp_pools=[[0, 1]] * 3, grasps=[dict(source="fixture", row=i) for i in range(2)]))
        with patch("lastmile_dataflow.station_map.rl.evaluation.reset_stations") as reset, patch(
            "lastmile_dataflow.station_map.rl.evaluation.action_sampled_q",
            return_value=(torch.tensor([1.0, 3.0]), torch.zeros(2)),
        ):
            rows = station_scores(None, env, 16, initial_scores=[cached])
        reset.assert_called_once_with(env, [1, 1])
        self.assertEqual(rows[0], cached)
        self.assertEqual(rows[1]["q_mean"], 2.0)
        self.assertFalse(rows[2]["valid"])
        self.assertIsNone(rows[2]["q_mean"])

    def test_training_validates_final_policy_once_after_closing_samplers(self):
        from lastmile_dataflow.station_map.rl.training import train
        from lastmile_dataflow.station_map.rl.monitor import TrainingMonitor
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
        from lastmile_dataflow.station_map.rl.config import resolve_config
        from tests.station_map.test_rl_sac import observation

        config = {
            "training": {"validation_stations": 1, "replay_capacity": 10, "warmup_min": 2, "batch_size": 1,
                         "eval_every": 1, "log_every": 10, "max_transitions": 105, "transitions_per_update": 1},
            "environment": {"num_envs": 1}, "evaluation": {"seed": 42, "action_samples": 1, "q_color_limits": [-1, 1]},
            "sac": {}, "success": {},
            "stations": {"robot_config": "configs/robots/panda_omron.yaml"},
        }
        config = resolve_config(config)
        prefix = "lastmile_dataflow.station_map.rl.training."
        cases = (
            (False, False, False, False, 0, False), (True, True, True, False, 100, False),
            (True, False, False, False, 0, False), (True, True, False, False, 0, False),
            (True, True, True, True, 100, False), (True, False, True, False, 1, False),
            (True, True, True, False, 1, False), (True, True, True, True, 1, True),
        )
        for resume, has_best, has_score, same_output, score_count, from_ckpts in cases:
            with self.subTest(resume=resume, has_best=has_best, has_score=has_score, same_output=same_output), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                parent = Path(directory)
                source = parent / "previous"
                source.mkdir()
                if has_best:
                    (source / "best.pt").touch()
                resume_path = source / "latest.pt"
                if from_ckpts:
                    (source / "ckpts").mkdir()
                    resume_path = source / "ckpts" / "step-000000100.pt"
                output = source if same_output else parent / "new"
                clock = Mock(return_value=0)
                stack.enter_context(patch("lastmile_dataflow.station_map.rl.monitor.time", SimpleNamespace(monotonic=clock)))
                monitor_class = stack.enter_context(patch(prefix + "TrainingMonitor", wraps=TrainingMonitor))
                agent = Mock(updates=0)
                def update(batch):
                    agent.updates += 1
                    value = torch.tensor(float(agent.updates))
                    return {key: value for key in ("actor_loss", "critic_loss", "alpha_loss", "entropy", "alpha")}

                agent.update.side_effect = update
                score_metric = "recent_100_episode_success_rate" if has_best else "tensorboard_interval_success_rate"
                preserve_best = resume and has_score and has_best and not from_ckpts
                saved = {"transitions": 100, "metadata": {"config": config, "best_training_success_rate": {"metric": score_metric, "rate": 1.0, "count": score_count} if has_score else None}}
                sac = stack.enter_context(patch(prefix + "SAC", return_value=agent))
                stack.enter_context(patch(prefix + "CHECKPOINT_EVERY_TRANSITIONS", 103))
                sac.load.return_value = agent, saved
                stack.enter_context(patch(prefix + "seed_all"))
                stack.enter_context(patch(prefix + "runtime_metadata", return_value={}))
                stack.enter_context(patch(prefix + "graph_observation", return_value=observation(1)))
                stack.enter_context(patch(prefix + "Replay")).return_value.size = 0
                stack.enter_context(patch(prefix + "torch.cuda.memory_allocated", return_value=0))
                env = Mock(device="cpu")
                env.task.grasps = []
                env.task.grasp_pools = [[]]
                env.reset.return_value = ({}, {})
                stack.enter_context(patch("lastmile_dataflow.station_map.rl.env.environment.StationEnv", return_value=env))
                workers = stack.enter_context(patch(prefix + "Samplers")).return_value
                def receive():
                    if workers.receive.call_count == 3:
                        reader = EventAccumulator(str(output / "tensorboard")).Reload()
                        points = reader.Scalars("runtime/healthy")
                        self.assertEqual([(p.step, p.value) for p in points], [(102 if resume else 2, 2)])
                        agent.save.assert_not_called()
                        workers.close.assert_not_called()
                    clock.return_value += 15
                    index = workers.receive.call_count - 1
                    episode_flags = [(True, False, True), (False, True, True),
                                     (False, False, False), (False, False, True)]
                    episodes = ""
                    if index < len(episode_flags):
                        unhealthy, warmup, success = episode_flags[index]
                        episodes = json.dumps({"event": "episode", "infrastructure_failure": unhealthy,
                                               "random_warmup": warmup, "success": success,
                                               "return": 1000. if not success else 1.})
                    return (index % 2, 0, True, SimpleNamespace(reward=torch.zeros(1)), episodes, 0.0)

                workers.receive.side_effect = receive
                workers.pause.return_value = []
                def validate(*args, **kwargs):
                    workers.close.assert_called_once()
                    self.assertEqual(workers.receive.call_count, 5)
                    self.assertIn("latest.pt", [call.args[0].name for call in agent.save.call_args_list])
                    return {"scope": "train", "transitions": args[3], "healthy_trials": 1, "evaluation_success_rate": 0.0, "episode_return_mean": 1.0}, []

                monitor = stack.enter_context(patch(prefix + "monitor_validation", side_effect=validate))
                stack.enter_context(patch(prefix + "station_scores", return_value=[]))
                stack.enter_context(patch(prefix + "plot_scores"))
                result = train(config, gpus=[0], output=output, seed=42,
                               resume=resume_path if resume else None,
                               max_transitions=105 if resume else 5)
                self.assertEqual(monitor.call_count, 1)
                self.assertEqual(monitor.call_args.args[3], result["transitions"])
                self.assertNotIn("initial.pt", [call.args[0].name for call in agent.save.call_args_list])
                self.assertTrue(monitor.call_args_list[-1].kwargs["force_video"])
                self.assertEqual(result["transitions"], 105 if resume else 5)
                workers.pause.assert_called_once()
                if not preserve_best:
                    self.assertIn("best.pt", [call.args[0].name for call in agent.save.call_args_list])
                else:
                    self.assertTrue((output / "best.pt").is_file())
                self.assertEqual(sac.load.call_args.args[0], output / "best.pt")
                self.assertEqual(sac.load.call_count, 2 if resume else 1)
                metadata = agent.save.call_args.kwargs["metadata"]
                self.assertNotIn("recent_episode_successes", metadata)
                self.assertEqual(metadata["best_training_success_rate"]["rate"],
                                 1. if preserve_best else 0.5)
                snapshots = [c for c in agent.save.call_args_list if c.args[0].parent.name == "ckpts"]
                self.assertEqual([c.kwargs["transitions"] for c in snapshots], [103] if resume else [])
                workers.close.assert_called_once()
                env.close.assert_called_once()
                self.assertEqual(monitor_class.call_args.kwargs["every_seconds"], 30)
                self.assertEqual(monitor_class.call_args.kwargs["purge_step"], 100 if same_output else None)
                reader = EventAccumulator(str(output / "tensorboard")).Reload()
                self.assertEqual(agent.update.call_count, 3)
                self.assertEqual([p.value for p in reader.Scalars("loss/actor")], [1.0, 2.5])
                point, = reader.Scalars("evaluation/train/evaluation_success_rate")
                self.assertEqual((point.step, point.value), (result["transitions"], 0))


class ParallelEvaluationTests(unittest.TestCase):
    def test_trial_streams_ignore_batch_width_and_finished_lanes(self):
        from lastmile_dataflow.station_map.rl.evaluation import episode_generators, episode_noise

        generators = episode_generators([42, 43], "cpu")
        single = episode_generators([43], "cpu")
        for active in ([True, True], [False, True], [False, True]):
            batch = episode_noise(generators, 7, torch.tensor(active))
            alone = episode_noise(single, 7, torch.tensor([True]))
            torch.testing.assert_close(batch[1], alone[0])
        first = episode_generators([42], "cpu")
        episode_noise(first, 7, torch.tensor([True]))
        torch.testing.assert_close(episode_noise(generators, 7, torch.tensor([True, False]))[0],
                                   episode_noise(first, 7, torch.tensor([True]))[0])

    def test_grasp_selection_follows_trial_seed_when_lanes_are_reordered(self):
        from lastmile_dataflow.station_map.rl.evaluation import episode_generators, request_episode_grasps

        task = SimpleNamespace(device="cpu", grasp_pools=[[3, 5, 7], [1, 2]])
        request_episode_grasps(task, [0, 1], episode_generators([42, 43], "cpu"))
        original = task.requested_grasp_ids.clone()
        request_episode_grasps(task, [1, 0], episode_generators([43, 42], "cpu"))
        torch.testing.assert_close(task.requested_grasp_ids, original.flip(0))

    def test_batches_keep_trial_order_and_exclude_padding(self):
        from lastmile_dataflow.station_map.rl.evaluation import run_evaluation_trials

        env = SimpleNamespace(num_envs=2, task=SimpleNamespace(stations=[{"station_id": "S0246"}]))
        plan = [dict(station_id="S0246", seed=42 + i, trial=i) for i in range(3)]

        def rollout(agent, env, stations, seed, gamma, **kwargs):
            for i in reversed(range(2)):
                kwargs["on_trial"](i, dict(seed=kwargs["trial_seeds"][i], steps=2,
                                          success=False, failed=False, infrastructure_failure=False))

        with tempfile.TemporaryDirectory() as directory, patch(
            "lastmile_dataflow.station_map.rl.evaluation.validation_rollout", side_effect=rollout
        ) as run, patch("lastmile_dataflow.station_map.rl.evaluation.EpisodeReplays") as replay:
            rows, videos = run_evaluation_trials(SimpleNamespace(updates=0), env, plan, 0.99, Path(directory))
            logged = [json.loads(line) for line in (Path(directory) / "trials.jsonl").read_text().splitlines()]
        self.assertEqual([r["trial"] for r in rows], [0, 1, 2])
        self.assertEqual([r["trial"] for r in logged], [1, 0, 2])
        self.assertEqual([r["seed"] for r in rows], [42, 43, 44])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(replay.return_value.save.call_count, 3)
        self.assertEqual(videos, [])


class FlatTwoEpisodeEnv(TwoEpisodeEnv):
    def __init__(self):
        super().__init__()
        self.task.first_contact_time_s = torch.tensor([0.1, -1.0])
        self.task.reward_components = torch.zeros(2, 7)

    def reset(self, env_ids=None):
        obs, info = super().reset(env_ids)
        return {"policy": {"flat": obs["policy"]["arm_task_state"]}}, info

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)
        return {"policy": {"flat": obs["policy"]["arm_task_state"]}}, reward, terminated, truncated, info


class FlatNoiseActor:
    def sample(self, observation, deterministic=False, *, noise=None):
        return observation.expand(-1, 7).clone() + noise, None


class FlatParallelEvaluationTests(unittest.TestCase):
    def test_flat_runner_preserves_ongoing_tensor_observations_and_contact_facts(self):
        from lastmile_dataflow.station_map.rl.evaluation import validation_rollout

        env = FlatTwoEpisodeEnv()
        callback = Mock()
        rows = validation_rollout(SimpleNamespace(mode="flat", actor=FlatNoiseActor()), env,
                                  [0, 1], 42, 0.5, trial_seeds=[42, 43], on_trial=callback)
        self.assertEqual(callback.call_count, 2)
        self.assertEqual([r["steps"] for r in rows], [1, 2])
        self.assertEqual([r["episode_return"] for r in rows], [2.0, 7.0])
        self.assertEqual([r["seed"] for r in rows], [42, 43])
        self.assertAlmostEqual(rows[0]["first_simultaneous_contact_s"], 0.1)
        self.assertIsNone(rows[1]["first_simultaneous_contact_s"])
        self.assertEqual(len(rows[0]["reward_component_names"]), 8)
        self.assertNotIn("grasp_source", rows[0])
        self.assertTrue(torch.all(env.actions[1][0] == 0))

    def test_flat_evaluation_excludes_unhealthy_and_captures_all_twenty(self):
        from lastmile_dataflow.station_map.rl.evaluation import evaluate_flat

        env = SimpleNamespace(num_envs=20, task=SimpleNamespace(pool=[0], stations=[{"station_id": "S0246"}]), close=Mock())
        rows = [dict(station_id="S0246", trial=i, seed=42+i, success=i < 10, collision_free=i < 8,
                     infrastructure_failure=i >= 18, first_simultaneous_contact_s=0.2,
                     reason="infrastructure_failure" if i >= 18 else "simultaneous_contact" if i < 10 else "timeout")
                for i in range(20)]
        config = {"sac": {"gamma": 0.999}, "success": {"version": "rl-pick-contact-v2", "lift_m": 0.0086}}
        with tempfile.TemporaryDirectory() as directory, patch(
            "lastmile_dataflow.station_map.rl.env.environment.StationEnv", return_value=env
        ), patch("lastmile_dataflow.station_map.rl.evaluation.run_evaluation_trials", return_value=(rows, [])) as run:
            result = evaluate_flat(config, SimpleNamespace(), "best.pt", 0, directory, 42, num_envs=20)
            plan = json.loads((Path(directory) / "selection.json").read_text())
        self.assertEqual([r["seed"] for r in plan], list(range(42, 62)))
        self.assertTrue(run.call_args.kwargs["capture_all"])
        self.assertEqual(result["total_trials"], 20)
        self.assertEqual(result["healthy_trials"], 18)
        self.assertEqual(result["success_rate"], 10 / 18)
        self.assertEqual(result["collision_free_success_rate"], 8 / 18)
        env.close.assert_called_once()

    def test_multistation_plan_is_seeded_distinct_and_uses_only_valid_pool(self):
        from lastmile_dataflow.station_map.rl.evaluation import flat_trial_plan, flat_trial_summary

        task = SimpleNamespace(pool=torch.arange(0, 50, 2),
                               stations=[{"station_id": f"S{i}"} for i in range(50)])
        settings = {"station_count": 20, "rollouts_per_station": 1}
        plan = flat_trial_plan(task, settings, 42)
        self.assertEqual(plan, flat_trial_plan(task, settings, 42))
        self.assertEqual(len({r["station_id"] for r in plan}), 20)
        self.assertTrue(all(int(r["station_id"][1:]) % 2 == 0 for r in plan))
        self.assertEqual([r["seed"] for r in plan], list(range(42, 62)))
        task.pool = torch.tensor([0, 2])
        self.assertEqual(len(flat_trial_plan(task, settings, 42)), 2)
        rows = [dict(success=True, collision_free=True, infrastructure_failure=False,
                     first_simultaneous_contact_s=0.2, reason="simultaneous_contact"),
                dict(success=False, collision_free=False, infrastructure_failure=True,
                     reason="infrastructure_failure")]
        summary = flat_trial_summary(rows)
        self.assertEqual(summary["healthy_trials"], 1)
        self.assertEqual(summary["success_rate"], 1.)

    def test_parallel_override_keeps_checkpoint_configuration_check(self):
        from lastmile_dataflow.station_map.rl.evaluation import evaluate

        config = {"evaluation": {"seed": 42, "station_count": 20}, "scene": {"lift": 0.2}}
        saved = {"evaluation": {"seed": 42, "station_count": 4}, "scene": {"lift": 0.3}}
        with patch("lastmile_dataflow.station_map.rl.evaluation.resolve_config", side_effect=lambda c: c), patch(
            "lastmile_dataflow.station_map.rl.evaluation.SAC.load", return_value=(SimpleNamespace(mode="flat"), {"metadata": {"config": saved}})
        ), patch("lastmile_dataflow.station_map.rl.evaluation.evaluate_flat") as flat:
            with self.assertRaisesRegex(ValueError, "checkpoint effective configuration"):
                evaluate(config, checkpoint="best.pt", scores=None, gpu=0, output="unused", num_envs=3)
            flat.assert_not_called()
            saved["scene"]["lift"] = 0.2  # Only the evaluation protocol differs now.
            evaluate(config, checkpoint="best.pt", scores=None, gpu=0, output="unused", num_envs=3)
            self.assertEqual(flat.call_args.kwargs["num_envs"], 3)
            self.assertEqual(config, {"evaluation": {"seed": 42, "station_count": 20}, "scene": {"lift": 0.2}})
