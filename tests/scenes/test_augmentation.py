"""Measured asset footprints and actual settled support checks."""
import unittest
from types import SimpleNamespace
import mujoco
import numpy as np
from lastmile_dataflow.runtime.construction.preparation import SettleConfig, settle_scene
from lastmile_dataflow.scenes.augmentation.checks import load_support
from lastmile_dataflow.scenes.augmentation.regions import rotated_placement, support_regions
from lastmile_dataflow.scenes.build.validation import PhysicalLimits


def config():
    return PhysicalLimits()


class GeometryTests(unittest.TestCase):
    def test_off_center_root_and_rotation(self):
        points = np.array([[1, 2, -0.2], [3, 4, 0.8]])
        for yaw in (0.0, np.pi / 2):
            pose, placed = rotated_placement(points, [7.0, 9.0], yaw, 0.5, 0.003)
            np.testing.assert_allclose((placed[:, :2].min(0) + placed[:, :2].max(0)) / 2, [7, 9])
            self.assertAlmostEqual(placed[:, 2].min(), 0.503)
            self.assertEqual(len(pose["quaternion_wxyz"]), 4)

    def test_surface_coverage_rechecked_after_settle_not_from_flag(self):
        xml = """<mujoco><worldbody><geom name="floor" type="plane" size="0 0 .1"/>
        <body name="table" pos="0 0 .4"><geom name="table_top" type="box" size=".3 .3 .04"/></body>
        <body name="procedural_obstacle_0" pos="0 0 .493"><freejoint name="object_free"/>
        <geom name="object_geom" type="box" size=".05 .05 .05" mass=".1"/></body></worldbody></mujoco>"""
        m = mujoco.MjModel.from_xml_string(xml)
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        sim = SimpleNamespace(
            model=m,
            data=d,
            started=False,
            closed=False,
            robot=SimpleNamespace(execution_started=False, config=SimpleNamespace(namespace="robot_0/")),
            state_vector=lambda: np.r_[d.qpos, d.qvel, d.ctrl],
        )
        settle_scene(sim, SettleConfig())
        p = {
            "kind": "surface",
            "support_geom": "table_top",
            "support_instance": "table",
            "support_height_m": 0.44,
            "surface_coverage_verified": True,
        }
        self.assertTrue(load_support(sim, [p], config())["valid"])
        q = int(m.jnt_qposadr[m.joint("object_free").id])
        d.qpos[q] = 0.29  # center is on table, but the settled footprint extends beyond edge
        mujoco.mj_forward(m, d)
        report = load_support(sim, [p], config())
        self.assertFalse(report["valid"])
        self.assertFalse(report["objects"][0]["coverage"])

    def test_target_plane_not_leg_tops_or_lower_shelves(self):
        from types import SimpleNamespace

        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody><body name="table">
        <geom name="top" type="box" pos="0 0 .75" size=".5 .8 .02"/>
        <geom name="leg" type="box" pos=".4 .6 .35" size=".05 .05 .35"/>
        <geom name="shelf" type="box" pos="0 0 .3" size=".4 .7 .02"/>
        </body></worldbody></mujoco>""")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        regions = support_regions(
            SimpleNamespace(model=model, data=data),
            {"anchor_world": [0, 0, 0.82], "support_obstacles": [{"body": "table"}]},
        )
        self.assertEqual([r.geom for r in regions], ["top"])
