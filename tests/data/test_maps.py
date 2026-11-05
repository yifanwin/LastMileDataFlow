"""支撑物栅格、A* 导航地图与分析视频。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.data.rendering.analysis_video import AnalysisVideo
from lastmile_dataflow.data.visualization.navigation_map import export_navigation_map
from lastmile_dataflow.planning.navigation.astar import scene_grid, search, task_support_obstacles


class SupportMapTests(unittest.TestCase):
    def scene(self):
        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody>
        <body name="table"><geom name="top" type="box" pos="0 0 .8" size=".5 .4 .05"/></body>
        <body name="cup" pos="0 0 .9"><freejoint/><geom type="sphere" size=".05"/></body>
        </worldbody></mujoco>""")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        catalog = [
            {"instance_id": name, "mjcf_body": name, "body_id": model.body(name).id}
            for name in ("table", "cup")
        ]
        return SimpleNamespace(model=model, data=data, catalog=catalog)

    def test_elevated_support_blocks_display_grid_and_inflated_astar(self):
        sim = self.scene()
        task = {"instance_id": "cup", "target_body": "cup", "operation": "pick", "anchor_world": [0, 0, 0.9]}
        obstacles = task_support_obstacles(sim, task)
        self.assertEqual(obstacles[0]["body"], "table")
        with patch("lastmile_dataflow.scenes.stations.initialization.room_triangles", return_value=[]):
            heat = scene_grid(sim, [0, 0], 2.0, 0.05, 0.0, 0.0, support_obstacles=obstacles)
            nav = scene_grid(sim, [0, 0], 2.0, 0.05, 0.2, 0.02, support_obstacles=obstacles)
        self.assertFalse(heat.free[heat.cell([0, 0])])
        self.assertTrue(heat.free[heat.cell([0.6, 0])])
        self.assertFalse(nav.free[nav.cell([0.6, 0])])
        path = search(nav, [-1, 0], [1, 0])
        self.assertIsNotNone(path)
        self.assertTrue(all(abs(p[0]) > 0.5 or abs(p[1]) > 0.4 for p in path["xy"]))
        with tempfile.TemporaryDirectory() as d:
            before = nav.free.copy()
            export_navigation_map(d, nav, [0, 0, 0.9], display_grid=heat)
            np.testing.assert_array_equal(nav.free, before)
            self.assertNotIn("support / obstacle", Path(d, "navigation_grid.svg").read_text())
            self.assertIn("physical_footprints", Path(d, "navigation_display.json").read_text())
            self.assertFalse(Path(d, "index.html").exists())

    def test_navigation_video_has_support_before_path(self):
        obstacle = {"min": [-0.5, -0.4, 0.75], "max": [0.5, 0.4, 0.85]}
        video = AnalysisVideo({"anchor_world": [0, 0, 1], "support_obstacles": [obstacle]}, {"xy": [-1, 0]})
        frame = video.frame(
            {"third_person_camera": np.zeros((200, 300, 3), np.uint8)},
            phase="initialize",
            time_s=0,
            base_xy=[-1, 0],
            target_height=1,
            initial_height=1,
        )
        # Interior of the support rectangle, away from target glyph/text.
        self.assertEqual(frame[571, 1070].tolist(), [73, 61, 45])

    def test_station_circles_use_metric_robot_radius(self):
        video = AnalysisVideo(
            {"anchor_world": [0, 0, 1]}, {"xy": [-1, 0]}, goal={"xy": [1, 0]}, footprint_radius_m=0.4
        )
        frame = video.frame(
            {"third_person_camera": np.zeros((200, 300, 3), np.uint8)},
            phase="initialize",
            time_s=0,
            base_xy=[0, -1],
            target_height=1,
            initial_height=1,
        )
        # 206 pixels / 4 metres: .4 m = 20.6 pixels; circles, not fixed-size dots.
        self.assertEqual(frame[555, 1010].tolist(), [237, 107, 100])
        self.assertEqual(frame[555, 1114].tolist(), [102, 214, 140])
        for radius in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                AnalysisVideo({}, {"xy": [0, 0]}, footprint_radius_m=radius)

