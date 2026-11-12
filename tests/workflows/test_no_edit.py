"""No-edit recipe, natural dispatch and additive trial fields."""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lastmile_dataflow.station_map.config import StationRun
from lastmile_dataflow.workflows.station_map import trial_record
from lastmile_dataflow.scenes.edits.search import build_task
from lastmile_dataflow.scenes.dataset import SceneRecipe
from lastmile_dataflow.workflows import scene_datasets


class NoEditRecipeTests(unittest.TestCase):
    def recipe(self, **overrides):
        values = dict(
            dataset_dir=".",
            assets_dir=".",
            asset_index="unused",
            robot_config="unused",
            houses=[0, 1],
            edits={"enabled": False},
            train_fraction=0.0,
            exclude_categories=[],
        )
        values.update(overrides)
        return SceneRecipe(**values)

    def test_empty_modes_and_all_val_split(self):
        recipe = self.recipe()
        self.assertFalse(recipe.edits.enabled)
        self.assertEqual(recipe.house_splits, {"0": "val", "1": "val"})

    def test_invalid_excluded_categories_rejected(self):
        with self.assertRaisesRegex(ValueError, "excluded target categories"):
            self.recipe(exclude_categories="Pillow")

    def test_invalid_included_categories_rejected(self):
        with self.assertRaisesRegex(ValueError, "included target categories"):
            self.recipe(include_categories="Pillow")

    def test_invalid_included_assets_rejected(self):
        with self.assertRaisesRegex(ValueError, "included target assets"):
            self.recipe(include_assets={"Vase": "Vase_Flat"})

    def test_invalid_excluded_tasks_rejected(self):
        with self.assertRaisesRegex(ValueError, "excluded house:task"):
            self.recipe(exclude_tasks=["pick-x"])

    def test_preparation_rejection_uses_no_edit_intent(self):
        with patch.object(scene_datasets.BuildContext, "prepare", side_effect=ValueError("boom")):
            record, _ = scene_datasets._build_task(None, {"task_id": "pick-x"}, 0, self.recipe(), 0, Path("."), {})
        self.assertEqual(
            record,
            [
                {
                    "house": 0,
                    "task_id": "pick-x",
                    "proposal_intent": "no_edit",
                    "status": "rejected",
                    "reason": "task_preparation_failed",
                    "detail": "boom",
                }
            ],
        )

    def test_empty_modes_dispatch_to_natural_task(self):
        ctx = SimpleNamespace(
            settings=SimpleNamespace(enabled=False),
            baseline=SimpleNamespace(source=SimpleNamespace(split="val")),
            task={"task_id": "pick-x"},
        )
        with patch(
            "lastmile_dataflow.scenes.edits.search.build_natural_task", return_value={"status": "accepted"}
        ) as natural:
            records = list(build_task(ctx, Path("."), house=0, seed=1))
        self.assertEqual(records, [{"status": "accepted"}])
        natural.assert_called_once()

    def test_trial_record_keeps_execution_attribution(self):
        result = {
            "status": "failure",
            "reason": "lift_contact_hold_or_slip",
            "attribution": {"category": "GraspSlip", "reason": "lift_contact_hold_or_slip"},
            "retry_history": [{"retry": 0, "reason": "x"}],
        }
        record = trial_record(result, {"scene_id": "s", "task": {"task_id": "t"}}, {"station_id": "S0"}, 1, 43)
        self.assertEqual(record["attribution"]["category"], "GraspSlip")
        self.assertEqual(record["retry_history"][0]["reason"], "x")

    def test_cpu_limits_follow_machine_capacity(self):
        cores = os.cpu_count() or 1
        if cores > 60:
            self.assertEqual(StationRun("x", cpu_workers=61).cpu_workers, 61)
        if cores > 12:
            self.assertEqual(StationRun("x", preparation_workers=13).preparation_workers, 13)


if __name__ == "__main__":
    unittest.main()
