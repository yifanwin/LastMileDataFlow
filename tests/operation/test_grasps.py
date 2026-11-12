"""Shared grasp diversity and corpus identity."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from lastmile_dataflow.operation.grasps import load_grasps, select_grasps


class GraspTests(unittest.TestCase):
    def test_rotations_separate_identical_positions_and_no_padding(self):
        poses = np.repeat(np.eye(4)[None], 3, axis=0)
        poses[1, :3, :3] = np.diag([-1, -1, 1])
        selected = select_grasps([0, 1, 2], poses, np.zeros(3), 2)
        self.assertEqual(selected, [0, 1])
        self.assertEqual(len(select_grasps([0, 1, 2], poses, np.zeros(3))), 3)

    def test_nearest_64_and_stable_fps(self):
        poses = np.repeat(np.eye(4)[None], 100, axis=0)
        poses[:, 0, 3] = np.arange(100)
        first = select_grasps(list(range(100)), poses, np.zeros(3))
        self.assertEqual(len(first), 16)
        self.assertEqual(first[:2], [0, 63])
        self.assertEqual(first, select_grasps(list(range(100)), poses, np.zeros(3)))
        self.assertTrue(all(i < 64 for i in first))

    def test_corpus_cap_and_original_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "grasps.npz"
            poses = np.repeat(np.eye(4)[None], 2048, axis=0)
            np.savez(path, transforms=poses)
            candidates = load_grasps(path, "target")
        self.assertEqual(len(candidates), 1024)
        self.assertEqual(candidates[-1]["row"], 2047)
        self.assertEqual(candidates[0]["source"], str(path))
