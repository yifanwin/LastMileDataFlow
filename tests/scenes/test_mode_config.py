"""Task-mode configuration and source split survive saved subset runs."""
from dataclasses import asdict, replace
import unittest

from lastmile_dataflow.scenes.dataset import SceneRecipe


class TaskModeConfigTests(unittest.TestCase):
    def recipe(self, **values):
        return SceneRecipe(dataset_dir='/data/scenes', assets_dir='/data/assets', asset_index='/data/assets.jsonl',
            robot_config='configs/robots/panda_omron_robocasa.json',
            houses=list(range(10)), **values)

    def test_source_split_is_seeded_once_and_inherited_by_subsets(self):
        full = self.recipe()
        self.assertEqual(list(full.house_splits.values()).count('train'), 8)
        subset = replace(full, houses=[1, 4, 7])
        saved_subset = SceneRecipe(**asdict(subset))
        self.assertEqual(full.house_splits, saved_subset.house_splits)
        reverse = replace(full, houses=list(reversed(full.houses)), house_splits={})
        self.assertEqual(reverse.house_splits, full.house_splits)

    def test_explicit_source_split_takes_precedence(self):
        split = {str(h): 'val' if h % 2 else 'train' for h in range(10)}
        self.assertEqual(self.recipe(house_splits=split).house_splits, split)

    def test_new_recipe_does_not_accept_case_rules_or_robot_derived_sampling(self):
        for values in ({'cases': ['Case1']}, {'station_config': {'radius_m': 2.}},
                       {'max_final_stations': 21}, {'local_task_models': True}):
            with self.subTest(values=values), self.assertRaises((TypeError, ValueError)):
                self.recipe(**values)
