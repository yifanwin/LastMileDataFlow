"""五个命令分发、构建编排和独立安装入口。"""

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lastmile_dataflow.cli import main, parser
from lastmile_dataflow.io import write_json
from tests.helpers import ROOT


class CliTests(unittest.TestCase):
    def test_core_commands_only(self):
        commands = next(action for action in parser()._actions if action.dest == "command")
        self.assertEqual(
            set(commands.choices),
            {"build-scenes", "evaluate-stations", "collect-lastmile", "build-bench", "evaluate-bench"},
        )

    def test_build_scenes_dispatch(self):
        with patch(
            "lastmile_dataflow.workflows.scene_datasets.build_scenes", return_value=Path("out")
        ) as build:
            main(["build-scenes", "--config", "recipe.json", "--run-id", "one", "--gpu", "6"])
        build.assert_called_once_with(
            Path("recipe.json"), Path("outputs"), "one", gpu_ids=[6], allow_partial_targets=False
        )

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
                    side_effect=lambda config, path: path,
                ) as discover,
                patch(
                    "lastmile_dataflow.workflows.scene_datasets.qualify_scene_discovery",
                    side_effect=lambda path, output: output,
                ) as qualify,
                patch(
                    "lastmile_dataflow.workflows.scene_datasets.build_scene_dataset",
                    return_value=root / "result",
                ) as build,
            ):
                result = build_scenes("recipe.json", root, "one", gpu_ids=[6])
            discover.assert_called_once()
            qualify.assert_called_once()
            build.assert_called_once()
            self.assertEqual(qualify.call_args.args[0], discover.call_args.args[1])
            self.assertEqual(build.call_args.args[1], qualify.call_args.args[1])
            self.assertEqual(result, root / "result")
            self.assertEqual(list(root.iterdir()), [])


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
