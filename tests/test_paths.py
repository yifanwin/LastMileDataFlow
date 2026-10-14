"""Explicit data-root and repository-root configuration rules."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lastmile_dataflow.config import read_config
from lastmile_dataflow.io import create_run, read_json, write_json
from lastmile_dataflow.policies.molmobot_config import load_policy_config
from lastmile_dataflow.scenes.dataset_config import load_scene_robot


class PathTests(unittest.TestCase):
    def test_data_paths_use_explicit_data_root_not_config_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'configs/nested/robot.json'
            write_json(path, {'data_root': str(root / 'assets'), 'model_path': 'robot.xml'})
            robot = load_scene_robot(path)
            self.assertEqual(robot.model_path, str(root / 'assets/robot.xml'))

    def test_missing_data_root_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'robot.json'
            write_json(path, {'model_path': 'robot.xml'})
            with self.assertRaisesRegex(ValueError, 'data_root'):
                load_scene_robot(path)

    def test_repo_paths_preserve_virtual_environment_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'base-python'
            base.touch()
            interpreter = root / '.venv/bin/python'
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(base)
            path = root / 'configs/nested/policy.json'
            write_json(path, {'data_root': 'data', 'checkpoint': 'models/multitask',
                              'python': '.venv/bin/python'})
            with patch('lastmile_dataflow.config.REPO_ROOT', root):
                config = load_policy_config(path)
            self.assertEqual(config.python, str(interpreter))
            self.assertEqual(config.checkpoint, str(root / 'data/models/multitask'))

    def test_saved_run_paths_are_not_rewritten_by_field_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = {'dataset': str(root / 'data'), 'source': {'dataset': 'custom'},
                     'python': '.venv/bin/python'}
            run = create_run(root / 'run', value)
            self.assertEqual(read_json(run / 'config.json'), value)
            self.assertEqual(read_config(run / 'config.json'), value)


if __name__ == '__main__':
    unittest.main()
