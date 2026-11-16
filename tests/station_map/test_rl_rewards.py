from types import SimpleNamespace
import unittest

import torch

from lastmile_dataflow.utils.io import read_config
from lastmile_dataflow.station_map.rl.env.terms import TaskState, grasp_events, reward
from lastmile_dataflow.station_map.rl.env.observation import REWARD_COMPONENTS, CONTACT_REWARD_COMPONENTS
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

    def contact_reward_env(self, distances, angles, actions):
        n = len(distances)
        config = read_config("configs/station_map/rl/val103_cube_s0246_facing.yaml")
        distance, angle = torch.tensor(distances), torch.tensor(angles)
        task = SimpleNamespace(
            contact_protocol=True,
            goal_error=lambda: (torch.stack((distance, torch.zeros(n), torch.zeros(n)), -1),
                                torch.stack((angle, torch.zeros(n), torch.zeros(n)), -1)),
            last_action=torch.zeros(n, 7), forces=torch.ones(n, 2),
            previous_distance=distance + 0.01, previous_angle=angle + 0.1,
            step_collision=torch.ones(n, dtype=torch.bool),
            success=torch.zeros(n, dtype=torch.bool),
            contact_success_paid=torch.zeros(n, dtype=torch.bool),
            reward_components=torch.zeros(n, len(CONTACT_REWARD_COMPONENTS)),
        )
        task.last_action[:, -1] = torch.tensor(actions)
        return SimpleNamespace(task=task, step_dt=0.05, research_config=config)

    def test_contact_reward_components_and_one_time_success(self):
        env = self.contact_reward_env([0.005, 0.05], [0.1, 0.2], [0.5, -0.5])
        task = env.task
        task.success[0] = True
        task.previous_distance[1] = 0.04
        task.previous_angle[1] = 0.1
        reward(env)
        torch.testing.assert_close(task.reward_components[0], torch.tensor([0.1, 0.05, -2., 30., 0., 0.025, 0.05]))
        torch.testing.assert_close(task.reward_components[1], torch.tensor([-0.1, -0.05, -2., 0., 0.01, 0., 0.]))
        env.step_dt = 0.1  # Action/contact rewards are per step, not per second.
        reward(env)
        self.assertEqual(float(task.reward_components[:, 3].sum()), 0.0)
        self.assertEqual(float(task.reward_components[:, :2].sum()), 0.0)
        torch.testing.assert_close(task.reward_components[0, 4:], torch.tensor([0., 0.025, 0.05]))
        torch.testing.assert_close(task.reward_components[1, 4:], torch.tensor([0.01, 0., 0.]))

    def test_contact_reward_gates_and_action_signs(self):
        angle_limit = read_config("configs/station_map/rl/val103_cube_s0246_facing.yaml")["reward"]["grasp_angle_rad"]
        env = self.contact_reward_env(
            [0.03, 0.029, 0.029, 0.04, 0.041, 0.029, 0.029, 0.041],
            [0., angle_limit, 0., 0., 0., 0., 0., 0.],
            [1., 1., -1., -1., -1., 0., 1., 1.],
        )
        env.task.forces[6, 0] = 1e-6
        reward(env)
        torch.testing.assert_close(env.task.reward_components[:, 4:], torch.tensor([
            [0., 0., 0.], [0., 0., 0.], [0., 0., 0.05], [0., 0., 0.],
            [0.02, 0., 0.], [0., 0., 0.05], [0., 0.05, 0.], [0., 0., 0.],
        ]))
        # Only the final physical sample counts for shaping, unlike success.
        env.task.forces.zero_()
        reward(env)
        self.assertEqual(float(env.task.reward_components[:, -1].sum()), 0.)

    def test_contact_requires_simultaneous_substep_forces_and_latches(self):
        task = TaskState.__new__(TaskState)
        task.contact_protocol = True
        task.env = SimpleNamespace(physics_dt=0.002, research_config={"success": {"finger_force_min_n": 1e-6, "lift_m": 0.0086}},
                                   sim=SimpleNamespace(data=SimpleNamespace(
                                       overflow=torch.zeros(1), qpos=torch.zeros(1, 1),
                                       qvel=torch.zeros(1, 1), qacc=torch.zeros(1, 1))))
        target_position = torch.zeros(1, 3)
        task.poses = lambda: (torch.zeros(1, 3), torch.eye(3)[None], None, None,
                              target_position, torch.eye(3)[None])
        task.goal_error = lambda: (torch.zeros(1, 3), torch.zeros(1, 3))
        samples = iter((torch.tensor([[1., 0.]]), torch.tensor([[0., 1.]]),
                        torch.tensor([[1e-6, 2.]]), torch.tensor([[0.1, 0.2]]),
                        torch.zeros(1, 2), torch.tensor([[0.1, 0.2]]), torch.zeros(1, 2)))
        hits = iter([torch.ones(1, 2, dtype=torch.bool)] + [torch.zeros(1, 2, dtype=torch.bool)] * 6)
        task.contact_facts = lambda: (next(samples), torch.tensor([False]), next(hits))
        task.forces = torch.zeros(1, 2)
        task.support = torch.zeros(1, dtype=torch.bool)
        task.collision_hits = torch.zeros(1, 2)
        task.step_collision = torch.zeros(1, dtype=torch.bool)
        task.collision_free = torch.ones(1, dtype=torch.bool)
        task.collision_time = torch.zeros(1)
        task.height_reference = torch.zeros(1)
        task.fixed = [0, 1, 2, 3]
        task.robot = SimpleNamespace(data=SimpleNamespace(joint_pos=torch.zeros(1, 4)))
        task.fixed_reference = torch.zeros(1, 4)
        task.max_drift = torch.zeros(1)
        task.max_yaw_drift = torch.zeros(1)
        task.max_lift_drift = torch.zeros(1)
        task.control_failed = torch.zeros(1, dtype=torch.bool)
        task.failed = torch.zeros(1, dtype=torch.bool)
        task.unhealthy = torch.zeros(1, dtype=torch.bool)
        task.success = torch.zeros(1, dtype=torch.bool)
        task.elapsed = torch.zeros(1, dtype=torch.float64)
        task.first_contact_time_s = torch.full((1,), -1., dtype=torch.float64)
        for _ in range(3):
            task.sample_physics()
            self.assertFalse(bool(task.success[0]))
        # Both fingers alone are no longer success.
        target_position[:, 2] = 0.008599
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        self.assertAlmostEqual(float(task.first_contact_time_s[0]), 0.008)
        # Reaching height in a later, contact-free substep cannot combine events.
        target_position[:, 2] = 0.0086
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.sample_physics()
        self.assertTrue(bool(task.success[0]))
        target_position[:, 2] = 0.
        task.sample_physics()
        self.assertTrue(bool(task.success[0]))
        self.assertAlmostEqual(float(task.first_contact_time_s[0]), 0.008)
        self.assertTrue(bool(task.step_collision[0]))
        self.assertAlmostEqual(float(task.collision_time[0]), 0.002)
        # The reference is per episode, rather than an absolute world-height threshold.
        task.success.zero_()
        task.height_reference[:] = 0.77
        task.contact_facts = lambda: (torch.ones(1, 2), torch.tensor([False]), torch.zeros(1, 2, dtype=torch.bool))
        target_position[:, 2] = 0.77
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        target_position[:, 2] = 0.78
        task.failed[:] = True
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.failed.zero_()
        task.unhealthy[:] = True
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.unhealthy.zero_()
        task.goal_error = lambda: (torch.ones(1, 3), torch.ones(1, 3))
        task.sample_physics()
        self.assertTrue(bool(task.success[0]))

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
