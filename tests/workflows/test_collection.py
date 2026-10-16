"""标定复用、连续采集、attempt 编号和共享故障停批。"""

import tempfile
import unittest
from dataclasses import replace
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lastmile_dataflow.data.evaluation.selection import (
    recovery_choices,
    station_calibration,
)
from lastmile_dataflow.utils.io import read_json, rows
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.workflows.collection import (
    _execute,
    _recovery_trials,
    _station_trials,
    collect_lastmile,
    evaluate_stations,
)
from tests.helpers import DatasetTestCase


class CollectionTests(DatasetTestCase):
    def test_scene_collector_writes_direct_records_no_child_run(self):
        dataset = self.dataset()

        def execute(baseline, scene, station, *args, **kwargs):
            return {
                "status": "success",
                "reason": "mocked",
                "recording_complete": True,
                "rollout_id": "mock-" + station["station_id"],
                "executed_steps": 1,
            }

        with (
            patch("lastmile_dataflow.workflows.collection._execute", side_effect=execute),
            patch("lastmile_dataflow.workflows.collection.export_heatmap"),
            patch("lastmile_dataflow.workflows.collection._recovery_trials") as recovery,
        ):
            output = evaluate_stations(
                dataset,
                self.root,
                ExecutionConfig(trials_per_station=1),
                self.collection,
                self.root,
                run_id="run",
                gpu_ids=[0],
            )
        recovery.assert_not_called()
        self.assertEqual(len(rows(output / "trials.jsonl")), 2)
        self.assertEqual({p.name for p in output.iterdir()}, {"config.json", "trials.jsonl", "summary.json"})
        self.assertTrue(read_json(output / "summary.json")["scenes"][0]["calibration_complete"])

    def test_reused_calibration_does_not_repeat_trials(self):
        dataset = self.dataset()
        config = ExecutionConfig(trials_per_station=1)

        def execute(baseline, scene, station, *args, **kwargs):
            return {
                "status": "failure" if station["station_id"] == "s0" else "success",
                "reason": "mocked",
                "rollout_id": station["station_id"],
                "recording_complete": True,
            }

        with (
            patch("lastmile_dataflow.workflows.collection._execute", side_effect=execute),
            patch("lastmile_dataflow.workflows.collection.export_heatmap"),
        ):
            calibration = evaluate_stations(
                dataset, self.root, config, self.collection, self.root, run_id="calibration", gpu_ids=[0]
            )
        with (
            patch("lastmile_dataflow.workflows.collection._station_trials") as station_trials,
            patch("lastmile_dataflow.workflows.collection._recovery_trials", return_value=[]),
            patch("lastmile_dataflow.workflows.collection.export_heatmap"),
        ):
            output = collect_lastmile(
                dataset,
                self.root,
                config,
                self.collection,
                self.root,
                run_id="recovery",
                gpu_ids=[0],
                calibration=calibration,
            )
        station_trials.assert_not_called()
        self.assertFalse((output / "trials.jsonl").exists())
        with self.assertRaisesRegex(ValueError, "calibration"):
            collect_lastmile(
                dataset,
                self.root,
                replace(config, attempt_timeout_s=9.0),
                self.collection,
                self.root,
                run_id="mismatched",
                gpu_ids=[0],
                calibration=calibration,
            )

    def test_continuous_run_calibrates_once_in_the_same_directory(self):
        dataset = self.dataset()
        config = ExecutionConfig(trials_per_station=1)
        with (
            patch(
                "lastmile_dataflow.workflows.collection._execute",
                return_value={"status": "failure", "reason": "mocked", "recording_complete": True},
            ) as execute,
            patch("lastmile_dataflow.workflows.collection._recovery_trials", return_value=[]) as recovery,
            patch("lastmile_dataflow.workflows.collection.export_heatmap"),
        ):
            output = collect_lastmile(
                dataset, self.root, config, self.collection, self.root, run_id="continuous", gpu_ids=[0]
            )
        self.assertEqual(execute.call_count, len(self.points))
        recovery.assert_called_once()
        self.assertEqual(recovery.call_args.args[-1], output)
        self.assertEqual(len(rows(output / "trials.jsonl")), len(self.points))
        self.assertEqual({p.name for p in output.iterdir()}, {"config.json", "trials.jsonl", "summary.json"})

    def test_incomplete_calibration_never_executes_recovery(self):
        dataset = self.dataset()
        config = ExecutionConfig(trials_per_station=2)
        with (
            patch(
                "lastmile_dataflow.workflows.collection._execute",
                return_value={"status": "failure", "reason": "mocked", "recording_complete": True},
            ),
            patch("lastmile_dataflow.workflows.collection.export_heatmap"),
        ):
            calibration = evaluate_stations(
                dataset,
                self.root,
                config,
                self.collection,
                self.root,
                run_id="partial",
                gpu_ids=[0],
                max_trials=1,
            )
        with (
            patch("lastmile_dataflow.workflows.collection._station_trials") as trials,
            patch("lastmile_dataflow.workflows.collection._execute") as execute,
            patch("lastmile_dataflow.workflows.collection.export_heatmap"),
        ):
            output = collect_lastmile(
                dataset,
                self.root,
                config,
                self.collection,
                self.root,
                run_id="no-recovery",
                gpu_ids=[0],
                calibration=calibration,
            )
        trials.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(rows(output / "episodes.jsonl"), [])
        self.assertFalse(read_json(output / "summary.json")["scenes"][0]["calibration_complete"])

    def test_no_path_is_a_negative_record_without_rollout(self):
        config = ExecutionConfig(trials_per_station=1, start_count=1)
        trials = [
            {"station_id": "s0", "trial": 0, "status": "failure"},
            {"station_id": "s1", "trial": 0, "status": "success"},
        ]
        binding = {"scene_id": "one", "task_id": "pick-target"}
        rates = station_calibration(self.points, trials, 1, binding, config)
        choices = recovery_choices(self.points, rates, binding, config)
        with (
            patch("lastmile_dataflow.workflows.collection.search", return_value=None),
            patch("lastmile_dataflow.workflows.collection._execute") as execute,
        ):
            records = _recovery_trials(
                self.sim,
                self.scene,
                choices,
                self.root,
                config,
                self.collection,
                self.root,
                attempt_ids=count(),
            )
        execute.assert_not_called()
        self.assertEqual(records[0]["status"], "path_unavailable")
        self.assertIsNone(records[0]["rollout_id"])
        self.assertEqual(rows(self.root / "episodes.jsonl"), records)


class AttemptCounterTests(DatasetTestCase):
    def test_attempt_counter_is_shared_across_scenes_without_file_scans(self):
        ids = count()
        config = ExecutionConfig(trials_per_station=1)
        result = {"status": "failure", "reason": "fixture", "recording_complete": True}
        with (
            patch("lastmile_dataflow.workflows.collection.run_curobo_attempt", return_value=result) as run,
            patch("lastmile_dataflow.workflows.collection.grasp_candidates", return_value=[]),
            patch("lastmile_dataflow.workflows.collection.rows", side_effect=AssertionError("no scans")),
        ):
            for scene_id in ("one", "two"):
                scene = {
                    "scene_id": scene_id,
                    "task": {"task_id": "pick"},
                    "stations": [{"station_id": "s0"}, {"station_id": "s1"}],
                }
                _station_trials(None, scene, self.root, config, None, self.root, attempt_ids=ids)
        self.assertEqual(
            [call.kwargs["attempt_id"] for call in run.call_args_list],
            ["r000000", "r000001", "r000002", "r000003"],
        )


class PolicyBatchStopTests(unittest.TestCase):
    def test_partial_budget_progress_does_not_inflate_completed_points(self):
        scene = {"scene_id": "one", "task": {"task_id": "pick"},
                 "stations": [{"station_id": "S0"}, {"station_id": "S1"}]}
        results = [{"status": status, "reason": status} for status in
                   ("success", "episode_error", "planning_no_solution")]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("lastmile_dataflow.workflows.collection._execute", side_effect=results),
            self.assertLogs("lastmile_dataflow.workflows.collection", level="INFO") as logs,
        ):
            trials = _station_trials(None, scene, None, SimpleNamespace(seed=17, trials_per_station=2),
                                     None, Path(tmp), budget=3, attempt_ids=count())
            self.assertEqual(len(rows(Path(tmp) / "trials.jsonl")), 3)
        self.assertEqual([row["status"] for row in trials], [row["status"] for row in results])
        final = logs.records[-1]
        self.assertTrue(final.progress)
        self.assertIn("已处理站位=1/2 有效完成站位=0/2", final.message)
        self.assertIn("trial=3/3 完整计划=4 有效=2 成功=1 异常或未知=1", final.message)

    def test_trial_result_log_levels(self):
        scene = {"scene_id": "one", "task": {"task_id": "pick"}}
        config = SimpleNamespace(operation_backend="molmobot")
        for status, level in [("planning_no_solution", "DEBUG"), ("timeout", "WARNING"),
                              ("recording_error", "ERROR"), ("infrastructure_error", "ERROR")]:
            with (
                self.subTest(status=status),
                patch("lastmile_dataflow.workflows.collection.run_vla_attempt", return_value={"status": status}),
                self.assertLogs("lastmile_dataflow.workflows.collection", level="DEBUG") as logs,
            ):
                result = _execute(None, scene, {"station_id": "S0"}, None, config, None,
                                  Path("unused"), 17, attempt_ids=count())
            self.assertEqual(result["status"], status)
            self.assertEqual(logs.records[-1].levelname, level)

    def test_shared_failure_preserves_one_unknown_trial_and_stops(self):
        scene = {
            "scene_id": "one",
            "task": {"task_id": "pick"},
            "stations": [{"station_id": "S0"}, {"station_id": "S1"}],
        }
        config = SimpleNamespace(seed=17, trials_per_station=2)
        result = {
            "status": "infrastructure_error",
            "failure_scope": "shared",
            "reason": "model response timeout",
            "executed_steps": 0,
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("lastmile_dataflow.workflows.collection._execute", return_value=result) as execute,
        ):
            trials = _station_trials(None, scene, None, config, None, Path(tmp), attempt_ids=count())
            self.assertEqual(len(trials), 1)
            self.assertEqual(len(rows(Path(tmp) / "trials.jsonl")), 1)
            execute.assert_called_once()
