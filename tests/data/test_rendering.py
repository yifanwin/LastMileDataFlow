"""相机取景、可见性策略与分析帧。"""

import tempfile
import unittest

import numpy as np

from lastmile_dataflow.data.rendering.analysis_video import AnalysisVideo
from lastmile_dataflow.data.rendering.edit_views import CameraRig, ViewConfig
from lastmile_dataflow.data.rendering.third_person import ThirdPersonCamera
from tests.helpers import make_sim


class ViewTests(unittest.TestCase):
    def test_union_fit_and_rad_adapter(self):
        rig = CameraRig.fit([[0, 0, 0], [10, 0, 1]], ViewConfig(width=320, height=240))
        self.assertEqual(rig.lookat, [5.0, 0.0, 0.5])
        self.assertGreater(rig.distance_m, 10)
        self.assertAlmostEqual(rig.camera(1).azimuth, 45)
        self.assertLess(rig.camera(0).elevation, -89)
        with self.assertRaises(ValueError):
            CameraRig.fit([[float("nan"), 0, 0]])


def station(name, xy, geometry="valid"):
    return {"station_id": name, "xy": xy, "geometry": geometry}


class ObserverAndTimingTests(unittest.TestCase):
    def test_reusable_observer_fits_robot_target_without_state_or_model_changes(self):
        with tempfile.TemporaryDirectory() as d:
            sim, _ = make_sim(d)
            q = sim.data.qpos.copy()
            fovy = float(sim.model.vis.global_.fovy)
            cam = sim.third_person_camera = ThirdPersonCamera()
            fit, robot, target = cam.framing(sim, 320, 240)
            self.assertTrue(robot and target)
            self.assertGreater(fit.distance_m, 0.0)
            np.testing.assert_array_equal(q, sim.data.qpos)
            self.assertEqual(fovy, float(sim.model.vis.global_.fovy))
            sim.close()

    def test_analysis_fourth_frame_does_not_mutate_three_robot_frames(self):
        frames = {
            name: np.zeros((32, 32, 3), dtype=np.uint8)
            for name in ("head_camera", "wrist_camera_l", "wrist_camera_r", "third_person_camera")
        }
        view = AnalysisVideo({"anchor_world": [0, 0, 1], "instruction": "fixture only"}, station("s", [0, 0]))
        image = view.frame(
            frames, phase="navigation", time_s=1.0, base_xy=[0.1, 0], target_height=1.0, initial_height=1.0
        )
        self.assertEqual(image.shape, (720, 1280, 3))
        self.assertTrue(image.any())
        self.assertTrue(all(not rgb.any() for rgb in frames.values()))


