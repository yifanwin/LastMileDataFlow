import importlib.util
import unittest

import torch


@unittest.skipUnless(importlib.util.find_spec('mjlab'), 'requires the independent RL environment')
class JointCommandTests(unittest.TestCase):
    def test_command_rate_is_relative_to_previous_command(self):
        from lastmile_dataflow.runtime.control.dls import limit_joint_command
        current = torch.zeros(1, 7)
        command = current.clone()
        for _ in range(30):
            previous = command.clone()
            command, _ = limit_joint_command(current, torch.full_like(current, .2), command, -.1, .1, .004)
            self.assertTrue(((command - previous).abs() <= .004001).all())
        torch.testing.assert_close(command, torch.full_like(current, .1))

    def test_position_servo_can_converge_with_a_rate_limited_reference(self):
        from lastmile_dataflow.runtime.control.dls import limit_joint_command
        dt, max_step = .002, 2.175 * .002
        current = torch.zeros(1, 7)
        command = current.clone()
        for _ in range(500):
            displacement = (.6 - current).clamp(-.1, .1)
            command, _ = limit_joint_command(current, displacement, command, -1., 1., max_step)
            # Low-frequency position servo response, kp/kv=10 as on Panda.
            current += 10 * (command - current) * dt
        self.assertTrue((current > .55).all())
