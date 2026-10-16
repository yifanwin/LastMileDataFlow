"""benchmark 累计进度与提前退出；转换、可见性和策略使用替身，不证明真实任务成功。"""

from unittest.mock import Mock, patch

from lastmile_dataflow.policies.molmobot_config import MolmoBotConfig
from lastmile_dataflow.runtime.construction.session import clone_preparation
from lastmile_dataflow.utils.io import read_json, rows
from lastmile_dataflow.workflows import lastmilebench as workflow
from tests.helpers import DatasetTestCase


class BenchmarkProgressTests(DatasetTestCase):
    def make_benchmark(self):
        dataset = self.dataset()
        self.policy_config = MolmoBotConfig("unused-checkpoint", "unused-python")
        with (
            patch.object(workflow, "load_policy_config", return_value=self.policy_config),
            patch.object(workflow, "native_scene", side_effect=clone_preparation),
            patch("lastmile_dataflow.data.rendering.head_views.head_frame", return_value=(None, None, {"visible_pixels": 24})),
            patch("lastmile_dataflow.scenes.stations.sampling.robot_contacts", return_value=[]),
            patch("lastmile_dataflow.scenes.stations.initialization.floor_support", return_value=True),
        ):
            return workflow.build(dataset, self.root / "bench", "unused-policy.json", gpu_ids=[0],
                                  allow_uncalibrated=True, starts_per_sample=2)

    def test_fast_build_has_blue_final_counts_without_per_episode_info(self):
        with self.assertLogs(workflow.logger, level="INFO") as logs:
            root = self.make_benchmark()
        self.assertEqual(len(rows(root / "episodes.jsonl")), 2)
        self.assertTrue(any(record.progress and "已筛选场景=1/1 已写入episode=2" in record.message
                            for record in logs.records if hasattr(record, "progress")))
        self.assertFalse(any("保存 episode=" in record.message for record in logs.records))

    def test_early_exit_keeps_partial_coverage_and_closes_policy(self):
        bench = self.make_benchmark()
        policy = Mock()
        output = self.root / "evaluation"
        with (
            patch.object(workflow, "load_policy_config", return_value=self.policy_config),
            patch.object(workflow, "MolmoBotClient", return_value=policy),
            patch.object(workflow, "run_vla_attempt", side_effect=[
                {"status": "success", "reason": "fixture", "executed_steps": 1, "recording_complete": True},
                OSError("fixture write error"),
            ]),
            self.assertLogs(workflow.logger, level="INFO") as logs,
            self.assertRaisesRegex(OSError, "fixture write error"),
        ):
            workflow.evaluate(bench, output, "unused-policy.json", gpu_ids=[0], log_dir=self.root / "logs")
        policy.close.assert_called_once()
        self.assertEqual(len(rows(output / "results.jsonl")), 1)
        summary = read_json(output / "summary.json")
        self.assertEqual(summary["coverage"], 0.5)
        self.assertIsNone(summary["task_success_rate"])
        self.assertTrue(any("已记录episode=1/2" in record.message and record.progress
                            for record in logs.records if hasattr(record, "progress")))
