"""相机取景、可见性策略与分析帧。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.data.rendering.analysis_video import AnalysisVideo
from lastmile_dataflow.data.rendering.edit_views import CameraRig, ViewConfig
from lastmile_dataflow.data.rendering.obstacle_views import (
    render_obstacle_pair,
    visibility_pass,
)
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
            cam = sim.enable_third_person()
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


class ObstacleViewTests(unittest.TestCase):
    def test_union_cannot_accept_wall_only_oblique_pairs(self):
        visibility = {}
        for view in ("top", "oblique_a", "oblique_b"):
            visibility["before/" + view] = {"target": 100 if view == "top" else 0}
            visibility["after/" + view] = {
                "target": 100 if view == "top" else 0,
                "a": 100 if view == "top" else 0,
                "b": 100 if view == "top" else 0,
            }
        self.assertFalse(visibility_pass(visibility, ["a", "b"], "target", 24, True))
        for view in ("oblique_a", "oblique_b"):
            visibility["before/" + view]["target"] = 100
            visibility["after/" + view]["a"] = 100
        self.assertTrue(visibility_pass(visibility, ["a", "b"], "target", 24, True))
        self.assertFalse(visibility_pass(visibility, ["a", "b"], "target", 24, False))

    def test_camera_search_includes_pair_without_common_45_degree_view(self):
        calls = []

        def fake_render(before, after, bg, ag, names, folder, **kwargs):
            pair = kwargs["oblique_azimuths_rad"]
            calls.append(pair)
            Path(folder).mkdir(parents=True)
            visibility = {}
            for view, visible in zip(
                ("top", "oblique_a", "oblique_b"),
                (True, not np.isclose(pair[0], np.pi / 4), not np.isclose(pair[1], np.pi / 4)),
            ):
                visibility["before/" + view] = {"target": 100 if visible else 0}
                visibility["after/" + view] = {"target": 100 if visible else 0, "a": 100 if visible else 0}
            return {"visible_pixels_by_instance": visibility, "images": []}

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "lastmile_dataflow.data.rendering.obstacle_views.render_edit_pair", side_effect=fake_render
            ),
        ):
            packet = render_obstacle_pair(
                None,
                None,
                None,
                None,
                {"a"},
                "target",
                set(),
                Path(tmp) / "rgb",
                sample_id="fixture",
                config=ViewConfig(),
                coverage_union=True,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (0.0, -np.pi / 4))
        self.assertEqual(packet["visibility_policy"], "three_view_union_with_each_pair_observed")
