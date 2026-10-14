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
            with patch('lastmile_dataflow.construction.obstacle_config.load_obstacle_config', return_value='config'), \
                 patch('lastmile_dataflow.workflows.obstacle_augmentation.augment_obstacles', return_value=output) as augment:
                self.assertEqual(main(['augment-obstacles', '--config', 'fixture.json',
                                       '--output-dir', directory, '--run-id', 'fixture']), 0)
            augment.assert_called_once_with('config', output, run_id='fixture')


if __name__ == '__main__':
    unittest.main()
