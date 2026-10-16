"""场景构建编排回归；简化模型和替代站位不证明真实可见性或任务成功。"""

import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lastmile_dataflow.utils.io import read_json, rows, write_json
from lastmile_dataflow.scenes.dataset_config import load_scene_recipe
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
        self.assertEqual(
            [row["task"]["task_id"] for row in selected], ["first", "other-room", "other-support", "far"]
        )
        self.assertEqual(selected, workflow.spatial_select(list(reversed(pool)), 4))
        preferred = workflow.spatial_select(pool, 1, preferred=["far"])
        self.assertEqual(preferred, [far])

    def test_unlimited_selection_does_not_drop_nearby_targets(self):
        pool = [candidate("a", 0.0), candidate("b", 0.1), candidate("c", 0.2)]
        self.assertEqual(len(workflow.spatial_select(pool, 3, min_separation_m=1.0)), 1)
        self.assertEqual(len(workflow.spatial_select(pool, -1, min_separation_m=1.0)), 3)


class SceneDatasetTests(DatasetTestCase):
    def setUp(self):
        super().setUp()
        self.recipe_path = self.root / "recipe.json"
        self.robot_path = self.root / "robot.json"
        write_json(self.robot_path, {"data_root": str(self.root), **asdict(self.robot)})
        self.baseline = self.root / "baseline"
        self.sim.freeze(self.baseline)

    def recipe(self, **overrides):
        value = {
            "data_root": str(self.root),
            "dataset_dir": str(self.root),
            "asset_library": str(self.root),
            "robot_config": str(self.robot_path),
            "houses": [1],
            "cases": ["Case2"],
            "rule_version": "scene-cases-v3",
            "pick_target_types": ["cup"],
            "max_pick_targets_per_house": 1,
            "min_prefilter_points": 1,
            "min_final_points": 1,
            "variants_per_case": 1,
            **overrides,
        }
        write_json(self.recipe_path, value)
        return load_scene_recipe(self.recipe_path)

    def plan(self, recipe, selected, exclusions=()):
        for row in selected:
            row["input_snapshot"] = str(self.baseline)
        path = self.root / "discovery.json"
        write_json(
            path,
            {
                "recipe": asdict(recipe),
                "selected": selected,
                "exclusions": list(exclusions),
                "houses": [{"house": 1}],
                "elapsed_s": 0.0,
            },
        )
        return path

    def test_discovery_keeps_open_independent_of_pick_selectors(self):
        self.recipe(cases=["Case2", "Case3"], targets={"1": ["pick"]})
        pick, ignored, opening = [
            candidate(name, x) for name, x in [("pick", 0.0), ("ignored", 1.0), ("open", 2.0)]
        ]
        opening["task"]["operation"] = "open"
        source = self.sim.source
        support = SimpleNamespace(support="table", bounds=[0.0, 1.0, 0.0, 1.0])
        output = self.root / "found.json"
        with (
            patch.object(workflow.SceneSource, "procthor", return_value=source),
            patch.object(workflow, "read_json", return_value={}),
            patch.object(
                workflow,
                "discover_tasks",
                return_value=([pick["task"], ignored["task"], opening["task"]], []),
            ),
            patch.object(workflow, "task_support_obstacles", return_value=[]),
            patch.object(workflow, "support_regions", return_value=[support]),
            patch.object(workflow, "sample_stations", return_value=self.points[:1]) as sample,
        ):
            workflow.discover_scene_dataset(self.recipe_path, output)
        plan = read_json(output)
        self.assertEqual([row["task"]["task_id"] for row in plan["selected"]], ["pick", "open"])
        self.assertEqual(sample.call_count, 2)
        self.assertTrue(
            all(call.kwargs == {"static_only": True, "stop_after_valid": 1} for call in sample.call_args_list)
        )
        self.assertEqual(plan["houses"][0]["selected_pick"], 1)
        self.assertEqual(plan["houses"][0]["selected_open"], 1)
        self.assertGreaterEqual(plan["elapsed_s"], 0.0)
        self.assertNotIn("started_at", plan)
        self.assertNotIn("finished_at", plan)

    def test_discovery_closes_simulation_when_baseline_save_fails(self):
        self.recipe()
        sim = Mock()
        sim.freeze.side_effect = OSError("fixture write failed")
        with (
            patch.object(workflow.SceneSource, "procthor", return_value=self.sim.source),
            patch.object(workflow.Simulation, "from_source", return_value=sim),
            self.assertRaisesRegex(OSError, "fixture write failed"),
        ):
            workflow.discover_scene_dataset(self.recipe_path, self.root / "failed.json")
        sim.close.assert_called_once()

    def test_qualification_reselects_static_candidates_without_repeating_checks(self):
        recipe = self.recipe(max_pick_targets_per_house=2)
        a, b, c = candidate("a", 0.0, area=3.0), candidate("b", 1.0, area=2.0), candidate("c", 2.0)
        plan = self.plan(
            recipe,
            [a, c],
            [{"house": 1, "task_id": "b", "reason": "eligible_not_selected_by_spatial_coverage"}],
        )
        checked = []

        def sample(sim, task, geometry):
            checked.append(task["task_id"])
            return [] if task["task_id"] == "c" else self.points[:1]

        support = SimpleNamespace(support="table", bounds=[0.0, 1.0, 0.0, 1.0])
        with (
            patch.object(workflow.SceneSource, "procthor", return_value=self.sim.source),
            patch.object(workflow, "read_json", return_value={}),
            patch.object(workflow, "discover_tasks", return_value=([r["task"] for r in (a, b, c)], [])),
            patch.object(workflow, "task_support_obstacles", return_value=[]),
            patch.object(workflow, "support_regions", return_value=[support]),
            patch.object(workflow, "sample_stations", side_effect=sample),
        ):
            result = workflow.qualify_scene_discovery(plan, self.root / "qualified.json")
        qualified = read_json(result)
        self.assertEqual({row["task"]["task_id"] for row in qualified["selected"]}, {"a", "b"})
        self.assertCountEqual(checked, ["a", "b", "c"])
        self.assertEqual(len(qualified["qualification"]), 3)
        self.assertEqual(qualified["houses"][0]["pick_pairwise_distances_m"], [1.0])
        self.assertTrue(any(row["task_id"] == "c" for row in qualified["exclusions"]))
        self.assertNotIn("finished_at", qualified)

    def test_build_requires_quota_before_creating_output(self):
        recipe = self.recipe()
        plan = self.plan(recipe, [])
        with self.assertRaisesRegex(ValueError, "not enough pick targets"):
            workflow.build_scene_dataset(self.recipe_path, plan, self.root, "strict", [0])
        self.assertFalse((self.root / "scene_datasets/strict").exists())
        root = workflow.build_scene_dataset(
            self.recipe_path, plan, self.root, "partial", [0], allow_partial_targets=True
        )
        self.assertEqual(
            read_json(root / "summary.json"), {"status": "completed", "accepted": 0, "rejected": 0}
        )

    def test_pick_cases_write_into_parent_dataset_with_correct_counts(self):
        recipe = self.recipe(cases=["Case1", "Case2"])
        plan = self.plan(recipe, [candidate("pick", 0.0)])
        with patch.object(workflow, "augment_obstacles") as augment:
            root = workflow.build_scene_dataset(self.recipe_path, plan, self.root, "pick", [0])
        configs = [call.args[0] for call in augment.call_args_list]
        self.assertEqual([config.obstacle_count for config in configs], [[6, 9], [10, 20]])
        self.assertEqual([config.mandatory_categories for config in configs], [["Chair"], []])
        self.assertTrue(all(call.kwargs["dataset_root"] == root for call in augment.call_args_list))
        self.assertEqual([config.seed for config in configs], [recipe.seed, recipe.seed])

    def test_open_saves_snapshot_only_when_final_stations_are_sufficient(self):
        recipe = self.recipe(cases=["Case3"])
        opening = candidate("open", 0.0)
        opening["task"]["operation"] = "open"
        plan = self.plan(recipe, [opening])
        for name, points, status in [("accepted", self.points[:1], "accepted"), ("rejected", [], "rejected")]:
            with self.subTest(status=status), patch.object(workflow, "sample_stations", return_value=points):
                root = workflow.build_scene_dataset(self.recipe_path, plan, self.root, name, [0])
                record = rows(root / "scenes.jsonl")[0]
                self.assertEqual(record["status"], status)
                if status == "accepted":
                    self.assertTrue((root / record["scene"] / "model.mjb").is_file())
                    self.assertEqual(record["placements"], [])
                else:
                    self.assertEqual(record["reason"], "too_few_stations")

    def test_probe_closes_clone_when_sampling_raises(self):
        recipe = self.recipe()
        probe = workflow.clone_preparation(self.sim)
        self.addCleanup(probe.close)
        support = SimpleNamespace(support="table", bounds=[0.0, 1.0, 0.0, 1.0])
        with (
            patch.object(workflow, "clone_preparation", return_value=probe),
            patch.object(workflow, "task_support_obstacles", return_value=[]),
            patch.object(workflow, "support_regions", return_value=[support]),
            patch.object(workflow, "sample_stations", side_effect=RuntimeError("sampling failed")),
            self.assertRaisesRegex(RuntimeError, "sampling failed"),
        ):
            workflow._probe_target(self.sim, self.task, recipe, recipe.geometry)
        self.assertTrue(probe.closed)
        self.assertFalse(self.sim.closed)

    def test_case1_requires_near_ground_points_even_with_visible_stations(self):
        recipe = self.recipe(cases=["Case1"])
        row = candidate("pick", 0.0)
        existing = {"pick": {"stations": self.points[:1]}}
        with (
            patch.object(workflow, "sample_stations") as sample,
            patch.object(workflow, "station_regions", return_value={"points": []}),
            patch.object(workflow, "approach_points"),
        ):
            evidence = workflow._qualify_target(self.sim, row, recipe, recipe.geometry, existing)
        sample.assert_not_called()
        self.assertEqual(evidence["valid_count"], 1)
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["reason"], "no_measured_near_ground_points_for_mandatory_chair")
        self.assertFalse(self.sim.closed)
