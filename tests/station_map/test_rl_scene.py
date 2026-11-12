"""The RL robot namespace comes from one place: mjlab's entity prefix plus the protocol namespace."""

from dataclasses import asdict
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lastmile_dataflow.robots.interface import ROBOT_NAMESPACE
from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from lastmile_dataflow.station_map.rl.env.scene import ROBOT_ENTITY, ROBOT_PREFIX, EntityPandaConfig, RLSceneConfig
from lastmile_dataflow.station_map.rl.config import resolve_config
from lastmile_dataflow.station_map.rl.env.stations import spatial_split, station_candidates
from lastmile_dataflow.scenes.tasks import disk_samples
from lastmile_dataflow.utils.io import read_config


class StationGeometryTests(unittest.TestCase):
    def test_robot_dimensions_change_the_sampling_lattice(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "robot.yaml"
            config = {"stations": {"robot_config": str(path), "heading_offsets_deg": [-60, 0, 60]}}
            path.write_text("arm_working_reach_m: 0.8\nbase_radius_m: 0.4\n")
            first = resolve_config(config)
            path.write_text("arm_working_reach_m: 0.4\nbase_radius_m: 0.2\n")
            second = resolve_config(first)
        self.assertNotIn("radius_m", config["stations"])
        self.assertEqual(first["stations"]["radius_m"], 0.8)
        self.assertEqual(first["stations"]["spacing_m"], 0.1)
        self.assertEqual(second["stations"]["radius_m"], 0.4)
        self.assertEqual(second["stations"]["spacing_m"], 0.05)
        grids = [disk_samples([0, 0], c["stations"]["radius_m"], c["stations"]["spacing_m"]) for c in (first, second)]
        self.assertEqual(len(grids[0]), len(grids[1]))
        for original, scaled in zip(*grids):
            self.assertEqual(scaled["xy"], [v / 2 for v in original["xy"]])
        self.assertEqual(second["stations"]["heading_offsets_deg"], [-60, 0, 60])

    def test_current_config_uses_robot_dimensions(self):
        config = read_config("configs/station_map/rl/val103_cup30.yaml")
        robot = read_config(config["stations"]["robot_config"])
        resolved = resolve_config(config)["stations"]
        self.assertEqual(resolved["radius_m"], robot["arm_working_reach_m"])
        self.assertEqual(resolved["spacing_m"], robot["base_radius_m"] / 4)

    def test_fixed_station_preserves_historical_pose_outside_current_grid(self):
        config = resolve_config(read_config("configs/station_map/rl/val103_cup30_s0246_y04.yaml"))
        settings = config["stations"]
        grid = disk_samples([0, 0], settings["radius_m"], settings["spacing_m"])
        self.assertNotIn("S0246", [p["xy_id"] for p in grid])
        for center in ([0, 0, 0], settings["fixed_station"]["base_xyyaw"]):
            rows = station_candidates(np.asarray(center), settings)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["station_id"], "S0246-y04")
            self.assertEqual([row[k] for k in ("x", "y", "yaw")], settings["fixed_station"]["base_xyyaw"])
            self.assertEqual(row["yaw"], -math.pi / 2)
            self.assertEqual(row["split"], "train")
            self.assertEqual(row["invalid_reason"], "")

    def test_default_grid_keeps_heading_fan_splits_and_no_heading_marker(self):
        config = resolve_config(read_config("configs/station_map/rl/val103_cup30.yaml"))
        settings = config["stations"]
        center = np.array([3.0, 2.0, 0.0])
        points = disk_samples(center, settings["radius_m"], settings["spacing_m"])
        labels = spatial_split(points, settings["block_m"], settings["split"], settings["seed"])
        splits = {p["xy_id"]: label for p, label in zip(points, labels)}
        rows = station_candidates(center, settings)
        self.assertEqual(len(rows), (len(points) - 1) * 9 + 1)
        self.assertEqual(sum(r["invalid_reason"] == "no_heading" for r in rows), 1)
        row = next(r for r in rows if r["station_id"] == "S0000-y04")
        self.assertAlmostEqual(row["yaw"], math.atan2(center[1] - row["y"], center[0] - row["x"]))
        self.assertEqual(row["yaw_offset_rad"], 0)
        self.assertTrue(all(r["split"] == splits[r["xy_id"]] for r in rows))


class RobotNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.scene = RLSceneConfig(
            data_root="data", scene_xml="s.xml", scene_metadata="m.json", robot_xml="r.xml", robot_scale=0.9, lift=0.3
        )

    def test_entity_prefix_wraps_the_protocol_namespace(self):
        self.assertEqual(ROBOT_PREFIX, ROBOT_ENTITY + "/" + ROBOT_NAMESPACE)
        self.assertEqual(ROBOT_PREFIX, "robot/robot_0/")

    def test_source_and_entity_configs_differ_only_in_namespace(self):
        source = self.scene.robot_config()
        entity = self.scene.robot_config(entity=True)
        self.assertIs(type(source), PandaOmronConfig)
        self.assertIsInstance(entity, EntityPandaConfig)
        self.assertEqual(source.namespace, ROBOT_NAMESPACE)
        self.assertEqual(entity.namespace, ROBOT_PREFIX)
        self.assertEqual(asdict(source), asdict(entity))
        self.assertEqual(entity.initial["lift"], 0.3)
        self.assertEqual(entity.initial["arm_q"]["right"], list(self.scene.home))


if __name__ == "__main__":
    unittest.main()
