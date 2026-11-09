"""Geometry blocks retain table openings and track dynamic target motion."""
from types import SimpleNamespace
import unittest

import mujoco
import numpy as np
import torch

from lastmile_dataflow.scenes.rl_observation import GeometryObservation
from lastmile_dataflow.scenes.geometry import geom_points


class GeometryTests(unittest.TestCase):
    def test_source_cylinder_bounds_with_numpy_geom_enum(self):
        model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
          <geom type="cylinder" size=".01 .08" pos="1 2 3"/>
          </worldbody></mujoco>''')
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        points = geom_points(SimpleNamespace(model=model, data=data), 0)
        np.testing.assert_allclose(points.min(0), [.99, 1.99, 2.92])
        np.testing.assert_allclose(points.max(0), [1.01, 2.01, 3.08])

    def test_blocks_dynamic_target_and_base_yaw(self):
        model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
          <body name="table"><geom type="box" size=".5 .5 .02" pos="0 0 .7"/>
          <geom type="box" size=".03 .03 .35" pos=".4 .4 .35"/></body>
          <body name="cup" pos="0 0 .8"><freejoint/>
          <geom type="cylinder" size=".03 .05" mass=".1"/></body>
          </worldbody></mujoco>''')
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        geometry = GeometryObservation(model, model.body('cup').id, np.array([0, 0, .8]), 2., 'cpu')
        self.assertEqual(geometry.capacity, 3)
        def tensors():
            return SimpleNamespace(**{name: torch.tensor(getattr(data, name)[None], dtype=torch.float32)
                                     for name in ('geom_xpos', 'geom_xmat', 'xpos', 'xmat', 'cvel', 'subtree_com')})
        base = torch.zeros(1, 3)
        rotation = torch.eye(3)[None]
        first = geometry.compute(tensors(), base, rotation)
        self.assertEqual(first.shape, (1, 3, 24))
        self.assertEqual(first[0, :, :4].argmax(-1).tolist(), [0, 3, 3])
        # Top thickness stays .04 m; the gap underneath is not filled in.
        self.assertAlmostEqual(float(first[0, 1, 9]), .04, places=5)
        data.qpos[0] = .2
        mujoco.mj_forward(model, data)
        yaw = torch.tensor([[[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]])
        changed = geometry.compute(tensors(), base, yaw)
        torch.testing.assert_close(changed[0, 0, 4:7], torch.tensor([0., -.2, .8]))
