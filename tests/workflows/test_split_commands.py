"""Both public workflows consume one run-wide calibration, without repeating trials."""
from dataclasses import replace
from unittest.mock import patch

from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.utils.io import append_row, read_json
from lastmile_dataflow.workflows.collection import collect_lastmile, evaluate_stations, _plan_collection
from tests.helpers import DatasetTestCase


class SplitCommandTests(DatasetTestCase):
    def test_standalone_and_builtin_calibration_share_resources_and_numeric_recording(self):
        dataset = self.dataset()
        second = {**self.scene, 'scene_id': 'two'}
        append_row(dataset / 'scenes.jsonl', second)
        config = ExecutionConfig(shared_station_preparation=True, trials_per_station=3)

        def calibrate(scenes, assets, execution, recording, root, resources, **options):
            self.assertEqual(len(scenes), 2)
            self.assertEqual(resources.gpu_ids, (1, 4))
            self.assertEqual(resources.cpu_workers, 5)
            self.assertFalse(recording.record_images or recording.record_video or recording.record_depth)
            records = []
            for scene in scenes:
                for station in scene['stations']:
                    for trial in range(3):
                        record = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id'],
                                  'station_id': station['station_id'], 'trial': trial,
                                  'status': 'planning_no_solution', 'reason': 'synthetic_fixture'}
                        records.append(record)
                        append_row(root / 'trials.jsonl', record)
            return records, {'failure': None, 'planned_trials': 12, 'recorded_trials': 12}

        for name, command in [('evaluate', evaluate_stations), ('collect', collect_lastmile)]:
            with (self.subTest(command=name),
                  patch('lastmile_dataflow.workflows.station_calibration.calibrate_stations', side_effect=calibrate) as run,
                  patch('lastmile_dataflow.workflows.collection._station_trials') as old,
                  patch('lastmile_dataflow.workflows.collection.export_heatmap')):
                output = command(dataset, self.root, config,
                    replace(self.collection, record_images=True), self.root, run_id=name,
                    gpu_ids=[1, 4], cpu_workers=5, preparation_workers=2)
            run.assert_called_once()
            old.assert_not_called()
            saved = read_json(output / 'config.json')
            self.assertFalse(saved['calibration_collection']['record_images'])
            self.assertTrue(saved['collection']['record_images'])
            summary = read_json(output / 'summary.json')
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(summary['calibration']['recorded_trials'], 12)
            self.assertEqual(len(summary['scenes']), 2)

    def test_numeric_success_can_select_rates_without_claiming_visual_demonstration(self):
        root = self.root / 'numeric'
        root.mkdir()
        record = {'scene_id': 'one', 'task_id': self.task['task_id'], 'station_id': 's0',
                  'trial': 0, 'seed': 42, 'status': 'success', 'task_outcome': {'status': 'success'},
                  'rollout_id': 'numeric', 'recording_complete': True,
                  'observation_complete': False, 'executed_steps': 3}
        _plan_collection([self.scene], [record], ExecutionConfig(trials_per_station=1), root, '.')
        from lastmile_dataflow.utils.io import rows
        self.assertEqual(rows(root / 'train_index.jsonl'), [])
