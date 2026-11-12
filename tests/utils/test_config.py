"""外部配置、显式数据根目录与运行路径。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lastmile_dataflow.utils.io import LocalConfig, load_local_config, read_config
from lastmile_dataflow.operation.config import CollectionConfig
from lastmile_dataflow.robots.rby1.config import RobotConfig
from lastmile_dataflow.utils.io import create_run, read_json, write_json
from lastmile_dataflow.policies.molmobot.client import load_molmobot_config as load_policy_config
from lastmile_dataflow.robots.registry import load_robot_config


class PathTests(unittest.TestCase):
    def test_local_config_is_optional_and_paths_use_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("lastmile_dataflow.utils.io.REPO_ROOT", root):
                self.assertEqual(load_local_config(), LocalConfig())
                write_json(root / "configs/local.yaml", {"assets_dir": "/shared/assets"})
                self.assertEqual(load_local_config(), LocalConfig(Path("/shared/assets")))
                write_json(root / "configs/local.yaml", {"assets_dir": "data/assets"})
                self.assertEqual(load_local_config().assets_dir, root / "data/assets")

    def test_data_paths_use_explicit_data_root_not_config_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "configs/nested/robot.yaml"
            write_json(path, {"data_root": str(root / "assets"), "model_path": "robot.xml"})
            robot = load_robot_config(path)
            self.assertEqual(robot.model_path, str(root / "assets/robot.xml"))

    def test_input_configs_are_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "robot.json"
            write_json(path, {"model_path": "robot.xml"})
            with self.assertRaisesRegex(ValueError, "YAML"):
                read_config(path)

    def test_missing_data_root_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "robot.yaml"
            write_json(path, {"model_path": "robot.xml"})
            with self.assertRaisesRegex(ValueError, "data_root"):
                load_robot_config(path)

    def test_repo_paths_preserve_virtual_environment_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base-python"
            base.touch()
            interpreter = root / ".venv/bin/python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(base)
            path = root / "configs/nested/policy.yaml"
            write_json(path, {"data_root": "data", "checkpoint": "models/multitask", "python": ".venv/bin/python"})
            with patch("lastmile_dataflow.utils.io.REPO_ROOT", root):
                config = load_policy_config(path)
            self.assertEqual(config.python, str(interpreter))
            self.assertEqual(config.checkpoint, str(root / "data/models/multitask"))

    def test_saved_run_paths_are_not_rewritten_by_field_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = {
                "dataset": str(root / "data"),
                "source": {"dataset": "custom"},
                "python": ".venv/bin/python",
            }
            run = create_run(root / "run", value)
            self.assertEqual(read_json(run / "config.json"), value)
            self.assertEqual(read_json(run / "config.json"), value)


class ConfigurationTests(unittest.TestCase):
    def test_panda_three_trial_configs_match_collection_and_rate_groups(self):
        from lastmile_dataflow.operation.config import load_execution_config
        from lastmile_dataflow.station_map.aggregate import eligible_stations

        for name in ("panda_omron_curobo",):
            config = load_execution_config(f"configs/execution/{name}.yaml")
            self.assertEqual(config.trials_per_station, 3)
            stations = [{"station_id": str(i), "geometry": "valid"} for i in range(4)]
            calibration = {
                "complete": True,
                "stations": [{"station_id": str(i), "trials": 3, "successes": i} for i in range(4)],
            }
            low, high = eligible_stations(stations, calibration, config.lastmile_low, config.lastmile_high)
            self.assertEqual([s["station_id"] for s in low], ["0", "1"])
            self.assertEqual([s["station_id"] for s in high], ["3"])
            calibration["complete"] = False
            self.assertEqual(
                eligible_stations(stations, calibration, config.lastmile_low, config.lastmile_high), ([], [])
            )

    def test_reject_unknown_and_invalid_config(self):
        for args in ({"typo": 1}, {"max_steps": 0}, {"width": 321}, {"penetration_ratio": float("nan")}):
            with self.assertRaises((ValueError, TypeError)):
                CollectionConfig(**args)
        with self.assertRaises(ValueError):
            RobotConfig("x", torso_limits=[0, 2])
