from types import SimpleNamespace
import unittest

import torch

from lastmile_dataflow.utils.io import read_config
from lastmile_dataflow.station_map.rl.env.terms import grasp_events, reward
from lastmile_dataflow.station_map.rl.env.observation import REWARD_COMPONENTS
from lastmile_dataflow.operation.outcomes.pick_state import update_hold_window, RL_PICK_THRESHOLDS


class RewardTests(unittest.TestCase):
    def test_contact_once_and_peak_no_repayment(self):
        previous, paid = torch.tensor([False]), torch.tensor([False])
        peak = torch.tensor([0.0])
        events, total = 0, 0.0
        for both, height in [
            (False, 0.2),
            (True, 0.3),
            (True, 0.6),
            (True, 0.2),
            (False, 0.7),
            (True, 0.8),
            (True, 1.0),
        ]:
            both = torch.tensor([both])
            event, paid, peak, progress = grasp_events(
                both, previous, paid, torch.tensor([height]), peak, torch.tensor([True])
            )
            events += int(event.sum())
            total += float(progress.sum())
            previous = both
        self.assertEqual(events, 1)
        self.assertAlmostEqual(total, 0.5)
        self.assertEqual(float(peak), 1.0)

    def test_pregrasp_neutral_width_and_signed_progress(self):
        config = read_config("configs/station_map/rl/val103_cup30.yaml")
        task = SimpleNamespace(
            goal_error=lambda: (torch.tensor([[0.02, 0, 0]]), torch.zeros(1, 3)),
            forces=torch.zeros(1, 2),
            robot=SimpleNamespace(data=SimpleNamespace(joint_pos=torch.tensor([[0.04, -0.04]]))),
            fingers=[0, 1],
            open_grip=0.08,
            tcp_velocity=lambda: (torch.zeros(1, 3), torch.zeros(1, 3)),
            previous_distance=torch.tensor([0.01]),
            previous_angle=torch.tensor([0.0]),
            step_collision=torch.tensor([False]),
            control_failed=torch.tensor([False]),
            success=torch.tensor([False]),
            step_contact=torch.zeros(1),
            step_lift=torch.zeros(1),
            held_time=torch.zeros(1),
            reward_components=torch.zeros(1, len(REWARD_COMPONENTS)),
        )
        env = SimpleNamespace(
            task=task,
            research_config=config,
            step_dt=0.05,
            action_manager=SimpleNamespace(get_term=lambda _: SimpleNamespace(limit_fraction=torch.zeros(1))),
        )
        reward(env)
        first = task.reward_components.clone()
        task.robot.data.joint_pos.zero_()
        task.previous_distance[:] = 0.01
        reward(env)
        torch.testing.assert_close(first, task.reward_components)
        self.assertLess(float(first[0, 0]), 0)
        self.assertEqual(float(first[0, REWARD_COMPONENTS.index("close")]), 0)

    def test_rl_window_ignores_slip_but_release_resets(self):
        position = torch.ones(1, 3)
        rotation = torch.eye(3)[None]
        args = (
            torch.tensor([1.0]),
            torch.tensor([True]),
            position,
            rotation,
            torch.tensor([0.0]),
            torch.zeros(1, 3),
            rotation,
        )
        _, _, _, duration = update_hold_window(*args, xp=torch, thresholds=RL_PICK_THRESHOLDS)
        self.assertEqual(float(duration), 1.0)
        args = (*args[:1], torch.tensor([False]), *args[2:])
        start, _, _, duration = update_hold_window(*args, xp=torch, thresholds=RL_PICK_THRESHOLDS)
        self.assertEqual(float(start), -1.0)
        self.assertEqual(float(duration), 0.0)
