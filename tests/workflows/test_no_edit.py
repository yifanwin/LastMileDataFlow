"""No-edit recipe, natural dispatch and additive trial fields."""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lastmile_dataflow.cli import parser
from lastmile_dataflow.runtime.resources import RunResources
from lastmile_dataflow.scenes.build.builder import build_task
from lastmile_dataflow.scenes.dataset_config import TaskModeRecipe
from lastmile_dataflow.workflows.collection import _trial_record
from lastmile_dataflow.workflows.scene_datasets import _task_preparation_rejections


class NoEditRecipeTests(unittest.TestCase):
    def recipe(self, **overrides):
        values = dict(dataset_dir='.', assets_dir='.', asset_index='unused',
                      robot_config='unused', mode_config='unused', houses=[0, 1],
                      modes=[], train_fraction=0.0, exclude_categories=[])
        values.update(overrides)
        return TaskModeRecipe(**values)

    def test_empty_modes_and_all_val_split(self):
        recipe = self.recipe()
        self.assertEqual(recipe.modes, [])
        self.assertEqual(recipe.house_splits, {'0': 'val', '1': 'val'})

    def test_invalid_excluded_categories_rejected(self):
        with self.assertRaisesRegex(ValueError, 'excluded target categories'):
            self.recipe(exclude_categories='Pillow')

    def test_invalid_included_categories_rejected(self):
        with self.assertRaisesRegex(ValueError, 'included target categories'):
            self.recipe(include_categories='Pillow')

    def test_invalid_included_assets_rejected(self):
        with self.assertRaisesRegex(ValueError, 'included target assets'):
            self.recipe(include_assets={'Vase': 'Vase_Flat'})

    def test_invalid_excluded_tasks_rejected(self):
        with self.assertRaisesRegex(ValueError, 'excluded house:task'):
            self.recipe(exclude_tasks=['pick-x'])

    def test_permanent_categories_and_low_success_rate(self):
        from scripts.run_no_edit_pipeline import exclusion_from_categories
        state = {'manual_exclude_categories': [], 'permanent_categories': ['Cup'],
                 'category_stats': {'Cup|pick': {'category': 'Cup', 'trials': 1000, 'successes': 0},
                                    'Mug|pick': {'category': 'Mug', 'trials': 1000, 'successes': 0},
                                    'Bowl|pick': {'category': 'Bowl', 'trials': 2000, 'successes': 1},
                                    'Plate|pick': {'category': 'Plate', 'trials': 2000, 'successes': 2}}}
        self.assertEqual(exclusion_from_categories(state, 1000, 0.001), ['Bowl', 'Mug'])

    def test_preparation_rejection_uses_no_edit_intent(self):
        record = list(_task_preparation_rejections({'task_id': 'pick-x'}, self.recipe(), 0, 'boom'))
        self.assertEqual(record, [{'house': 0, 'task_id': 'pick-x', 'proposal_intent': 'no_edit',
                                    'status': 'rejected', 'reason': 'task_preparation_failed',
                                    'detail': 'boom'}])

    def test_empty_modes_dispatch_to_natural_task(self):
        ctx = SimpleNamespace(recipe=SimpleNamespace(modes=[]),
                              baseline=SimpleNamespace(source=SimpleNamespace(split='val')),
                              task={'task_id': 'pick-x'})
        with patch('lastmile_dataflow.scenes.build.builder.build_natural_task',
                   return_value={'status': 'accepted'}) as natural:
            records = list(build_task(ctx, {}, Path('.'), house=0, seed=1))
        self.assertEqual(records, [{'status': 'accepted'}])
        natural.assert_called_once()

    def test_recipe_cli_is_mutually_exclusive_with_dataset(self):
        args = parser().parse_args(['evaluate-stations', '--recipe', 'configs/x.json',
                                    '--assets-dir', '/tmp', '--output-dir', '/tmp',
                                    '--run-id', 'r', '--gpu', '0'])
        self.assertEqual(str(args.recipe), 'configs/x.json')
        with self.assertRaises(SystemExit):
            parser().parse_args(['evaluate-stations', '--recipe', 'a', '--dataset', 'b',
                                 '--assets-dir', '/tmp', '--output-dir', '/tmp',
                                 '--run-id', 'r', '--gpu', '0'])

    def test_trial_record_keeps_execution_attribution(self):
        result = {'status': 'failure', 'reason': 'lift_contact_hold_or_slip',
                  'attribution': {'category': 'GraspSlip', 'reason': 'lift_contact_hold_or_slip'},
                  'retry_history': [{'retry': 0, 'reason': 'x'}]}
        record = _trial_record(result, {'scene_id': 's', 'task': {'task_id': 't'}},
                               {'station_id': 'S0'}, 1, 43)
        self.assertEqual(record['attribution']['category'], 'GraspSlip')
        self.assertEqual(record['retry_history'][0]['reason'], 'x')

    def test_cpu_limits_follow_machine_capacity(self):
        cores = os.cpu_count() or 1
        if cores > 60:
            self.assertEqual(RunResources(gpu_ids=(0,), cpu_workers=61).cpu_workers, 61)
        if cores > 12:
            self.assertEqual(RunResources(gpu_ids=(0,), preparation_workers=13).preparation_workers, 13)


if __name__ == '__main__':
    unittest.main()
