"""Full-corpus pose diversity and source-row identity."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from lastmile_dataflow.operation.grasps import load_grasps, select_grasps
from lastmile_dataflow.operation.config import ExecutionConfig
from lastmile_dataflow.operation.control.manipulation import candidate_pool
from lastmile_dataflow.operation.grasp_visualization import _scene_mapping
from types import SimpleNamespace
from unittest.mock import patch


class GraspTests(unittest.TestCase):
    def test_mesh_and_grasps_share_object_to_world_transform(self):
        triangles = np.eye(3)[None]
        poses = np.eye(4)[None]
        poses[0, :3, 3] = [1, 2, 3]
        transform = np.array([[0, -1, 0, 2], [1, 0, 0, 3], [0, 0, 1, 4], [0, 0, 0, 1]])
        mesh_world, poses_world = _scene_mapping(triangles, poses, transform)
        np.testing.assert_allclose(mesh_world, [[[2, 4, 4], [1, 3, 4], [2, 3, 5]]])
        np.testing.assert_allclose(poses_world[0, :3, 3], [0, 4, 7])
        np.testing.assert_allclose(poses_world[0, :3, :3], transform[:3, :3])
        np.testing.assert_array_equal(poses[0, :3, 3], [1, 2, 3])

    def test_rotations_separate_identical_positions_and_no_padding(self):
        poses = np.repeat(np.eye(4)[None], 3, axis=0)
        poses[1, :3, :3] = np.diag([-1, -1, 1])
        self.assertEqual(select_grasps([0, 1, 2], poses, 2), [0, 1])
        self.assertEqual(len(select_grasps([0, 1, 2], poses)), 3)
        self.assertEqual(select_grasps([], []), [])

    def test_full_corpus_seed_and_configurable_count(self):
        poses = np.repeat(np.eye(4)[None], 100, axis=0)
        poses[:, 0, 3] = np.arange(100)
        candidates = list(range(100))
        first = select_grasps(candidates, poses)
        self.assertEqual(len(first), 16)
        self.assertEqual(first[0], int(np.random.default_rng(42).integers(100)))
        self.assertIn(99, first)
        self.assertEqual(first, select_grasps(candidates, poses))
        self.assertNotEqual(first, select_grasps(candidates, poses, seed=43))
        for count in (1, 5, 23, 120):
            pool = select_grasps(candidates, poses, count)
            self.assertEqual(len(pool), min(count, 100))
            self.assertEqual(len(set(pool)), len(pool))
        for count in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                select_grasps(candidates, poses, count)

    def test_shared_rigid_transform_preserves_pool(self):
        rng = np.random.default_rng(9)
        poses = np.repeat(np.eye(4)[None], 80, axis=0)
        poses[:, :3, 3] = rng.normal(size=(80, 3))
        transform = np.eye(4)
        transform[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        transform[:3, 3] = [3, -2, 1]
        candidates = list(range(80))
        self.assertEqual(select_grasps(candidates, poses), select_grasps(candidates, transform @ poses))

    def test_corpus_is_uncapped_unless_explicitly_limited(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "grasps.npz"
            poses = np.repeat(np.eye(4)[None], 2048, axis=0)
            poses[7, 0, 0] = np.nan
            np.savez(path, transforms=poses)
            candidates = load_grasps(path, "target")
            limited = load_grasps(path, "target", max_rows=16)
        self.assertEqual(len(candidates), 2047)
        self.assertNotIn(7, [c["row"] for c in candidates])
        self.assertEqual(candidates[-1]["row"], 2047)
        self.assertEqual(candidates[0]["source"], str(path))
        self.assertEqual(len(limited), 16)
        self.assertEqual(limited[-1]["row"], 2047)

    def test_curobo_uses_shared_fps_and_keeps_forced_replay(self):
        candidates = []
        for row in range(80):
            pose = np.eye(4)
            pose[0, 3] = row / 1000
            candidates.append(dict(pose_local=pose, body="target", row=row, source="fixture"))
        sim = SimpleNamespace(model=SimpleNamespace(body=lambda name: SimpleNamespace(id=1)))
        config = ExecutionConfig(grasp_candidate_order="fps", max_grasp_candidates=23, grasp_fps_seed=7)
        with patch("lastmile_dataflow.operation.control.manipulation.body_pose", return_value=np.eye(4)):
            pool = candidate_pool(sim, config, candidates, seed=100, forced=None)
            other_trial = candidate_pool(sim, config, candidates, seed=200, forced=None)
        expected = select_grasps(candidates, [c["pose_local"] for c in candidates], 23, seed=7)
        self.assertEqual([c["row"] for c in pool], [c["row"] for c in expected])
        self.assertEqual([c["row"] for c in pool], [c["row"] for c in other_trial])
        forced = candidate_pool(sim, config, candidates, 100, dict(grasp_row=79, grasp_source="fixture"))
        self.assertEqual([c["row"] for c in forced], [79])
