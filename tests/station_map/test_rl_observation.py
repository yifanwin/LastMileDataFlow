"""Geometry blocks retain table openings and track dynamic target motion."""

from types import SimpleNamespace
import unittest

import mujoco
import numpy as np
import torch

from lastmile_dataflow.station_map.rl.env.observation import GeometryObservation, FLAT_FIELDS, policy_observation
from lastmile_dataflow.station_map.rl.env.terms import TaskState
from lastmile_dataflow.sim.model.geometry import geom_points


class GeometryTests(unittest.TestCase):
    def test_flat_policy_shape_and_dispatch(self):
        self.assertEqual(sum(dim for _, dim, _ in FLAT_FIELDS), 65)
        flat = torch.zeros(2, 65)
        self.assertIs(policy_observation({"policy": {"flat": flat}}, "flat"), flat)
        with self.assertRaises(ValueError):
            policy_observation({}, "unknown")

    def test_flat_state_field_values_and_base_rotation(self):
        task = TaskState.__new__(TaskState)
        task.device = "cpu"
        task.env = SimpleNamespace(num_envs=1, research_config={"scene": {"cube": {"size_m": 0.04}}},
                                   cfg=SimpleNamespace(episode_length_s=10))
        yaw = torch.tensor([[[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]])
        identity = torch.eye(3)[None]
        task.poses = lambda: (torch.tensor([[1., 2., 3.]]), identity,
                              torch.tensor([[1., 1., 1.]]), yaw,
                              torch.tensor([[2., 1., 2.]]), yaw)
        task.goal_error = lambda: (torch.tensor([[0.1, 0.2, 0.3]]), torch.tensor([[torch.pi / 2, -torch.pi / 4, 0.]]))
        task.target_velocity = lambda: (torch.tensor([[0.7, 0.8, 0.9]]), torch.tensor([[1.1, 1.2, 1.3]]))
        task.last_action = torch.arange(7)[None].float() / 7
        task.tcp_velocity = lambda: (torch.tensor([[0.1, 0.2, 0.3]]), torch.tensor([[0.4, 0.5, 0.6]]))
        task.needs_baseline = torch.tensor([True])
        task.previous_distance = torch.zeros(1)
        task.previous_angle = torch.zeros(1)
        task.height_reference = torch.zeros(1)
        task.robot = SimpleNamespace(data=SimpleNamespace(
            joint_pos=torch.tensor([[0., 0., 0., 0., 0., 0., 0., 0.5, 0.02, -0.02]]),
            joint_vel=torch.tensor([[0., 0., 0., 0., 0., 0., 0., 0.05, 0.02, -0.02]]),
        ))
        task.arm = list(range(7))
        task.physical_fixed = False
        task.base_joints = [0, 1, 2]
        task.lift = [7]
        task.fingers = [8, 9]
        task.lift_range = torch.tensor([0., 1.])
        task.open_grip = 0.08
        task.elapsed = torch.tensor([2.])
        state = task.flat_state()
        self.assertEqual(state.shape, (1, 65))
        values = {}
        offset = 0
        for name, dim, _ in FLAT_FIELDS:
            values[name] = state[0, offset:offset + dim]
            offset += dim
        expected = {
            "cube_position_base": [0., -1., 1.],
            "cube_rotation_columns_6d": [1., 0., 0., 0., 1., 0.],
            "cube_size_local": [0.04] * 3,
            "cube_bottom_height_base": [0.98],
            "target_position_error_base": [5., 10., 15.],
            "target_rotation_error_base": [4.5, -2.25, 0.],
            "cube_linear_velocity_base": [0.7, 0.8, 0.9],
            "cube_angular_velocity_base": [1.1, 1.2, 1.3],
            "arm_and_lift_q": [0.] * 7 + [0.5],
            "arm_and_lift_qdot": [0.] * 7 + [1.],
            "tcp_position_base": [1., 0., 2.],
            "tcp_rotation_columns_6d": [0., -1., 0., 1., 0., 0.],
            "tcp_linear_velocity_base": [0.1, 0.2, 0.3],
            "tcp_angular_velocity_base": [0.4, 0.5, 0.6],
            "gripper_aperture": [0.5],
            "gripper_velocity": [0.2],
            "previous_action": task.last_action[0].tolist(),
        }
        self.assertEqual(list(values), list(expected))
        for name, value in expected.items():
            torch.testing.assert_close(values[name], torch.tensor(value), msg=name)
        self.assertAlmostEqual(float(task.height_reference[0]), 2.)
        self.assertAlmostEqual(float(task.previous_distance[0]), (0.14 ** 0.5))
        self.assertAlmostEqual(float(task.previous_angle[0]), torch.pi * (5 ** 0.5) / 4, places=6)
        # The error norm reaches one at the task tolerance; signs are retained.
        task.goal_error = lambda: (torch.tensor([[-0.02, 0., 0.]]),
                                  torch.tensor([[0., torch.pi / 9, 0.]]))
        normalized = task.flat_state()
        torch.testing.assert_close(normalized[0, 13:19], torch.tensor([-1., 0., 0., 0., 1., 0.]))
        # World state appends raw XY/yaw and lift metres without changing old fields.
        task.env.research_config["observation"] = {"world_state": True}
        extended = task.flat_state()
        self.assertEqual(extended.shape, (1, 69))
        torch.testing.assert_close(extended[:, :65], normalized)
        torch.testing.assert_close(extended[0, 65:], torch.tensor([1., 1., torch.pi / 2, 0.5]))
        task.env.research_config["observation"]["world_state"] = False
        # A tilted cube's bottom is its lowest corner, not center minus size/2.
        tilt = torch.tensor([[[1., 0., 0.], [0., 2**-0.5, -2**-0.5], [0., 2**-0.5, 2**-0.5]]])
        poses = task.poses()
        task.poses = lambda: (*poses[:5], yaw @ tilt)
        tilted = task.flat_state()
        self.assertAlmostEqual(float(tilted[0, 12]), 1 - 0.02 * 2**0.5, places=6)
        # Re-reading observations during a rollout must not move the height baseline.
        self.assertAlmostEqual(float(task.height_reference[0]), 2.)

    def test_target_velocity_accounts_for_com_and_moving_base(self):
        task = TaskState.__new__(TaskState)
        task.target, task.base = 1, 0
        task.model = SimpleNamespace(body_rootid=[0, 1], site_bodyid=[0])
        identity = torch.eye(3)[None]
        yaw = torch.tensor([[[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]])
        position = torch.tensor([[1., 0., 0.]])
        task.poses = lambda: (position, identity, torch.zeros(1, 3), yaw, position, identity)
        task.env = SimpleNamespace(sim=SimpleNamespace(data=SimpleNamespace(
            xpos=torch.stack((torch.zeros(1, 3), position), 1),
            subtree_com=torch.zeros(1, 2, 3),
            cvel=torch.tensor([[[0., 0., 1., 1., 0., 0.], [0., 0., 2., 3., 0., 0.]]]),
        )))
        linear, angular = task.target_velocity()
        torch.testing.assert_close(linear, torch.tensor([[1., -2., 0.]]))
        torch.testing.assert_close(angular, torch.tensor([[0., 0., 1.]]))

    def test_source_cylinder_bounds_with_numpy_geom_enum(self):
        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody>
          <geom type="cylinder" size=".01 .08" pos="1 2 3"/>
          </worldbody></mujoco>""")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        points = geom_points(SimpleNamespace(model=model, data=data), 0)
        np.testing.assert_allclose(points.min(0), [0.99, 1.99, 2.92])
        np.testing.assert_allclose(points.max(0), [1.01, 2.01, 3.08])

    def test_blocks_dynamic_target_and_base_yaw(self):
        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody>
          <body name="table"><geom type="box" size=".5 .5 .02" pos="0 0 .7"/>
          <geom type="box" size=".03 .03 .35" pos=".4 .4 .35"/></body>
          <body name="cup" pos="0 0 .8"><freejoint/>
          <geom type="cylinder" size=".03 .05" mass=".1"/></body>
          </worldbody></mujoco>""")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        geometry = GeometryObservation(model, model.body("cup").id, np.array([0, 0, 0.8]), 2.0, "cpu")
        self.assertEqual(geometry.capacity, 3)

        def tensors():
            return SimpleNamespace(
                **{
                    name: torch.tensor(getattr(data, name)[None], dtype=torch.float32)
                    for name in ("geom_xpos", "geom_xmat", "xpos", "xmat", "cvel", "subtree_com")
                }
            )

        base = torch.zeros(1, 3)
        rotation = torch.eye(3)[None]
        first = geometry.compute(tensors(), base, rotation)
        self.assertEqual(first.shape, (1, 3, 24))
        self.assertEqual(first[0, :, :4].argmax(-1).tolist(), [0, 3, 3])
        # Top thickness stays .04 m; the gap underneath is not filled in.
        self.assertAlmostEqual(float(first[0, 1, 9]), 0.04, places=5)
        data.qpos[0] = 0.2
        mujoco.mj_forward(model, data)
        yaw = torch.tensor([[[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]])
        changed = geometry.compute(tensors(), base, yaw)
        torch.testing.assert_close(changed[0, 0, 4:7], torch.tensor([0.0, -0.2, 0.8]))


class RealObjectGeometryTests(unittest.TestCase):
    def test_offset_mesh_and_child_collision_geometry_match_mujoco_when_tilted(self):
        from lastmile_dataflow.station_map.rl.env.terms import target_bottom_height
        from lastmile_dataflow.sim.model.geometry import body_points
        model = mujoco.MjModel.from_xml_string("""<mujoco>
          <asset><mesh name="tetra" vertex="0 0 0  .04 0 0  0 .03 0  0 0 .08"/></asset>
          <worldbody><body name="target" pos="1 2 3"><freejoint/>
            <geom type="mesh" mesh="tetra" pos=".02 -.03 -.07"/>
            <body pos="-.04 0 0"><geom type="box" size=".01 .02 .03"/></body>
            <geom type="box" size="1 1 1" contype="0" conaffinity="0" mass="0"/>
          </body></worldbody></mujoco>""")
        data = mujoco.MjData(model)
        task = TaskState.__new__(TaskState)
        task.model, task.device, task.target = model, "cpu", model.body("target").id
        task.initial_qpos = torch.tensor(data.qpos.copy(), dtype=torch.float32)
        task.prepare_target_geometry()
        self.assertTrue((task.target_size < .2).all())  # Visual-only box is excluded.
        for quaternion in ([1., 0., 0., 0.], [0.9238795325, 0., 0.3826834324, 0.], [0., 1., 0., 0.]):
            data.qpos[3:7] = quaternion
            mujoco.mj_forward(model, data)
            points = body_points(SimpleNamespace(model=model, data=data), task.target)
            position = torch.tensor(data.xpos[task.target][None], dtype=torch.float32)
            rotation = torch.tensor(data.xmat[task.target].reshape(1, 3, 3), dtype=torch.float32)
            lowest = target_bottom_height(task, position, rotation)
            self.assertAlmostEqual(float(lowest), points[:, 2].min(), places=6)

    def test_real_object_builder_preserves_69d_and_uses_exact_bottom(self):
        from lastmile_dataflow.station_map.rl.env.observation import FlatObservationState, build_flat_observation, flat_fields
        identity, zero = torch.eye(3)[None], torch.zeros(1, 3)
        state = FlatObservationState(
            poses=(zero, identity, zero, identity, zero, identity),
            position_error=zero, rotation_error=zero,
            arm_q=torch.zeros(1, 7), lift_q=torch.zeros(1, 1),
            arm_qdot=torch.zeros(1, 7), lift_qdot=torch.zeros(1, 1),
            cube_linear=zero, cube_angular=zero, tcp_linear=zero, tcp_angular=zero,
            gripper_width=torch.zeros(1, 1), gripper_velocity=torch.zeros(1, 1), last_action=torch.zeros(1, 7))
        obs = build_flat_observation(state, target_size=torch.tensor([.04, .06, .1]),
                                     target_bottom_height=torch.tensor([-.07]), lift_range=[0, 1],
                                     open_grip=.08, world_state=True)
        self.assertEqual(obs.shape, (1, 69))
        torch.testing.assert_close(obs[0, 9:13], torch.tensor([.04, .06, .1, -.07]))
        self.assertEqual(flat_fields(69, real_object=True)[0][0], "target_position_base")
