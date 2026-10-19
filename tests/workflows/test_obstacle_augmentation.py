"""只验证增强编排、记录与关闭责任，不代表真实放置或物理检查通过。"""

import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.utils.io import read_json, rows
from lastmile_dataflow.scenes.augmentation.config import ObstacleConfig
from lastmile_dataflow.scenes.dataset_config import SceneStationConfig
from lastmile_dataflow.workflows import obstacle_augmentation as workflow


class ObstacleWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.robot = RobotConfig("robot.xml")
        self.geometry = SceneStationConfig(radius_m=.8, spacing_m=.1)
        self.config = ObstacleConfig(
            baseline_config="baseline.json", dataset_dir="dataset-val", asset_library="assets",
            target_instance="cup", variant_count=2, obstacle_count=[6, 9], surface_categories=["Cup"],
            schema_version="procedural-obstacles-v3",
            construction={"case": "Case1", "rule_version": "scene-cases-v3", "min_prefilter_points": 1},
        )
        self.baseline = Mock()
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.task = {"instance_id": "cup", "operation": "pick", "category": "Cup", "anchor_world": [0, 0, 1]}
        replacements = {
            "read_json": Mock(return_value={}),
            "read_config": Mock(return_value={"robot": asdict(self.robot)}),
            "discover_tasks": Mock(return_value=([self.task], [])),
            "task_support_obstacles": Mock(return_value=[]),
            "park_robot": Mock(), "settle_scene": Mock(),
            "sample_stations": Mock(return_value=[{"geometry": "valid"}]),
            "support_regions": Mock(return_value=[Mock()]),
            "station_regions": Mock(return_value={"points": []}),
            "robot_footprint": Mock(return_value={"radius_m": 0.3}),
            "approach_points": Mock(),
            "environment_pool": Mock(return_value={"ground": ["Chair"], "surface": ["Cup"]}),
            "ThorLibrary": Mock(), "asset_geometry": Mock(return_value=[]),
            "surface_points": Mock(return_value=[{"xy": [0.1, 0]}, {"xy": [2, 0]}]),
            "ObstacleEditSession": Mock(return_value=self.session), "build_variant": Mock(),
        }
        for name, value in replacements.items():
            patcher = patch.object(workflow, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("from_source", "from_snapshot"):
            patcher = patch.object(workflow.Simulation, name, return_value=self.baseline)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_augmentation(self, **kwargs):
        return workflow.augment_obstacles(
            self.config, self.root, run_id="fixture", geometry_config=self.geometry,
            robot_config=self.robot, **kwargs,
        )

    def test_standalone_counts_results_and_releases_each_variant(self):
        workflow.build_variant.side_effect = [{"scene_id": "fixture-v000", "status": "accepted"}, TimeoutError("budget")]
        with self.assertLogs(workflow.logger, level="INFO") as logs, patch(
            "lastmile_dataflow.data.visualization.scene_preview.export_scene_previews"
        ) as preview:
            root = self.run_augmentation()
        self.assertFalse(any("变体 1/2" in message for message in logs.output))
        self.assertTrue(any(record.progress and "已处理变体=2/2 接受=1 拒绝=1" in record.message
                            for record in logs.records if hasattr(record, "progress")))
        self.assertTrue(any("fixture-v001: rejected budget" in message for message in logs.output))
        self.assertTrue(any(record.levelname == "WARNING" and "rejected budget" in record.message
                            for record in logs.records))
        records = rows(root / "scenes.jsonl")
        self.assertEqual([row["status"] for row in records], ["accepted", "rejected"])
        self.assertEqual(records[0]["scene"], "scene-files/fixture-v000/scene")
        self.assertEqual(records[0]["before_scene"], "scene-files/fixture-v000/before-scene")
        preview.assert_called_once_with(root)
        self.assertEqual(records[1]["reason"], "budget")
        self.assertEqual(read_json(root / "summary.json"), {"status": "completed", "accepted": 1})
        self.assertEqual(self.session.reject.call_count, 2)
        self.baseline.close.assert_called_once()
        workflow.Simulation.from_source.assert_called_once_with(
            workflow.SceneSource.procthor("dataset-val", 103), self.robot, target="cup"
        )
        surfaces = workflow.build_variant.call_args.args[8]
        self.assertEqual(surfaces, [{"xy": [0.1, 0]}])
        workflow.approach_points.assert_called_once()

    def test_snapshot_writes_only_parent_records(self):
        self.config = replace(self.config, input_snapshot="snapshot")
        workflow.build_variant.side_effect = [
            {"scene_id": f"fixture-v{i:03d}", "status": "rejected"} for i in range(2)
        ]
        root = self.run_augmentation(dataset_root=self.root)
        self.assertEqual(len(rows(root / "scenes.jsonl")), 2)
        self.assertFalse((root / "config.json").exists())
        self.assertFalse((root / "summary.json").exists())
        workflow.Simulation.from_snapshot.assert_called_once_with("snapshot", self.robot, target="cup")
        workflow.Simulation.from_source.assert_not_called()
        self.baseline.close.assert_called_once()

    def test_invalid_baseline_stops_before_asset_loading(self):
        workflow.sample_stations.return_value = []
        with self.assertLogs(workflow.logger, level="WARNING") as logs:
            root = self.run_augmentation()
        self.assertIn("invalid_baseline", logs.output[0])
        self.assertEqual(rows(root / "scenes.jsonl"), [
            {"scene_id": "fixture", "status": "rejected", "reason": "invalid_baseline"}
        ])
        workflow.ThorLibrary.assert_not_called()
        self.baseline.close.assert_called_once()

    def test_unexpected_build_error_closes_session_and_baseline(self):
        workflow.build_variant.side_effect = OSError("write failed")
        with self.assertRaisesRegex(OSError, "write failed"):
            self.run_augmentation()
        self.session.reject.assert_called_once()
        self.session.__exit__.assert_called_once()
        self.baseline.close.assert_called_once()
