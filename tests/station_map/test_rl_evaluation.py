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
        rows = validation_rollout(SimpleNamespace(actor=ObservationActor()), env, [0, 1], 42, 0.5)
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
        env = SimpleNamespace(num_envs=1, device="cpu", task=SimpleNamespace(stations=[row], pool=[0]))
        agent = SimpleNamespace(actor=Mock(), q1=Mock(), q2=Mock())
        prefix = "lastmile_dataflow.station_map.rl.evaluation."
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch(prefix + "torch.random.fork_rng", return_value=nullcontext()))
            scores = stack.enter_context(patch(prefix + "station_scores", return_value=[row]))
            rollout = stack.enter_context(patch(prefix + "validation_rollout", return_value=[trial]))
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
        self.assertEqual(rollout.call_count, 4)  # Two trials plus stochastic / mean video rollouts.
        self.assertTrue(all(call.args[2] == [0] for call in rollout.call_args_list))

    def test_training_validates_final_policy_once_after_closing_samplers(self):
        from lastmile_dataflow.station_map.rl.training import train
        from lastmile_dataflow.station_map.rl.config import resolve_config
        from tests.station_map.test_rl_sac import observation

        config = {
            "training": {"validation_stations": 1, "replay_capacity": 10, "warmup_min": 20,
                         "eval_every": 1, "log_every": 10, "max_transitions": 105, "transitions_per_update": 128},
            "environment": {"num_envs": 1}, "evaluation": {"seed": 42, "action_samples": 1, "q_color_limits": [-1, 1]},
            "sac": {}, "success": {},
            "stations": {"robot_config": "configs/robots/panda_omron.yaml"},
        }
        config = resolve_config(config)
        prefix = "lastmile_dataflow.station_map.rl.training."
        cases = ((False, False, False), (True, True, True), (True, False, True), (True, True, False))
        for resume, has_best, has_score in cases:
            with self.subTest(resume=resume, has_best=has_best, has_score=has_score), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                parent = Path(directory)
                source = parent / "previous"
                source.mkdir()
                if has_best:
                    (source / "best.pt").touch()
                agent = Mock(updates=0)
                saved = {"transitions": 100, "metadata": {"config": config, "best_validation_score": [0, 5] if has_score else None}}
                sac = stack.enter_context(patch(prefix + "SAC", return_value=agent))
                sac.load.return_value = agent, saved
                stack.enter_context(patch(prefix + "seed_all"))
                stack.enter_context(patch(prefix + "runtime_metadata", return_value={}))
                stack.enter_context(patch(prefix + "graph_observation", return_value=observation(1)))
                stack.enter_context(patch(prefix + "Replay"))
                env = Mock(device="cpu")
                env.reset.return_value = ({}, {})
                stack.enter_context(patch("lastmile_dataflow.station_map.rl.env.environment.StationEnv", return_value=env))
                workers = stack.enter_context(patch(prefix + "Samplers")).return_value
                workers.receive.return_value = (0, 0, True, SimpleNamespace(reward=torch.zeros(1)), "", 0.0)
                workers.pause.return_value = []
                def validate(*args, **kwargs):
                    workers.close.assert_called_once()
                    self.assertEqual(workers.receive.call_count, 5)
                    self.assertIn("latest.pt", [call.args[0].name for call in agent.save.call_args_list])
                    return {"healthy_trials": 1, "evaluation_success_rate": 0.0, "episode_return_mean": 1.0}

                monitor = stack.enter_context(patch(prefix + "monitor_validation", side_effect=validate))
                stack.enter_context(patch(prefix + "training_curves"))
                stack.enter_context(patch(prefix + "station_scores", return_value=[]))
                stack.enter_context(patch(prefix + "plot_scores"))
                result = train(config, gpus=[0], output=parent / "new", seed=42,
                               resume=source / "latest.pt" if resume else None,
                               max_transitions=105 if resume else 5)
                self.assertEqual(monitor.call_count, 1)
                self.assertEqual(monitor.call_args.args[3], result["transitions"])
                self.assertNotIn("initial.pt", [call.args[0].name for call in agent.save.call_args_list])
                self.assertTrue(monitor.call_args_list[-1].kwargs["force_video"])
                self.assertEqual(result["transitions"], 105 if resume else 5)
                workers.pause.assert_called_once()
                self.assertIn("best.pt", [call.args[0].name for call in agent.save.call_args_list])
                self.assertEqual(sac.load.call_count, 1 if resume else 0)
                workers.close.assert_called_once()
                env.close.assert_called_once()
