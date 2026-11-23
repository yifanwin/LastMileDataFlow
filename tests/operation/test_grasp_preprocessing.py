"""Real mesh rays, source identities and uniform consumption of offline libraries."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.operation.config import ExecutionConfig
from lastmile_dataflow.operation.control.manipulation import candidate_pool
from lastmile_dataflow.operation.grasp_preprocessing import paired_ray_hits
from lastmile_dataflow.operation.grasps import load_grasps, sample_grasps


class PairedRayTests(unittest.TestCase):
    def setUp(self):
        self.model = mujoco.MjModel.from_xml_string('''<mujoco>
          <asset><mesh name="cube" vertex="-.5 -.5 -.5  -.5 -.5 .5  -.5 .5 -.5  -.5 .5 .5
            .5 -.5 -.5  .5 -.5 .5  .5 .5 -.5  .5 .5 .5"/></asset>
          <worldbody><body name="target"><geom type="mesh" mesh="cube"/></body></worldbody>
        </mujoco>''')
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)

    def test_finite_paired_hits_and_invalid_endpoints(self):
        starts = np.array([[-2, 0, 0], [-2, 2, 0], [-2, 0, 0], [-.4, 0, 0], [-2, 0, 0.]])
        ends = np.array([[2, 0, 0], [2, 2, 0], [-1, 0, 0], [.4, 0, 0], [2, 0, 0.]])
        valid = np.array([True, True, True, True, False])
        hits = paired_ray_hits(self.model, self.data, [0], starts, ends, valid)
        np.testing.assert_array_equal(hits, [True, False, False, False, False])

    def test_shared_rigid_transform_preserves_queries(self):
        starts = np.array([[-2., 0, 0], [-2, 2, 0]])
        ends = starts + [4, 0, 0]
        rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
        position = np.array([3., -4, 2])
        self.model.body_pos[1] = position
        mujoco.mju_mat2Quat(self.model.body_quat[1], rotation.ravel())
        mujoco.mj_forward(self.model, self.data)
        hits = paired_ray_hits(self.model, self.data, [0], starts @ rotation.T + position,
                               ends @ rotation.T + position, np.ones(2, dtype=bool))
        np.testing.assert_array_equal(hits, [True, False])

    def test_441_ray_ratio_boundary(self):
        for count, accepted in ((132, False), (133, True)):
            starts = np.tile([-2., 2, 0], (441, 1))
            starts[:count, 1] = 0
            ends = starts + [4, 0, 0]
            hits = paired_ray_hits(self.model, self.data, [0], starts, ends, np.ones(441, dtype=bool))
            self.assertEqual(int(hits.sum()), count)
            self.assertEqual(hits.mean() >= .3, accepted)


class OfflineLibraryTests(unittest.TestCase):
    def test_source_identity_and_uniform_sampling(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "processed.npz"
            poses = np.repeat(np.eye(4)[None], 128, axis=0)
            poses[:, 0, 3] = np.arange(128) / 1000
            rows = np.arange(128) * 3
            np.savez(path, transforms=poses, source_rows=rows, source="original.npz")
            candidates = load_grasps(path, "target")
        self.assertEqual([c["row"] for c in candidates], list(rows))
        self.assertEqual(candidates[0]["source"], "original.npz")
        np.testing.assert_array_equal(candidates[100]["pose_local"], poses[100])
        for count in (1, 16, 128, 200):
            pool = sample_grasps(candidates, count, seed=42)
            self.assertEqual(len(pool), min(count, 128))
            self.assertEqual(len({c["row"] for c in pool}), len(pool))
            self.assertEqual([c["row"] for c in pool], [c["row"] for c in sample_grasps(candidates, count, seed=42)])
        self.assertNotEqual([c["row"] for c in sample_grasps(candidates, 16, seed=42)],
                            [c["row"] for c in sample_grasps(candidates, 16, seed=43)])
        self.assertEqual(sample_grasps([], 16, seed=42), [])
        for count in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                sample_grasps(candidates, count, seed=42)

    def test_calibration_samples_preprocessed_library_without_fps(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "processed.npz"
            poses = np.repeat(np.eye(4)[None], 128, axis=0)
            raw_source = "/assets/Cup_30/raw.npz"
            np.savez(path, transforms=poses, source_rows=np.arange(128) * 2, source=raw_source)
            candidates = [dict(pose_local=np.eye(4), body="target", source=raw_source, row=row) for row in (0, 255)]
            config = ExecutionConfig(max_grasp_candidates=16, grasp_preprocessed_paths={"Cup_30": str(path)})
            with patch("lastmile_dataflow.operation.control.manipulation.select_grasps", side_effect=AssertionError("FPS repeated")):
                first = candidate_pool(None, config, candidates, 100, None)
                second = candidate_pool(None, config, candidates, 200, None)
            expected = sample_grasps(load_grasps(path, "target"), 16, seed=42)
            self.assertEqual([c["row"] for c in first], [c["row"] for c in expected])
            self.assertEqual([c["row"] for c in first], [c["row"] for c in second])
            forced = candidate_pool(None, config, candidates, 200, dict(grasp_row=255, grasp_source=raw_source))
            self.assertEqual([c["row"] for c in forced], [255])


if __name__ == "__main__":
    unittest.main()
