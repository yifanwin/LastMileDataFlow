"""Task-mode configuration and source split survive saved subset runs."""
from dataclasses import asdict, replace
import unittest

from lastmile_dataflow.scenes.dataset_config import TaskModeRecipe, load_task_mode_recipe
from lastmile_dataflow.scenes.mode_config import ModeConfig, MODE_NAMES, load_mode_config
from lastmile_dataflow.runtime.execution_config import load_execution_config, calibration_execution


class TaskModeConfigTests(unittest.TestCase):
    def recipe(self, **values):
        return TaskModeRecipe(dataset_dir='/data/scenes', assets_dir='/data/assets', asset_index='/data/assets.jsonl',
            robot_config='configs/robots/panda_omron_robocasa.json',
            mode_config='configs/task_modes/mode_defaults.json', houses=list(range(10)), **values)

    def test_source_split_is_seeded_once_and_inherited_by_subsets(self):
        full = self.recipe()
        self.assertEqual(list(full.house_splits.values()).count('train'), 8)
        subset = replace(full, houses=[1, 4, 7])
        saved_subset = TaskModeRecipe(**asdict(subset))
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

    def test_checked_configs_share_settings_and_three_trial_mobile_protocol(self):
        recipe = load_task_mode_recipe('configs/task_modes/panda_omron_task_modes.json')
        execution = load_execution_config('configs/task_modes/panda_omron_calibration.json')
        self.assertEqual(set(recipe.modes), set(MODE_NAMES))
        self.assertEqual(recipe.geometry.radius_m, 1.)
        self.assertEqual(recipe.geometry.spacing_m, .25)
        self.assertEqual(recipe.mode_config, execution.mode_config)
        self.assertEqual(execution.trials_per_station, 3)
        self.assertEqual(execution.operations, ['pick', 'open'])
        self.assertEqual(execution.operation_base_mode, 'holonomic_joint_planning')
        self.assertEqual(load_mode_config(recipe.mode_config), ModeConfig())
        self.assertEqual(calibration_execution({'radius_m': 2., 'mode_config': 'a'}), {'radius_m': 2.})
