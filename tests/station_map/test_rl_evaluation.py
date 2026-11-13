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
            (False, False, False, False), (True, True, True, False), (True, False, False, False),
            (True, True, False, False), (True, True, True, True),
        )
        for resume, has_best, has_score, same_output in cases:
            with self.subTest(resume=resume, has_best=has_best, has_score=has_score, same_output=same_output), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                parent = Path(directory)
                source = parent / "previous"
                source.mkdir()
                if has_best:
                    (source / "best.pt").touch()
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
                saved = {"transitions": 100, "metadata": {"config": config, "best_training_return": {"mean": 2.0} if has_score else None, "recent_episode_returns": [2.0] if has_score else []}}
                sac = stack.enter_context(patch(prefix + "SAC", return_value=agent))
                sac.load.return_value = agent, saved
                stack.enter_context(patch(prefix + "seed_all"))
                stack.enter_context(patch(prefix + "runtime_metadata", return_value={}))
                stack.enter_context(patch(prefix + "graph_observation", return_value=observation(1)))
                stack.enter_context(patch(prefix + "Replay")).return_value.size = 0
                stack.enter_context(patch(prefix + "torch.cuda.memory_allocated", return_value=0))
                env = Mock(device="cpu")
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
                    return (0, 0, True, SimpleNamespace(reward=torch.zeros(1)), "", 0.0)

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
                               resume=source / "latest.pt" if resume else None,
                               max_transitions=105 if resume else 5)
                self.assertEqual(monitor.call_count, 1)
                self.assertEqual(monitor.call_args.args[3], result["transitions"])
                self.assertNotIn("initial.pt", [call.args[0].name for call in agent.save.call_args_list])
                self.assertTrue(monitor.call_args_list[-1].kwargs["force_video"])
                self.assertEqual(result["transitions"], 105 if resume else 5)
                workers.pause.assert_called_once()
                if not (resume and has_score):
                    self.assertIn("best.pt", [call.args[0].name for call in agent.save.call_args_list])
                else:
                    self.assertTrue((output / "best.pt").is_file())
                self.assertEqual(sac.load.call_args.args[0], output / "best.pt")
                self.assertEqual(sac.load.call_count, 2 if resume else 1)
                workers.close.assert_called_once()
                env.close.assert_called_once()
                self.assertEqual(monitor_class.call_args.kwargs["every_seconds"], 30)
                self.assertEqual(monitor_class.call_args.kwargs["purge_step"], 100 if same_output else None)
                reader = EventAccumulator(str(output / "tensorboard")).Reload()
                self.assertEqual(agent.update.call_count, 3)
                self.assertEqual([p.value for p in reader.Scalars("loss/actor")], [1.0, 2.5])
                point, = reader.Scalars("evaluation/train/evaluation_success_rate")
                self.assertEqual((point.step, point.value), (result["transitions"], 0))
