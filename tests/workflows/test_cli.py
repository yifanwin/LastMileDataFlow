"""五个命令分发、构建编排和独立安装入口。"""

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lastmile_dataflow.cli import main, parser
from lastmile_dataflow.config import LocalConfig
from lastmile_dataflow.utils.io import write_json
from tests.helpers import ROOT


class CliTests(unittest.TestCase):
    def setUp(self):
        # Tests must not depend on this machine's ignored local.json.
        local = patch('lastmile_dataflow.cli.load_local_config', return_value=LocalConfig())
        self.local = local.start()
        self.addCleanup(local.stop)

    def test_local_defaults_and_cli_overrides(self):
        self.local.return_value = LocalConfig(Path('/shared/assets'), Path('/shared/output'))
        for command in ('evaluate-stations', 'collect-lastmile'):
            with self.subTest(command=command):
                argv = [command, '--dataset', 'dataset', '--run-id', 'one', '--gpu', '6']
                args = parser().parse_args(argv)
                self.assertEqual(args.assets_dir, Path('/shared/assets'))
                self.assertEqual(args.output_dir, Path('/shared/output'))
                args = parser().parse_args(argv + ['--assets-dir', 'assets', '--output-dir', 'out'])
                self.assertEqual(args.assets_dir, Path('assets'))
                self.assertEqual(args.output_dir, Path('out'))
        args = parser().parse_args(['build-scenes', '--config', 'recipe.json', '--gpu', '6'])
        self.assertEqual(args.output_dir, Path('/shared/output'))
        args = parser().parse_args(['evaluate-bench', '--benchmark', 'bench', '--output', 'result',
                                    '--policy-config', 'policy.json', '--gpu', '6'])
        self.assertEqual(args.log_dir, Path('/shared/output/logs'))
        self.assertEqual(args.output, Path('result'))

    def test_assets_and_gpu_still_required_without_defaults(self):
        argv = ['evaluate-stations', '--dataset', 'dataset', '--run-id', 'one']
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            main(argv + ['--gpu', '6'])
        self.local.return_value = LocalConfig(assets_dir=Path('/shared/assets'))
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parser().parse_args(argv)

    def test_core_commands_only(self):
        commands = next(action for action in parser()._actions if action.dest == "command")
        self.assertEqual(
            set(commands.choices),
            {"build-scenes", "evaluate-stations", "collect-lastmile", "build-bench", "evaluate-bench"},
        )

    def test_measured_replay_dispatch_needs_no_planner_or_assets(self):
        with (patch('lastmile_dataflow.data.rendering.replay.render_run', return_value=Path('video')) as replay,
              patch('lastmile_dataflow.workflows.collection.evaluate_stations') as evaluate,
              patch('builtins.print')):
            main(['evaluate-stations', '--replay-from', 'run', '--rollouts', 'r0', 'r1',
                  '--replay-camera', 'overview', '--output-dir', 'out', '--run-id', 'video', '--gpu', '0'])
        replay.assert_called_once_with(Path('run'), Path('out/replays/video'),
                                       rollouts=['r0', 'r1'], camera='overview')
        evaluate.assert_not_called()

    def test_materialization_dispatch_needs_neither_gpu_nor_assets(self):
        with (
            patch('lastmile_dataflow.data.export.lastmile_dataset.materialize_lastmile', return_value=Path('out/one')) as materialize,
            patch('lastmile_dataflow.workflows.collection.collect_lastmile') as collect,
            patch('builtins.print'),
        ):
            main(['collect-lastmile', '--materialize-from', 'reference', '--output-dir', 'out', '--run-id', 'one'])
        materialize.assert_called_once_with(Path('reference'), Path('out/one'))
        collect.assert_not_called()

    def test_build_scenes_dispatch(self):
        with patch(
            "lastmile_dataflow.workflows.scene_datasets.build_scenes", return_value=Path("out")
        ) as build, self.assertLogs("lastmile_dataflow", level="INFO") as logs, patch("builtins.print") as output:
            main(["build-scenes", "--config", "recipe.json", "--run-id", "one", "--gpu", "6"])
        self.assertIn("开始命令=build-scenes GPU=6", logs.output[0])
        self.assertIn("命令结束=build-scenes 输出=out", logs.output[-1])
        output.assert_called_once_with(Path("out"))
        build.assert_called_once_with(
            Path("recipe.json"), Path("outputs"), "one", gpu_ids=[6], allow_partial_targets=False, workers=1
        )

    def test_preview_dataset_dispatch_without_new_run(self):
        with patch(
            "lastmile_dataflow.data.visualization.scene_preview.export_scene_previews",
            return_value=Path("dataset/index.html"),
        ) as preview, patch("builtins.print"):
            main(["build-scenes", "--preview-dataset", "dataset", "--gpu", "0"])
        preview.assert_called_once_with(Path("dataset"))

    def test_collection_commands_use_the_two_public_functions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "execution.json"
            write_json(config, {})
            for command, target in [
                ("evaluate-stations", "evaluate_stations"),
                ("collect-lastmile", "collect_lastmile"),
            ]:
                with (
                    self.subTest(command=command),
                    patch(
                        "lastmile_dataflow.workflows.collection.evaluate_stations",
                        return_value=Path(directory),
                    ) as stations,
                    patch(
                        "lastmile_dataflow.workflows.collection.collect_lastmile",
                        return_value=Path(directory),
                    ) as lastmile,
                ):
                    main(
                        [
                            command,
                            "--dataset",
                            directory,
                            "--assets-dir",
                            directory,
                            "--config",
                            str(config),
                            "--run-id",
                            "fixture",
                            "--gpu",
                            "6",
                        ]
                    )
                    selected, other = (
                        (stations, lastmile) if target == "evaluate_stations" else (lastmile, stations)
                    )
                    selected.assert_called_once()
                    other.assert_not_called()

    def test_build_command_runs_the_three_existing_steps_in_order(self):
        from lastmile_dataflow.workflows.scene_datasets import build_scenes

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "lastmile_dataflow.workflows.scene_datasets.discover_scene_dataset",
                    side_effect=lambda config, path, **options: path,
                ) as discover,
                patch(
                    "lastmile_dataflow.workflows.scene_datasets.qualify_scene_discovery",
                    side_effect=lambda path, output: output,
                ) as qualify,
                patch(
                    "lastmile_dataflow.workflows.scene_datasets.build_scene_dataset",
                    return_value=root / "result",
                ) as build,
                patch('lastmile_dataflow.workflows.scene_datasets.read_json', return_value={}),
                patch('lastmile_dataflow.workflows.scene_datasets.write_json') as save_summary,
            ):
                result = build_scenes("recipe.json", root, "one", gpu_ids=[6])
            discover.assert_called_once()
            qualify.assert_called_once()
            build.assert_called_once()
            self.assertTrue(discover.call_args.args[1].parent.name.startswith('scene-build-'))
            self.assertEqual(qualify.call_args.args[0], discover.call_args.args[1])
            self.assertEqual(build.call_args.args[1], qualify.call_args.args[1])
            self.assertEqual(result, root / "result")
            self.assertEqual(list(root.iterdir()), [])
            self.assertIn('wall_elapsed_s', save_summary.call_args.args[1])


class IndependenceTests(unittest.TestCase):
    def test_no_old_package_imports_or_environment_script(self):
        root = ROOT
        banned = {"lastmile", "molmo_spaces", "molmospaces"}
        for path in (root / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned, msg=str(path))
        import tomllib

        project = tomllib.loads((root / "pyproject.toml").read_text())
        self.assertEqual(project["project"]["scripts"]["lastmile-dataflow"], "lastmile_dataflow.cli:main")
        self.assertFalse((root / "bin/lastmile-dataflow").exists())

    def test_installed_module_starts_outside_repository_without_pythonpath(self):
        import os
        import subprocess
        import sys
        import tempfile

        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "lastmile_dataflow.cli", "--help"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertIn("evaluate-stations", result.stdout)
        self.assertNotIn("collect-no-edit", result.stdout)
