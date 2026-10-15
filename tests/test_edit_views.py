"""核心增强视图复用的相机几何。"""
import unittest
from lastmile_dataflow.data.rendering.edit_views import CameraRig, ViewConfig


class ViewTests(unittest.TestCase):
    def test_union_fit_and_rad_adapter(self):
        rig = CameraRig.fit([[0, 0, 0], [10, 0, 1]], ViewConfig(width=320, height=240))
        self.assertEqual(rig.lookat, [5., 0., .5])
        self.assertGreater(rig.distance_m, 10)
        self.assertAlmostEqual(rig.camera(1).azimuth, 45)
        self.assertLess(rig.camera(0).elevation, -89)
        with self.assertRaises(ValueError):
            CameraRig.fit([[float('nan'), 0, 0]])
