"""Visibility evidence rules and temporary visual edits; pixel counts are fixtures."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.data.rendering.head_views import (
    TaskVisibility, aggregate_visibility, ray_visibility, roi_geoms,
)
from lastmile_dataflow.scenes.dataset_config import SceneStationConfig


def scene(blocked=True):
    hidden = ''.join(f'<geom name="hidden{i}" type="sphere" size=".01" rgba="0 0 0 0" pos="4 0 0"/>' for i in range(20))
    blocker = '<geom name="blocker" type="box" size=".3 .3 .03" pos="0 0 1"/>' if blocked else ''
    model = mujoco.MjModel.from_xml_string(f'''<mujoco><worldbody>{hidden}{blocker}
        <body name="target" pos="0 0 .5"><geom name="target_geom" type="box" size=".1 .1 .1"/>
        <body name="handle" pos="0 0 .1"><geom name="handle_geom" type="box" size=".02 .02 .02"/></body></body>
        <camera name="left" pos="0 0 2"/><camera name="right" pos="0 0 2"/><camera name="wrist" pos="0 0 2"/>
        </worldbody></mujoco>''')
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    robot = SimpleNamespace(config=SimpleNamespace(cameras=('left', 'right', 'wrist')),
                            camera_names={name: name for name in ('left', 'right', 'wrist')}, execution_started=False)
    return SimpleNamespace(model=model, data=data, robot=robot, started=False, closed=False)


class FixtureRenderer:
    """Real MjvScene packing, synthetic segmentation; no OpenGL acceptance."""
    def __init__(self, model, *, height, width):
        self.model = model
        self.scene = mujoco.MjvScene(model, maxgeom=100)
        self.height, self.width = height, width
        self.fail_camera = None
        self.empty = False
        self.geometry_counts = []

    def update_scene(self, data, camera):
        self.camera = camera
        view = mujoco.MjvCamera()
        view.type = mujoco.mjtCamera.mjCAMERA_FIXED
        view.fixedcamid = camera
        mujoco.mjv_updateScene(self.model, data, mujoco.MjvOption(), None, view,
                             mujoco.mjtCatBit.mjCAT_ALL, self.scene)

    def render(self):
        if self.camera == self.fail_camera:
            raise RuntimeError('synthetic camera failure')
        geoms = self.scene.geoms[:self.scene.ngeom]
        assert all(g.segid < self.scene.ngeom for g in geoms)
        self.geometry_counts.append(self.scene.ngeom)
        result = np.full((self.height, self.width, 2), -1, dtype=np.int32)
        if self.empty:
            return result
        target = self.model.geom('target_geom').id
        blocker = self.model.geom('blocker').id
        count = 4 if any(g.objid == blocker for g in geoms) else 16
        result.reshape(-1, 2)[:count] = [target, int(mujoco.mjtObj.mjOBJ_GEOM)]
        return result

    def enable_segmentation_rendering(self):
        pass

    def disable_segmentation_rendering(self):
        pass

    def close(self):
        pass


class VisibilityTests(unittest.TestCase):
    def test_unknown_camera_prevents_low_evidence_but_known_high_bound_is_retained(self):
        cameras = {'left': {'fraction': 0.}, 'right': {'fraction': None}, 'wrist': {'fraction': 1.}}
        result = aggregate_visibility(cameras)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['value'])
        self.assertEqual(result['known_max'], 1.)
        cameras['right']['fraction'] = 0.
        result = aggregate_visibility(cameras)
        self.assertTrue(result['complete'])
        self.assertEqual(result['value'], 1.)

    def test_open_roi_is_handle_and_missing_interaction_roi_is_unknown(self):
        sim = scene()
        pick = roi_geoms(sim, {'operation': 'pick', 'target_body': 'target'})
        opened = roi_geoms(sim, {'operation': 'open', 'target_body': 'target', 'handle_body': 'handle'})
        self.assertIn(sim.model.geom('target_geom').id, pick)
        self.assertEqual(opened, [sim.model.geom('handle_geom').id])
        self.assertEqual(roi_geoms(sim, {'operation': 'open', 'target_body': 'target', 'handle_body': 'target'}), [])

    def test_reference_uses_same_roi_and_only_changes_temporary_render_scene(self):
        sim = scene()
        before = {key: getattr(sim.model, key).copy() for key in ('geom_rgba', 'geom_contype', 'geom_conaffinity')}
        position = sim.data.qpos.copy()
        original_samples = sim.model.vis.quality.offsamples
        cfg = SceneStationConfig(width=8, height=8, min_reference_pixels=8)
        with patch('lastmile_dataflow.data.rendering.head_views.mujoco.Renderer', FixtureRenderer):
            with TaskVisibility(sim, {'operation': 'pick', 'target_body': 'target'}, cfg) as visibility:
                self.assertIsNot(visibility.render_model, sim.model)
                self.assertEqual(visibility.render_model.vis.quality.offsamples, 0)
                self.assertEqual(sim.model.vis.quality.offsamples, original_samples)
                result = visibility.measure()
                self.assertEqual(result['value'], .25)
                self.assertTrue(result['complete'])
                self.assertTrue(all(row['reference_pixels'] == 16 for row in result['cameras'].values()))
                counts = visibility.renderer.geometry_counts
                self.assertTrue(all(a > b for a, b in zip(counts[::2], counts[1::2])))
                visibility.renderer.fail_camera = sim.model.camera('right').id
                result = visibility.measure()
                self.assertIsNone(result['value'])
                self.assertEqual(result['known_max'], .25)
                self.assertIsNone(result['cameras']['right']['fraction'])
        for key, value in before.items():
            np.testing.assert_array_equal(getattr(sim.model, key), value)
        np.testing.assert_array_equal(sim.data.qpos, position)
        self.assertEqual(sim.model.vis.quality.offsamples, original_samples)

    def test_small_reference_is_unknown_and_proven_out_of_view_is_zero(self):
        sim = scene()
        cfg = SceneStationConfig(width=8, height=8, min_reference_pixels=32)
        with patch('lastmile_dataflow.data.rendering.head_views.mujoco.Renderer', FixtureRenderer):
            with TaskVisibility(sim, {'operation': 'pick', 'target_body': 'target'}, cfg) as visibility:
                result = visibility.measure()
                self.assertIsNone(result['value'])
                self.assertTrue(all(row['status'] == 'insufficient_projection' for row in result['cameras'].values()))
                sim.model.cam_pos[:, 2] = -2.
                mujoco.mj_forward(sim.model, sim.data)
                visibility.renderer.empty = True
                result = visibility.measure()
                self.assertEqual(result['value'], 0.)
                self.assertTrue(all(row['status'] == 'out_of_view' for row in result['cameras'].values()))

    def test_ray_proxy_observes_blocker_without_rendering(self):
        cfg = SceneStationConfig(width=256, height=256)
        task = {'operation': 'pick', 'target_body': 'target'}
        clear = ray_visibility(scene(False), task, cfg)
        blocked = ray_visibility(scene(True), task, cfg)
        self.assertGreater(clear['value'], blocked['value'])
        self.assertEqual(blocked['value'], 0.)
