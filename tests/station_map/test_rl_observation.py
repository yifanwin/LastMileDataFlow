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
        self.assertEqual(sum(dim for _, dim, _ in FLAT_FIELDS), 45)
        flat = torch.zeros(2, 45)
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
        task.goal_error = lambda: (torch.tensor([[0.1, 0.2, 0.3]]), torch.tensor([[0., 0., 0.]]))
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
        task.fixed = [0, 1, 2, 7]
        task.fingers = [8, 9]
        task.lift_range = torch.tensor([0., 1.])
        task.open_grip = 0.08
        task.elapsed = torch.tensor([2.])
        state = task.flat_state()
        self.assertEqual(state.shape, (1, 45))
        torch.testing.assert_close(state[0, :3], torch.tensor([0., -1., 1.]))
        torch.testing.assert_close(state[0, 6:9], torch.full((3,), 0.04))
        torch.testing.assert_close(state[0, 9:12], torch.tensor([0.1, 0.2, 0.3]))
        self.assertAlmostEqual(float(state[0, 22]), 0.5)
        self.assertAlmostEqual(float(state[0, 30]), 1.0)
        torch.testing.assert_close(state[0, 31:34], torch.tensor([1., 0., 2.]))
        torch.testing.assert_close(state[0, 37:43], torch.tensor([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]))
        self.assertAlmostEqual(float(state[0, 43]), 0.5)
        self.assertAlmostEqual(float(state[0, 44]), 0.2)
        self.assertAlmostEqual(float(task.height_reference[0]), 2.)

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
