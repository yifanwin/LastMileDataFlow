"""场景构建编排回归；简化模型和替代站位不证明真实可见性或任务成功。"""

import unittest
from unittest.mock import Mock, patch

from lastmile_dataflow.utils.io import read_json, rows
from lastmile_dataflow.scenes.dataset import SceneRecipe
from lastmile_dataflow.workflows import scene_datasets as workflow
from tests.helpers import DatasetTestCase


def candidate(identity, x, *, area=1.0, room=1, support="table"):
    return {
        "house": 1,
        "support_id": support,
        "support_area_m2": area,
        "prefilter_count": 1,
        "task": {
            "task_id": identity,
            "instance_id": identity,
            "asset_id": identity,
            "category": "Cup",
            "operation": "pick",
            "target_body": "target",
            "room_id": room,
            "anchor_world": [x, 0.0, 0.15],
        },
    }


class SpatialSelectionTests(unittest.TestCase):
    def test_spatial_selection_prioritizes_diversity_then_distance(self):
        first = candidate("first", 0.0, area=10.0)
        far = candidate("far", 8.0)
        other_room = candidate("other-room", 1.0, room=2)
        other_support = candidate("other-support", 2.0, support="counter")
        pool = [far, other_support, first, other_room]
        selected = workflow.spatial_select(pool, 4)
        self.assertEqual([row["task"]["task_id"] for row in selected], ["first", "other-room", "other-support", "far"])
        self.assertEqual(selected, workflow.spatial_select(list(reversed(pool)), 4))

    def test_unlimited_selection_does_not_drop_nearby_targets(self):
        pool = [candidate("a", 0.0), candidate("b", 0.1), candidate("c", 0.2)]
        self.assertEqual(len(workflow.spatial_select(pool, -1)), 3)


class SceneDatasetTests(DatasetTestCase):
    def setUp(self):
        super().setUp()
        self.recipe = SceneRecipe(
            dataset_dir=str(self.root),
            assets_dir=str(self.root),
            asset_index="unused",
            robot_config="unused",
            houses=[0, 1],
        )

    def build(self, name, worker):
        with (
            patch.object(workflow, "load_scene_recipe", return_value=self.recipe),
            patch.object(workflow, "load_robot_config", return_value=self.robot),
            patch.object(workflow, "_build_house", side_effect=worker),
        ):
            return workflow.build_scenes("unused", self.root / name, gpu_ids=[6])

    def test_parent_writes_one_main_file_and_preserves_negative_results(self):
        def worker(job):
            house = job[1]
            return {
                "source": {"house": house},
                "records": [
                    {
                        "house": house,
                        "proposal_intent": "reachability",
                        "status": "rejected",
                        "reason": "no_legal_candidate",
                    }
                ],
            }

        root = self.build("negative", worker)
        self.assertEqual(len(rows(root / "scenes.jsonl")), 2)
        summary = read_json(root / "summary.json")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["rejected"], 2)
        self.assertEqual(summary["intent_counts"], {"reachability": 2})
        config = read_json(root / "config.json")
        self.assertEqual(config["recipe"]["house_splits"], self.recipe.house_splits)

    def test_shared_failure_keeps_finished_rows_and_stops_next_house(self):
        def worker(job):
            self.assertEqual(job[1], 0)
            return {
                "source": {"house": 0, "error": {"type": "RuntimeError", "reason": "renderer failed"}},
                "records": [{"proposal_intent": "orientation", "status": "accepted", "task_completion": "unknown"}],
            }

        with self.assertRaisesRegex(RuntimeError, "renderer failed"):
            self.build("shared-error", worker)
        root = self.root / "shared-error"
        self.assertEqual(len(rows(root / "scenes.jsonl")), 1)
        summary = read_json(root / "summary.json")
        self.assertEqual(summary["status"], "infrastructure_error")
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(len(summary["sources"]), 1)

    def test_executor_failure_never_saves_completed_status(self):
        with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
            self.build("executor-error", RuntimeError("worker unavailable"))
        summary = read_json(self.root / "executor-error/summary.json")
        self.assertEqual(summary["status"], "infrastructure_error")
        self.assertEqual(summary["accepted"] + summary["rejected"], 0)

    def test_worker_reuses_source_and_prepares_once_per_task(self):
        task = candidate("one", 0.0)["task"]
        sim = Mock(source=self.sim.source)
        ctx = Mock()
        ctx.__enter__ = Mock(return_value=ctx)
        ctx.__exit__ = Mock(return_value=False)
        ctx.timings = {"prepare_s": 1.0}
        ctx.grasp_evidence = {"data_grasp_count": 1}
        ctx.assets.compile_count = 0
        with (
            patch.object(workflow.SceneSource, "procthor", return_value=self.sim.source),
            patch.object(workflow.Simulation, "from_source", return_value=sim) as load,
            patch.object(workflow, "_select_tasks", return_value=([task], {})),
            patch.object(workflow.BuildContext, "prepare", return_value=ctx) as prepare,
            patch.object(workflow, "build_task", return_value=iter([])) as build,
        ):
            result = workflow._build_house((self.recipe, 0, self.robot, self.root, 6))
        self.assertNotIn("error", result["source"])
        load.assert_called_once()
        prepare.assert_called_once_with(sim, task, self.recipe)
        self.assertEqual(build.call_args.args[1], self.root)
        sim.close.assert_called_once()
        ctx.__exit__.assert_called_once()

    def test_preparation_rejections_skip_articulated_pick_and_keep_real_reason(self):
        task = candidate("pick", 0.0)["task"]
        with patch.object(workflow.BuildContext, "prepare", side_effect=ValueError("missing support")):
            records, report = workflow._build_task(Mock(), task, 0, self.recipe, 0, self.root, {})
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["status"] == "rejected" for r in records))
        self.assertTrue(
            all(r["reason"] == "task_preparation_failed" and r["detail"] == "missing support" for r in records)
        )
        self.assertEqual(report["status"], "preparation_rejected")
