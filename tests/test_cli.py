"""核心命令与 workflow 分发；不执行真实采集。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lastmile_dataflow.cli import main, parser
from lastmile_dataflow.io import write_json


class CliTests(unittest.TestCase):
    def test_core_commands_only(self):
        subcommands = next(action for action in parser()._actions if action.dest == 'command')
        self.assertEqual(set(subcommands.choices), {
            'collect-lastmile-datasets', 'build-lastmilebench', 'evaluate-lastmilebench',
            'discover-scene-datasets', 'qualify-scene-datasets', 'build-scene-datasets',
            'augment-obstacles', 'export-molmobot',
            'collect-no-edit',
        })

    def test_obstacle_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_json(output / 'summary.json', {'status': 'completed'})
            with patch('lastmile_dataflow.scenes.obstacle_config.load_obstacle_config', return_value='config'), \
                 patch('lastmile_dataflow.workflows.obstacle_augmentation.augment_obstacles', return_value=output) as augment:
                self.assertEqual(main(['augment-obstacles', '--config', 'fixture.json',
                                       '--output-dir', directory, '--run-id', 'fixture']), 0)
            augment.assert_called_once_with('config', output, run_id='fixture')

    def test_collection_commands_use_the_two_public_functions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'execution.json'
            write_json(config, {})
            for command, flags, target in [
                ('collect-no-edit', ['--dataset-dir', directory], 'evaluate_stations'),
                ('collect-lastmile-datasets', ['--dataset', directory], 'collect_lastmile'),
                ('collect-lastmile-datasets', ['--dataset', directory, '--calibration-only'], 'evaluate_stations'),
            ]:
                with self.subTest(command=command, flags=flags), \
                     patch('lastmile_dataflow.workflows.collection.evaluate_stations', return_value=Path(directory)) as stations, \
                     patch('lastmile_dataflow.workflows.collection.collect_lastmile', return_value=Path(directory)) as lastmile:
                    main([command, *flags, '--assets-dir', directory, '--config', str(config),
                          '--run-id', 'fixture', '--gpu-ids', '0'])
                    selected, other = (stations, lastmile) if target == 'evaluate_stations' else (lastmile, stations)
                    selected.assert_called_once()
                    other.assert_not_called()


if __name__ == '__main__':
    unittest.main()
