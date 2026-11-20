import importlib.util
import unittest

import torch


@unittest.skipUnless(importlib.util.find_spec("mjlab"), "requires the independent RL environment")
class JointCommandTests(unittest.TestCase):
    def test_contact_gripper_positive_closes_and_graph_keeps_its_sign(self):
        from lastmile_dataflow.station_map.rl.env.environment import Gripper

        gripper = Gripper.__new__(Gripper)
        gripper._raw = torch.zeros(3, 1)
        gripper.targets = torch.zeros(3, 2)
        gripper.open_targets = torch.tensor([[0.04, -0.04]]).expand(3, -1)
        gripper.closed_targets = torch.zeros(3, 2)
        gripper.step, gripper.closed = None, torch.zeros(3, 1)
        actions = torch.tensor([[-1.], [0.], [1.]])
        for positive_closes in (True, False):
            gripper.positive_closes = positive_closes
            gripper.process_actions(actions)
            expected = torch.tensor([[0.04, -0.04], [0.02, -0.02], [0., 0.]])
            torch.testing.assert_close(gripper.targets, expected if positive_closes else expected.flip(0))
            torch.testing.assert_close(gripper.raw_action, actions)

    def test_incremental_gripper_accumulates_clamps_and_resets_open(self):
        from lastmile_dataflow.station_map.rl.env.environment import Gripper

        gripper = Gripper.__new__(Gripper)
        gripper._raw, gripper.targets = torch.zeros(2, 1), torch.zeros(2, 2)
        gripper.open_targets = torch.tensor([[0.04, -0.04]]).expand(2, -1)
        gripper.closed_targets = torch.zeros(2, 2)
        gripper.positive_closes, gripper.step, gripper.closed = True, 0.2, torch.zeros(2, 1)
        for _ in range(3):
            gripper.process_actions(torch.tensor([[1.], [0.5]]))
        torch.testing.assert_close(gripper.closed, torch.tensor([[0.6], [0.3]]))
        torch.testing.assert_close(gripper.targets, torch.tensor([[0.016, -0.016], [0.028, -0.028]]))
        gripper.process_actions(torch.tensor([[0.], [-1.]]))  # Zero holds; opening clamps at fully open.
        gripper.process_actions(torch.tensor([[0.], [-1.]]))
        torch.testing.assert_close(gripper.closed, torch.tensor([[0.6], [0.]]))
        for _ in range(5):
            gripper.process_actions(torch.tensor([[1.], [0.]]))
        self.assertEqual(float(gripper.closed[0]), 1.)
        gripper.reset(torch.tensor([0]))
        torch.testing.assert_close(gripper.closed, torch.zeros(2, 1))
        torch.testing.assert_close(gripper.targets[0], torch.tensor([0.04, -0.04]))

    def test_command_rate_is_relative_to_previous_command(self):
        from lastmile_dataflow.station_map.rl.env.dls import limit_joint_command

        current = torch.zeros(1, 7)
        command = current.clone()
        for _ in range(30):
            previous = command.clone()
            command, _ = limit_joint_command(current, torch.full_like(current, 0.2), command, -0.1, 0.1, 0.004)
            self.assertTrue(((command - previous).abs() <= 0.004001).all())
        torch.testing.assert_close(command, torch.full_like(current, 0.1))

    def test_reference_ramp_inside_a_policy_step_is_not_a_limit(self):
        from lastmile_dataflow.station_map.rl.env.dls import limit_joint_command

        current = torch.zeros(1, 7)
        _, within = limit_joint_command(current, torch.full_like(current, 0.05), current, -1.0, 1.0, 0.004, 25)
        _, beyond = limit_joint_command(current, torch.full_like(current, 0.2), current, -1.0, 1.0, 0.004, 25)
        _, clamped = limit_joint_command(current, torch.full_like(current, 0.05), current, -0.01, 0.01, 0.004, 25)
        self.assertEqual(float(within), 0.0)
        self.assertEqual(float(beyond), 1.0)
        self.assertEqual(float(clamped), 1.0)

    def test_position_servo_can_converge_with_a_rate_limited_reference(self):
        from lastmile_dataflow.station_map.rl.env.dls import limit_joint_command

        dt, max_step = 0.002, 2.175 * 0.002
        current = torch.zeros(1, 7)
        command = current.clone()
        for _ in range(500):
            displacement = (0.6 - current).clamp(-0.1, 0.1)
            command, _ = limit_joint_command(current, displacement, command, -1.0, 1.0, max_step)
            # Low-frequency position servo response, kp/kv=10 as on Panda.
            current += 10 * (command - current) * dt
        self.assertTrue((current > 0.55).all())
