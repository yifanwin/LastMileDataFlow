"""任务发现与抓取候选生成。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.tasks.discovery import discover_tasks, grasp_candidates
from tests.helpers import make_sim


class TaskTests(unittest.TestCase):
    def test_actual_free_joint_and_dimensions_not_category_alone(self):
        with tempfile.TemporaryDirectory() as d:
            sim, robot = make_sim(d)
            sim.catalog[0].update(asset_id="fixture", category="Cup", room_id=1)
            tasks, skipped = discover_tasks(sim, {}, ExecutionConfig(gripper_width_m=0.21))
            self.assertTrue(any(t["operation"] == "pick" for t in tasks))
            sim.close()

    def test_procedural_grasps_are_proper_rotations(self):
        task = {
            "asset_id": "fixture",
            "operation": "pick",
            "target_body": "target",
            "local_bounds": [[-0.01, -0.02, -0.03], [0.01, 0.02, 0.03]],
        }
        with tempfile.TemporaryDirectory() as d:
            candidates = grasp_candidates(task, d)
            self.assertTrue(candidates)
            for row in candidates:
                self.assertAlmostEqual(np.linalg.det(row["pose_local"][:3, :3]), 1.0)


class GraspFallbackTests(unittest.TestCase):
    def test_empty_asset_grasp_array_is_explicit_procedural_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "grasps" / "droid" / "fixture"
            p.mkdir(parents=True)
            np.savez(p / "fixture_grasps_filtered.npz", transforms=np.array([]))
            task = {
                "asset_id": "fixture",
                "operation": "pick",
                "target_body": "target",
                "local_bounds": [[-0.01, -0.02, -0.03], [0.01, 0.02, 0.03]],
            }
            rows = grasp_candidates(task, d)
            self.assertTrue(rows)
            self.assertEqual(rows[0]["source"], "procedural_bbox_pinch_v1")
            self.assertEqual(len(rows[0]["empty_asset_grasp_sources"]), 1)
