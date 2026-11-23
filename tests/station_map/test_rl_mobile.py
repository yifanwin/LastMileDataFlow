"""Mobile control coordinates, local-scene boundary and the 10D SAC contract."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import mujoco
import torch

from lastmile_dataflow.station_map.rl.env.mobile import LocalBoundary, MobileBase, base_delta
from lastmile_dataflow.station_map.rl.env.dls import BaseFrameDLS
from lastmile_dataflow.station_map.rl.env.observation import (
    FlatObservationState, build_flat_observation, flat_fields,
)
from lastmile_dataflow.station_map.rl.env.terms import TaskState
from lastmile_dataflow.station_map.rl.sac.agent import SAC, SACConfig, SACInference
from lastmile_dataflow.station_map.rl.sac.replay import Replay, Transition


class MobileTests(unittest.TestCase):
    def test_body_delta_rotates_and_diagonal_translation_is_limited(self):
        rotation = torch.tensor([[[0., -1, 0], [1, 0, 0], [0, 0, 1]]]).repeat(3, 1, 1)
        delta = base_delta(torch.tensor([[1., 0, 1], [0, -1, -1], [1, 1, 0]]), rotation, 0.04, 0.1)
        torch.testing.assert_close(delta[:2], torch.tensor([[0., .04, .1], [.04, 0, -.1]]))
        torch.testing.assert_close(delta[:, :2].norm(dim=-1), torch.full((3,), .04))

    def test_blocked_base_target_does_not_accumulate(self):
        base = MobileBase.__new__(MobileBase)
        base._entity = SimpleNamespace(data=SimpleNamespace(joint_pos=torch.tensor([[1., 2, 0]])))
        base._env = SimpleNamespace(sim=SimpleNamespace(data=SimpleNamespace(site_xmat=torch.eye(3).reshape(1, 1, 9))))
        base.cfg = SimpleNamespace(translation_m=.04, yaw_rad=.1)
        base.ids, base.base = [0, 1, 2], 0
        base._raw, base.command = torch.zeros(1, 3), torch.zeros(1, 3)
        for _ in range(5):  # The measured pose never moves, as when pushing a table.
            base.process_actions(torch.tensor([[1., 0, 1]]))
        torch.testing.assert_close(base.command, torch.tensor([[1.04, 2, .1]]))

    def test_base_reset_only_changes_selected_references(self):
        base = MobileBase.__new__(MobileBase)
        base._entity = SimpleNamespace(data=SimpleNamespace(joint_pos=torch.tensor([[1., 2, 3], [4, 5, 6]])))
        base.ids = [0, 1, 2]
        base._raw = torch.ones(2, 3)
        base.command = torch.zeros(2, 3)
        base.reset(torch.tensor([1]))
        torch.testing.assert_close(base.command, torch.tensor([[0., 0, 0], [4, 5, 6]]))
        torch.testing.assert_close(base._raw, torch.tensor([[1., 1, 1], [0, 0, 0]]))

    def test_moving_dls_target_follows_translation_and_rotation_of_base(self):
        dls = BaseFrameDLS.__new__(BaseFrameDLS)
        dls.cfg = SimpleNamespace(moving_base=True)
        rotation = torch.tensor([[[0., -1, 0], [1, 0, 0], [0, 0, 1]]])
        dls._env = SimpleNamespace(sim=SimpleNamespace(data=SimpleNamespace(
            site_xmat=rotation[:, None], site_xpos=torch.tensor([[[2., 3, 0]]]),
        )), cfg=SimpleNamespace(decimation=25))
        dls.base_id = 0
        dls.local_position = torch.tensor([[1., 0, .5]])
        dls.local_rotation = torch.eye(3)[None]
        dls._desired_pos = torch.zeros(1, 3)
        dls._desired_quat = torch.zeros(1, 4)
        dls._joint_ids = [0]
        dls._joint_lower, dls._joint_upper = -1., 1.
        dls.velocity_step = torch.tensor([.001])
        dls.q_command = torch.zeros(1, 1)
        dls.limit_fraction = torch.zeros(1)
        dls._entity = SimpleNamespace(data=SimpleNamespace(joint_pos=torch.zeros(1, 1)), set_joint_position_target=Mock())
        dls.compute_dq = Mock(return_value=torch.zeros(1, 1))
        dls.apply_actions()
        torch.testing.assert_close(dls._desired_pos, torch.tensor([[2., 4, .5]]))
        from mjlab.utils.lab_api.math import matrix_from_quat
        torch.testing.assert_close(matrix_from_quat(dls._desired_quat), rotation)

    def test_boundary_checks_whole_robot_target_and_rotated_geometry(self):
        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody>
          <body name="robot/robot_0/base" pos="0 0 .2"><freejoint/>
            <geom type="box" size=".1 .3 .1"/></body>
          <body name="cube" pos="0 0 .2"><freejoint/><geom size=".02"/></body>
          <body name="unrelated" pos="10 0 .2"><geom size="1"/></body>
        </worldbody></mujoco>""")
        data = mujoco.MjData(model)
        boundary = LocalBoundary(model, "cube", [0, 0], 1., "cpu")
        mujoco.mj_forward(model, data)
        self.assertFalse(boundary.outside(data).item())
        data.qpos[0] = .8
        mujoco.mj_forward(model, data)
        self.assertFalse(boundary.outside(data).item())
        data.qpos[3:7] = [2 ** -.5, 0, 0, 2 ** -.5]
        mujoco.mj_forward(model, data)
        self.assertTrue(boundary.outside(data).item())
        data.qpos[0] = 0
        data.qpos[7] = 1.1
        mujoco.mj_forward(model, data)
        self.assertTrue(boundary.outside(data).item())

    def test_mobile_drift_is_allowed_and_boundary_remains_terminal(self):
        task = TaskState.__new__(TaskState)
        task.fixed_state = lambda: torch.tensor([[1., 2, 2., .2]])
        task.fixed_reference = torch.zeros(1, 4)
        task.max_drift = torch.zeros(1)
        task.max_yaw_drift = torch.zeros(1)
        task.max_lift_drift = torch.zeros(1)
        task.max_base_speed = torch.zeros(1)
        task.max_base_yaw_speed = torch.zeros(1)
        task.out_of_bounds = torch.zeros(1, dtype=torch.bool)
        task.mobile_base = True
        task.base_velocity = lambda: torch.tensor([[.1, 0, .2]])
        task.env = SimpleNamespace(sim=SimpleNamespace(data=None))
        task.boundary = Mock()
        task.boundary.outside.return_value = torch.tensor([False])
        self.assertFalse(task.record_base_drift().item())
        task.boundary.outside.return_value = torch.tensor([True])
        self.assertTrue(task.record_base_drift().item())

    def test_mobile_observation_and_sac_replay_save_load(self):
        n = 4
        zero = torch.zeros(n, 3)
        rotation = torch.eye(3).repeat(n, 1, 1)
        state = FlatObservationState(
            poses=(zero, rotation, zero, rotation, zero, rotation),
            position_error=zero, rotation_error=zero, arm_q=torch.zeros(n, 7), lift_q=torch.zeros(n, 1),
            arm_qdot=torch.zeros(n, 7), lift_qdot=torch.zeros(n, 1), cube_linear=zero, cube_angular=zero,
            tcp_linear=zero, tcp_angular=zero, gripper_width=torch.zeros(n, 1),
            gripper_velocity=torch.zeros(n, 1), last_action=torch.ones(n, 10), base_velocity=zero + .5,
        )
        obs = build_flat_observation(state, cube_size=.04, lift_range=[0, .306], open_grip=.072, world_state=True)
        self.assertEqual(obs.shape, (4, 75))
        self.assertEqual(sum(size for _, size, _ in flat_fields(75)), 75)
        torch.testing.assert_close(obs[:, -3:], zero + .5)
        replay = Replay(16, obs, action_dim=10)
        replay.add(Transition(obs, torch.zeros(n, 10), torch.ones(n), obs + .1,
                              torch.tensor([True, False, False, True]), torch.zeros(n, dtype=torch.bool)))
        agent = SAC(75, "cpu", SACConfig(target_entropy=-10), mode="flat", action_dim=10)
        losses = agent.update(replay.sample(n))
        self.assertTrue(all(torch.isfinite(value) for value in losses.values()))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mobile.pt"
            agent.save(path, metadata={}, transitions=4)
            restored, saved = SAC.load(path, "cpu")
            frozen, _ = SACInference.load(path, "cpu")
            for actor in (restored.actor, frozen.actor):
                torch.testing.assert_close(actor.sample(obs, True)[0], agent.actor.sample(obs, True)[0])
                self.assertEqual(actor.action_dim, 10)
            saved["action_definition"] = ("wrong",)
            torch.save(saved, path)
            for load in (SAC.load, SACInference.load):
                with self.assertRaisesRegex(ValueError, "action definition"):
                    load(path, "cpu")
            old = SAC(69, "cpu", mode="flat")
            old.save(path, metadata={}, transitions=0)
            saved = torch.load(path, weights_only=False)
            del saved["action_dim"], saved["action_definition"]
            torch.save(saved, path)
            self.assertEqual(SAC.load(path, "cpu")[0].actor.action_dim, 7)
