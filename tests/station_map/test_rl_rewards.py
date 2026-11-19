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
            robot=SimpleNamespace(data=SimpleNamespace(joint_pos=torch.tensor([[0.04, -0.04]]).repeat(n, 1))),
            fingers=[0, 1], open_grip=0.08,
            closing_active=torch.zeros(n, dtype=torch.bool),
            closing_peak=torch.zeros(n), closing_paid=torch.zeros(n),
            lift_peak=torch.zeros(n),
            step_contact=torch.zeros(n),
            height_reference=torch.full((n,), 0.77),
            table_height_m=0.75,
            reward_components=torch.zeros(n, len(CONTACT_REWARD_COMPONENTS)),
        )
        target_position = torch.zeros(n, 3)
        target_position[:, 2] = task.height_reference
        task.poses = lambda: (None, None, None, None, target_position, torch.eye(3)[None].expand(n, -1, -1))
        task.last_action[:, -1] = torch.tensor(actions)
        return SimpleNamespace(task=task, step_dt=0.05, research_config=config)

    def test_contact_reward_components_and_one_time_success(self):
        env = self.contact_reward_env([0.005, 0.05], [0.1, 0.2], [0.5, -0.5])
        task = env.task
        task.success[0] = True
        task.step_contact[0] = 1
        task.previous_distance[1] = 0.04
        task.previous_angle[1] = 0.1
        reward(env)
        torch.testing.assert_close(task.reward_components[0], torch.tensor([0.2, 0.5, -1., 30., 0., 3., 0.]))
        torch.testing.assert_close(task.reward_components[1], torch.tensor([-0.2, -0.5, -1., 0., 0., 0., 0.]))
        env.step_dt = 0.1  # Action/contact rewards are per step, not per second.
        task.step_contact.zero_()
        reward(env)
        self.assertEqual(float(task.reward_components[:, 3].sum()), 0.0)
        self.assertEqual(float(task.reward_components[:, :2].sum()), 0.0)
        torch.testing.assert_close(task.reward_components[0, 4:], torch.zeros(3))
        torch.testing.assert_close(task.reward_components[1, 4:], torch.zeros(3))

    def test_pregrasp_stage_latches_with_continuous_potential(self):
        env = self.contact_reward_env([0.10], [0.0], [0.0])
        task, cfg = env.task, env.research_config["reward"]
        cfg.update(pregrasp_offset_m=0.1, pregrasp_tolerance_m=0.02, pregrasp_tolerance_rad=0.2)
        task.pregrasp_reached = torch.zeros(1, dtype=torch.bool)
        grasp_dp = torch.tensor([[0.0, 0.0, 0.10]])
        task.goal_error = lambda offset=0.0: (grasp_dp + torch.tensor([[0.0, 0.0, offset]]), torch.zeros(1, 3))
        task.previous_distance[:] = 0.11
        reward(env)  # Pregrasp error 0 reaches the stage; the grasp distance 0.10 equals offset + 0.
        self.assertTrue(bool(task.pregrasp_reached[0]))
        torch.testing.assert_close(task.reward_components[0, 0], torch.tensor(20 * 0.01))
        grasp_dp = torch.tensor([[0.0, 0.0, 0.05]])  # Moving away from the pregrasp keeps the stage.
        reward(env)
        self.assertTrue(bool(task.pregrasp_reached[0]))
        torch.testing.assert_close(task.reward_components[0, 0], torch.tensor(20 * 0.05))

    def test_descent_weights_lateral_error_after_switch(self):
        env = self.contact_reward_env([0.10], [0.0], [0.0])
        task, cfg = env.task, env.research_config["reward"]
        cfg.update(pregrasp_offset_m=0.1, pregrasp_tolerance_m=0.02, pregrasp_tolerance_rad=0.2,
                   descent_lateral_weight=3.0)
        task.pregrasp_reached = torch.ones(1, dtype=torch.bool)
        task.approach_axis = lambda: torch.tensor([[0.0, 0.0, 1.0]])
        task.goal_error = lambda offset=0.0: (torch.tensor([[0.03, 0.0, 0.04 + offset]]), torch.zeros(1, 3))
        task.previous_distance[:] = 0.2
        reward(env)  # sqrt(0.04^2 + (3 * 0.03)^2)
        torch.testing.assert_close(task.previous_distance, torch.tensor([(0.04 ** 2 + 0.09 ** 2) ** 0.5]))

    def test_closing_requires_a_pressing_finger_when_configured(self):
        env = self.contact_reward_env([0.02, 0.02], [0.0, 0.0], [1.0, 1.0])
        env.research_config["reward"]["closing_requires_contact"] = True
        env.task.forces = torch.tensor([[0.0, 0.0], [0.6, 0.0]])
        reward(env)  # Enter the stage; only the pressing environment becomes active.
        env.task.robot.data.joint_pos *= 0.5
        reward(env)
        torch.testing.assert_close(env.task.reward_components[:, 4], torch.tensor([0.0, 1.5]))

    def test_closing_uses_actual_progress_with_strict_pose_gate(self):
        env = self.contact_reward_env(
            [0.02, 0.03, 0.02, 0.04], [0., 0., 20 * torch.pi / 180, 0.],
            [0., 1., 1., 1.],
        )
        reward(env)  # Enter the aligned stage with open fingers.
        env.task.robot.data.joint_pos *= 0.5
        reward(env)
        torch.testing.assert_close(env.task.reward_components[:, 4], torch.tensor([1.5, 0., 0., 0.]))
        env.task.last_action[:, -1] = 1  # A command without actual motion earns nothing.
        reward(env)
        torch.testing.assert_close(env.task.reward_components[:, 4], torch.zeros(4))

    def test_closing_stage_reentry_no_backpay_and_episode_cap(self):
        env = self.contact_reward_env([0.02], [0.], [1.])
        task = env.task
        total = 0.
        for near, closing, expected in (
            (False, 0.2, 0.), (True, 0.2, 0.), (True, 0.5, 0.9),
            (True, 0.5, 0.), (True, 0.1, 0.), (True, 0.4, 0.),
            (True, 0.7, 0.6), (False, 0.9, 0.), (True, 0.9, 0.),
            (True, 1., 0.3), (False, 0., 0.), (True, 0., 0.),
            (True, 1., 1.2), (False, 0., 0.), (True, 0., 0.), (True, 1., 0.),
        ):
            task.goal_error = lambda: (torch.tensor([[0.02 if near else 0.04, 0., 0.]]), torch.zeros(1, 3))
            task.robot.data.joint_pos[:] = torch.tensor([[0.04, -0.04]]) * (1 - closing)
            reward(env)
            value = float(task.reward_components[0, 4])
            self.assertAlmostEqual(value, expected, places=5)
            total += value
        self.assertAlmostEqual(total, 3., places=5)

    def test_contact_event_survives_leaving_stage_before_reward(self):
        env = self.contact_reward_env([0.1], [1.], [0.])
        env.task.step_contact[:] = 1
        env.task.forces.zero_()
        reward(env)
        self.assertEqual(float(env.task.reward_components[0, CONTACT_REWARD_COMPONENTS.index("grasp")]), 3.)

    def test_lift_episode_peak_survives_release_and_caps_at_eight(self):
        env = self.contact_reward_env([0.1], [0.8], [0.])
        task = env.task
        height = env.research_config["success"]["lift_m"]
        position = task.poses()[4]
        index = CONTACT_REWARD_COMPONENTS.index("lift")
        total_progress = 0.
        for progress, both, expected in (
            (0.2, False, 0.), (0.3, True, 2.4), (0.6, True, 2.4),
            (0.6, True, 0.), (0.2, True, 0.), (0.5, True, 0.),
            (0.8, True, 1.6), (1.5, False, 0.), (0.7, True, 0.),
            (0.9, True, 0.8), (1.5, True, 0.8), (0., False, 0.),
            (-0.5, True, 0.), (1.5, True, 0.), (0., False, 0.),
            (0., True, 0.), (1., True, 0.),
        ):
            position[:, 2] = task.height_reference + progress * height
            task.forces[:] = float(both)
            total = reward(env)
            value = float(task.reward_components[0, index])
            self.assertAlmostEqual(value, expected, places=4)
            total_progress += value
            torch.testing.assert_close(total, task.reward_components.sum(-1))
        self.assertAlmostEqual(total_progress, 8., places=4)

    def contact_physics_task(self):
        task = TaskState.__new__(TaskState)
        task.contact_protocol = True
        task.env = SimpleNamespace(physics_dt=0.002, research_config=read_config("configs/station_map/rl/val103_cube_s0246_facing.yaml"),
                                   sim=SimpleNamespace(data=SimpleNamespace(
                                       overflow=torch.zeros(1), qpos=torch.zeros(1, 1),
                                       qvel=torch.zeros(1, 1), qacc=torch.zeros(1, 1))))
        task.env.task = task
        task.table_height_m = -0.02
        target_position = torch.zeros(1, 3)
        task.poses = lambda: (torch.zeros(1, 3), torch.eye(3)[None], None, None,
                              target_position, torch.eye(3)[None])
        task.goal_error = lambda: (torch.zeros(1, 3), torch.zeros(1, 3))
        samples = iter((torch.tensor([[1., 0.]]), torch.tensor([[0., 1.]]),
                        torch.tensor([[0.5, 2.]]), torch.tensor([[0.6, 0.7]]),
                        torch.zeros(1, 2), torch.tensor([[0.6, 0.7]]), torch.zeros(1, 2)))
        hits = iter([torch.ones(1, 2, dtype=torch.bool)] + [torch.zeros(1, 2, dtype=torch.bool)] * 6)
        task.contact_facts = lambda: (next(samples), torch.tensor([False]), next(hits))
        task.forces = torch.zeros(1, 2)
        task.support = torch.zeros(1, dtype=torch.bool)
        task.collision_hits = torch.zeros(1, 2)
        task.step_collision = torch.zeros(1, dtype=torch.bool)
        task.step_contact = torch.zeros(1)
        task.contact_bonus_paid = torch.zeros(1, dtype=torch.bool)
        task.step_lift = torch.zeros(1)
        task.held_time = torch.zeros(1)
        task.last_action = torch.zeros(1, 7)
        task.collision_free = torch.ones(1, dtype=torch.bool)
        task.collision_time = torch.zeros(1)
        task.height_reference = torch.zeros(1)
        task.physical_fixed = False
        task.base_joints = [0, 1, 2]
        task.lift = [3]
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
        task.hold = torch.zeros(1, dtype=torch.float64)
        task.first_contact_time_s = torch.full((1,), -1., dtype=torch.float64)
        return task, target_position

    def test_contact_requires_simultaneous_substep_forces_and_latches(self):
        task, target_position = self.contact_physics_task()
        for _ in range(3):
            task.sample_physics()
            self.assertFalse(bool(task.success[0]))
        # Both fingers alone are no longer success.
        target_position[:, 2] = 0.009999
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        self.assertAlmostEqual(float(task.first_contact_time_s[0]), 0.008)
        # Reaching height in a later, contact-free substep cannot combine events.
        target_position[:, 2] = 0.01
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        self.assertAlmostEqual(float(task.hold[0]), 0.002)
        task.contact_facts = lambda: (torch.ones(1, 2), torch.tensor([False]), torch.zeros(1, 2, dtype=torch.bool))
        for _ in range(123):
            task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        self.assertAlmostEqual(float(task.hold[0]), 0.248)
        task.sample_physics()
        self.assertTrue(bool(task.success[0]))
        target_position[:, 2] = 0.
        task.contact_facts = lambda: (torch.zeros(1, 2), torch.tensor([False]), torch.zeros(1, 2, dtype=torch.bool))
        task.sample_physics()
        self.assertTrue(bool(task.success[0]))
        self.assertEqual(float(task.hold[0]), 0.)
        self.assertAlmostEqual(float(task.first_contact_time_s[0]), 0.008)
        self.assertTrue(bool(task.step_collision[0]))
        self.assertAlmostEqual(float(task.collision_time[0]), 0.002)
        # The tabletop is fixed independently of the per-episode center baseline.
        task.success.zero_()
        task.height_reference[:] = 0.77
        task.table_height_m = 0.75
        task.contact_facts = lambda: (torch.ones(1, 2), torch.tensor([False]), torch.zeros(1, 2, dtype=torch.bool))
        target_position[:, 2] = 0.77
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        target_position[:, 2] = 0.781
        task.failed[:] = True
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.failed.zero_()
        task.unhealthy[:] = True
        task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.unhealthy.zero_()
        task.goal_error = lambda: (torch.ones(1, 3), torch.ones(1, 3))
        for _ in range(125):
            task.sample_physics()
        self.assertTrue(bool(task.success[0]))

    def test_success_hold_resets_on_each_condition_break_across_policy_steps(self):
        for interrupted in ("left", "right", "height", "support"):
            with self.subTest(interrupted=interrupted):
                task, position = self.contact_physics_task()
                forces = torch.ones(1, 2)
                support = torch.tensor([False])
                task.contact_facts = lambda: (forces, support, torch.zeros(1, 2, dtype=torch.bool))
                position[:, 2] = 0.011
                for _ in range(100):
                    task.sample_physics()
                self.assertAlmostEqual(float(task.hold[0]), 0.2)
                task.begin_step(torch.zeros(1, 7))
                self.assertAlmostEqual(float(task.hold[0]), 0.2)
                if interrupted in ("left", "right"):
                    forces[0, 0 if interrupted == "left" else 1] = 0.5
                elif interrupted == "height":
                    position[:, 2] = 0.009
                else:
                    support[:] = True
                task.sample_physics()
                self.assertEqual(float(task.hold[0]), 0.)
                self.assertFalse(bool(task.success[0]))
                forces.fill_(1.)
                support.zero_()
                position[:, 2] = 0.011
                for _ in range(124):
                    task.sample_physics()
                self.assertFalse(bool(task.success[0]))
                task.sample_physics()
                self.assertTrue(bool(task.success[0]))

    def test_cube_support_uses_contact_presence_even_without_normal_force(self):
        task, _ = self.contact_physics_task()
        task.finger_addresses = torch.tensor([0, 1])
        task.support_addresses = torch.tensor([2])
        task.collision_addresses = torch.tensor([3])
        # Cube support addresses select native "found", not force magnitude.
        task.env.sim.data.sensordata = torch.tensor([[1., 1., 1., 0.]])
        _, support, _ = TaskState.contact_facts(task)
        self.assertTrue(bool(support[0]))
        task.env.sim.data.sensordata[0, 2] = 0
        self.assertFalse(bool(TaskState.contact_facts(task)[1][0]))

    def test_tilted_cube_clearance_controls_success_and_lift_reward(self):
        env = self.contact_reward_env([0.1], [0.8], [0.])
        task = env.task
        position = task.poses()[4]
        tilt = torch.tensor([[[1., 0., 0.], [0., 2**-0.5, -2**-0.5],
                              [0., 2**-0.5, 2**-0.5]]])
        task.poses = lambda: (None, None, None, None, position, tilt)
        # Upright: 0.78 would give 1 cm clearance; tilted: only about 1.7 mm.
        position[:, 2] = 0.78
        reward(env)
        position[:, 2] += 0.002
        reward(env)
        self.assertAlmostEqual(float(task.reward_components[0, -1]), 1.6, places=4)
        # Rotation alone can reduce clearance despite unchanged center height.
        position[:, 2] = 0.785
        task.poses = lambda: (None, None, None, None, position, torch.eye(3)[None])
        reward(env)
        peak = task.lift_peak.clone()
        task.poses = lambda: (None, None, None, None, position, tilt)
        reward(env)
        self.assertEqual(float(task.reward_components[0, -1]), 0.)
        torch.testing.assert_close(task.lift_peak, peak)

        physics, center = self.contact_physics_task()
        physics.contact_facts = lambda: (torch.ones(1, 2), torch.tensor([False]),
                                        torch.zeros(1, 2, dtype=torch.bool))
        physics.poses = lambda: (torch.zeros(1, 3), torch.eye(3)[None], None, None, center, tilt)
        center[:, 2] = 0.01  # Center rose 1 cm, lowest corner did not.
        physics.sample_physics()
        self.assertFalse(bool(physics.success[0]))
        center[:, 2] = 0.019
        for _ in range(124):
            physics.sample_physics()
        self.assertFalse(bool(physics.success[0]))
        physics.sample_physics()
        self.assertTrue(bool(physics.success[0]))

    def test_lift_reward_requires_strict_half_newton_on_each_finger(self):
        env = self.contact_reward_env([0.1], [0.8], [0.])
        task = env.task
        position = task.poses()[4]
        for forces in ([0.5, 1.], [1., 0.5], [0.1, 0.2]):
            task.forces[0] = torch.tensor(forces)
            position[:, 2] = 0.77
            reward(env)
            position[:, 2] = 0.78
            reward(env)
            self.assertEqual(float(task.reward_components[0, -1]), 0.)
        task.forces.fill_(0.6)
        position[:, 2] = 0.77
        reward(env)
        position[:, 2] = 0.775
        reward(env)
        self.assertAlmostEqual(float(task.reward_components[0, -1]), 4., places=4)

    def test_contact_bonus_once_per_episode_even_after_stage_reentry(self):
        task, _ = self.contact_physics_task()
        dp, dr, forces = torch.zeros(1, 3), torch.zeros(1, 3), torch.ones(1, 2)
        task.goal_error = lambda: (dp, dr)
        task.contact_facts = lambda: (forces, torch.tensor([False]), torch.zeros(1, 2, dtype=torch.bool))
        angle_limit = task.env.research_config["reward"]["grasp_angle_rad"]
        for distance, angle, left_force, right_force, events, paid in (
            (0.04, 0., 1., 1., 0, False),
            (0.02, 0., 0., 0., 0, False),
            (0.02, 0., 1., 0., 0, False),
            (0.02, 0., 0., 1., 0, False),
            (0.02, 0., 0.5, 1., 0, False),
            (0.02, 0., 1., 0.5, 0, False),
            (0.02, 0., 1., 1., 1, True),
            (0.02, 0., 1., 1., 1, True),
            (0.02, 0., 0., 0., 1, True),
            (0.02, 0., 1., 1., 1, True),
            (0.03, 0., 1., 1., 1, True),
            (0.029, 0., 1., 1., 1, True),
            (0.02, angle_limit, 1., 1., 1, True),
            (0.02, angle_limit - 0.001, 1., 1., 1, True),
        ):
            dp[0, 0], dr[0, 0] = distance, angle
            forces[0] = torch.tensor([left_force, right_force])
            task.sample_physics()
            self.assertEqual(float(task.step_contact[0]), events)
            self.assertEqual(bool(task.contact_bonus_paid[0]), paid)
        # Policy-step boundaries clear the accumulator, not the episode's paid state.
        task.begin_step(torch.zeros(1, 7))
        task.sample_physics()
        self.assertEqual(float(task.step_contact[0]), 0.)
        self.assertTrue(bool(task.contact_bonus_paid[0]))

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


class TypedCollisionTests(unittest.TestCase):
    def test_tier_first_sustained_and_rearm(self):
        from lastmile_dataflow.station_map.rl.env.terms import tier_penalty
        first, sustained = torch.tensor([0.5, 0.8, 1.0]), torch.tensor([0.05, 0.08, 0.1])
        idle = torch.full((1, 3), 5)
        pattern = [1, 1, 0, 1, 0, 0, 0, 0, 0, 1]  # large tier only
        paid = []
        for on in pattern:
            active = torch.tensor([[False, False, bool(on)]])
            penalty, _ = tier_penalty(active, idle, first, sustained, 5)
            paid.append(round(float(penalty[0]), 3))
        # A short gap keeps the sustained rate; five idle steps rearm the first-contact penalty.
        self.assertEqual(paid, [1.0, 0.1, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
        both = torch.tensor([[True, True, False]])
        penalty, onset = tier_penalty(both, torch.full((1, 3), 5), first, sustained, 5)
        self.assertAlmostEqual(float(penalty[0]), 1.3, places=5)

    def test_stuck_window_requires_all_three_conditions(self):
        from lastmile_dataflow.station_map.rl.env.terms import stuck_window
        cfg = dict(collision_fraction=0.5, tcp_path_m=0.02, distance_gain_m=0.005, angle_gain_rad=0.05)
        w = 40
        illegal = torch.ones(4, w, dtype=torch.bool)
        illegal[1, :25] = False                     # only 15/40 colliding
        move = torch.full((4, w), 0.0002)
        move[2] = 0.001                             # 4 cm path
        distance = torch.full((4, w), 0.1)
        distance[3, 10:] = 0.09                     # 1 cm gain after the oldest step
        angle = torch.full((4, w), 0.2)
        stuck = stuck_window(illegal, move, distance, angle, torch.zeros(4, dtype=torch.long), cfg)
        self.assertEqual(stuck.tolist(), [True, False, False, False])


class RealObjectContactTests(unittest.TestCase):
    contact_physics_task = RewardTests.contact_physics_task
    def test_real_object_uses_lowest_vertex_and_continuous_hold(self):
        task, position = self.contact_physics_task()
        task.env.research_config["scene"].pop("cube")
        task.target_points = torch.tensor([[0., 0., -0.06], [0.03, 0., 0.02]])
        task.table_height_m = 0.75
        position[:, 2] = 0.811  # Center rose, but the bottom clears by only 1 mm.
        forces = torch.ones(1, 2)
        support = torch.tensor([False])
        task.contact_facts = lambda: (forces, support, torch.zeros(1, 2, dtype=torch.bool))
        for _ in range(125):
            task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        position[:, 2] = 0.821
        for _ in range(100):
            task.sample_physics()
        forces[0, 1] = 0.5
        task.sample_physics()
        self.assertEqual(float(task.hold[0]), 0.)
        forces.fill_(1.)
        for _ in range(124):
            task.sample_physics()
        self.assertFalse(bool(task.success[0]))
        task.sample_physics()
        self.assertTrue(bool(task.success[0]))
