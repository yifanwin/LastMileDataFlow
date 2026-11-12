"""标定 run 与连续采集 run：运行准备、标定复用、全组合配对、负结果和共享故障停批。

标定本身（CPU/GPU 调度）见 test_split_calibration；这里替换成写好的 trial 记录，
连续执行通过 run_collection 的 execute 参数注入。
"""

from collections import Counter
from unittest.mock import patch

from lastmile_dataflow.station_map.config import StationRun
from lastmile_dataflow.utils.io import append_row, read_json, rows, write_json
from lastmile_dataflow.workflows import collect, station_map
from lastmile_dataflow.workflows.collect import run_collection
from lastmile_dataflow.workflows.station_map import prepare_run, run_station_map
from tests.helpers import DatasetTestCase, add_scene

PATH = {"xy": [[0.0, 0.0], [1.0, 1.0], [3.0, 1.0]], "poses": [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0], [3.0, 1.0, 0.0]]}
SUCCESS = {
    "status": "success",
    "reason": "synthetic",
    "task_outcome": {"status": "success"},
    "recording_complete": True,
    "observation_complete": True,
    "executed_steps": 3,
    "navigation": {"status": "success", "steps": 2},
}
SHARED = {
    "status": "infrastructure_error",
    "failure_scope": "shared",
    "reason": "cuda error",
    "executed_steps": 2,
    "navigation": {"status": "unknown", "steps": 2},
}


def trial_rows(scene_id, task_id, counts, trials_per_station):
    """``counts[i]`` successes out of ``trials_per_station`` at station ``s{i}``."""
    return [
        {
            "scene_id": scene_id,
            "task_id": task_id,
            "station_id": f"s{i}",
            "station_index": i,
            "trial": trial,
            "seed": trial,
            "rollout_id": f"{scene_id}-r{i}-{trial}",
            "status": "success" if trial < successes else "failure",
            "task_outcome": {"status": "success" if trial < successes else "failure"},
            "recording_complete": True,
            "observation_complete": True,
            "executed_steps": 3,
        }
        for i, successes in enumerate(counts)
        for trial in range(trials_per_station)
    ]


class Executor:
    """Stands in for run_continuous_attempt and records the attempt ids it was given."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(
        self, sim, task, start, candidates, assets_dir, config, collection, *, root, attempt_id, seed, path, goal
    ):
        self.calls.append(attempt_id)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return {**result, "rollout_id": attempt_id}


class CollectionTests(DatasetTestCase):
    def setUp(self):
        super().setUp()
        # These fixtures do not write trajectories; export has its own file tests.
        for started in (
            patch.object(collect, "finalize_dataset"),
            patch.object(station_map, "export_heatmap"),
            patch.object(collect, "grasp_candidates", return_value=[]),
        ):
            started.start()
            self.addCleanup(started.stop)

    def run_config(self, name="run", **execution):
        path = self.root / f"{name}-execution.yaml"
        write_json(path, {"trials_per_station": 1, **execution})
        return StationRun(str(path), assets_dir=str(self.root), cpu_workers=2, preparation_workers=1)

    def six_station_dataset(self, scene_ids=("one",)):
        self.scene["stations"] = [
            {**self.points[0], "station_id": f"s{i}", "xy": [i * 2.0, 0.0], "base": [i * 2.0, 0.0, 0.0]}
            for i in range(6)
        ]
        dataset = self.dataset()
        for scene_id in scene_ids[1:]:
            add_scene(dataset, {**self.scene, "scene_id": scene_id})
        return dataset

    def calibrate(self, dataset, trials, config, name="calibration"):
        """A finished station-map run whose calibration produced exactly ``trials``."""

        def fake(context):
            for row in trials:
                append_row(context.root / "trials.jsonl", row)
            return trials, {"failure": None}

        with patch.object(station_map, "calibrate_stations", side_effect=fake):
            return run_station_map(dataset, config, self.root / name, gpu_ids=[0])

    def collect(self, dataset, config, calibration, execute, name="collect"):
        with (
            patch.object(collect, "navigation_grid"),
            patch.object(collect, "robot_footprint", return_value={"radius_m": 0.1, "max": [0, 0, 1]}),
            patch.object(collect, "GeometryNavigation") as navigator,
        ):
            navigator.return_value.plan.side_effect = lambda start, goals: self.plans.pop(0)
            return run_collection(
                dataset, config, self.root / name, gpu_ids=[0], calibration=calibration, execute=execute
            )

    def test_prepared_run_keeps_inputs_and_logs_and_refuses_overwrite(self):
        dataset = self.dataset()
        root = self.root / "prepared"
        write_json(root / "scene-inputs" / "summary.json", {"accepted": 1})
        (root / "run.log").write_text("preparation completed\n")
        prepare_run(dataset, self.run_config(), root, [0], collect=False)
        self.assertEqual(read_json(root / "scene-inputs" / "summary.json"), {"accepted": 1})
        self.assertEqual((root / "run.log").read_text(), "preparation completed\n")
        with self.assertRaises(FileExistsError):
            prepare_run(dataset, self.run_config(), root, [0], collect=False)

    def test_sampling_geometry_does_not_override_execution_workspace(self):
        dataset = self.dataset()
        write_json(
            dataset / "config.json",
            {
                "geometry": {
                    "radius_m": 1.0,
                    "spacing_m": 0.25,
                    "width": 256,
                    "height": 256,
                    "navigation_margin_m": 0.8,
                    "navigation_whole_body": False,
                }
            },
        )
        config = self.run_config(
            radius_m=2.0, local_base_radius_m=0.35, navigation_margin_m=0.02, navigation_whole_body=True
        )
        context = prepare_run(dataset, config, self.root / "geometry", [0], collect=True)
        effective = context.config
        self.assertEqual((effective.radius_m, effective.local_base_radius_m), (2.0, 0.35))
        self.assertEqual(effective.navigation_margin_m, 0.02)
        self.assertTrue(effective.navigation_whole_body)
        self.assertEqual((effective.spacing_m, effective.width, effective.height), (0.25, 256, 256))
        saved = read_json(context.root / "config.json")
        self.assertEqual(saved["config"]["radius_m"], 2.0)
        self.assertTrue(saved["collection"]["record_images"])
        self.assertFalse(saved["calibration_collection"]["record_images"])
        self.assertEqual(saved["resources"]["planning_workers_per_gpu"], [1])

    def test_station_map_writes_summary_without_collection(self):
        dataset = self.dataset()
        trials = trial_rows("one", self.task["task_id"], [1, 0], 1)
        output = self.calibrate(dataset, trials, self.run_config())
        self.assertEqual({p.name for p in output.iterdir()}, {"config.json", "trials.jsonl", "summary.json"})
        summary = read_json(output / "summary.json")
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["scenes"][0]["calibration_complete"])
        self.assertEqual(summary["calibration"], {"failure": None})

    def test_reused_calibration_plans_all_pairs_and_keeps_negative_records(self):
        dataset = self.six_station_dataset()
        config = self.run_config(trials_per_station=5)
        trials = trial_rows("one", self.task["task_id"], [0, 1, 4, 5, 5, 2], 5)
        calibration = self.calibrate(dataset, trials, config)
        self.plans = [{"path": None, "reason": "navigation_search_exhausted"}] + [{"path": PATH, "reason": "valid"}] * 5
        execute = Executor(SUCCESS)
        root = self.collect(dataset, config, calibration, execute)
        pairs = rows(root / "pair_index.jsonl")
        self.assertEqual(
            {(p["s0"], p["s1"]) for p in pairs}, {(s0, s1) for s0 in ("s0", "s1") for s1 in ("s2", "s3", "s4")}
        )
        self.assertEqual(len(execute.calls), 5)
        self.assertEqual(pairs[0]["status"], "path_unavailable")
        self.assertIsNone(pairs[0]["rollout_id"])
        self.assertAlmostEqual(pairs[1]["astar_path_length_m"], 2 + 2**0.5)
        self.assertEqual(pairs[1]["navigation_steps"], 2)
        self.assertAlmostEqual(pairs[0]["euclidean_distance_m"], 4.0)
        training = rows(root / "train_index.jsonl")
        # 17 次完整的标定成功（低率点的单次成功也纳入）+ 5 次连续成功。
        self.assertEqual(len(training), 22)
        self.assertEqual(
            Counter(r["source_type"] for r in training), {"s1_direct_operation": 17, "s0_to_s1_continuous": 5}
        )
        summary = read_json(root / "summary.json")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual((summary["planned_pairs"], summary["executed_pairs"]), (6, 5))
        self.assertNotIn("calibration", summary)

    def test_shared_failure_stops_across_scenes_but_preserves_all_planned_pairs(self):
        dataset = self.six_station_dataset(("one", "two"))
        config = self.run_config(trials_per_station=5)
        counts = [0, 1, 4, 5, 5, 2]
        trials = trial_rows("one", self.task["task_id"], counts, 5) + trial_rows("two", self.task["task_id"], counts, 5)
        calibration = self.calibrate(dataset, trials, config)
        self.plans = [{"path": PATH, "reason": "valid"}] * 12
        execute = Executor(SHARED)
        root = self.collect(dataset, config, calibration, execute)
        self.assertEqual(len(execute.calls), 1)
        pairs = rows(root / "pair_index.jsonl")
        self.assertEqual(len(pairs), 12)
        self.assertEqual(pairs[0]["status"], "infrastructure_error")
        self.assertTrue(all(p["status"] == "not_executed" and p["reason"] == "shared_failure" for p in pairs[1:]))
        summary = read_json(root / "summary.json")
        self.assertEqual(summary["status"], "incomplete")
        self.assertEqual(summary["executed_pairs"], 1)
        self.assertAlmostEqual(summary["execution_coverage"], 1 / 12)

    def test_collection_continues_the_run_attempt_counter_after_calibration(self):
        dataset = self.six_station_dataset()
        config = self.run_config(trials_per_station=5)
        trials = trial_rows("one", self.task["task_id"], [0, 1, 4, 5, 5, 2], 5)

        def fake(context):
            next(context.attempt_ids)  # one calibration attempt id consumed
            return trials, {"failure": None}

        self.plans = [{"path": PATH, "reason": "valid"}] * 6
        execute = Executor(SUCCESS)
        with patch.object(collect, "calibrate_stations", side_effect=fake) as calibrate:
            root = self.collect(dataset, config, None, execute)
        calibrate.assert_called_once()
        self.assertEqual(execute.calls, [f"r{i:06d}" for i in range(1, 7)])
        self.assertEqual(read_json(root / "summary.json")["calibration"], {"failure": None})

    def test_failed_calibration_never_executes_recovery(self):
        dataset = self.six_station_dataset()
        trials = trial_rows("one", self.task["task_id"], [0, 1, 4, 5, 5, 2], 5)
        execute = Executor(SUCCESS)
        self.plans = []
        with patch.object(collect, "calibrate_stations", return_value=(trials, {"failure": SHARED})):
            root = self.collect(dataset, self.run_config(trials_per_station=5), None, execute)
        self.assertEqual(execute.calls, [])
        pairs = rows(root / "pair_index.jsonl")
        self.assertTrue(pairs and all(p["reason"] == "shared_failure" for p in pairs))
        self.assertEqual(read_json(root / "summary.json")["status"], "incomplete")

    def test_calibration_reuse_allows_selection_changes_but_not_execution_changes(self):
        dataset = self.dataset()
        trials = trial_rows("one", self.task["task_id"], [0, 0], 1)
        calibration = self.calibrate(dataset, trials, self.run_config())
        selection = self.run_config(
            "selection",
            lastmile_low=0.1,
            lastmile_high=0.9,
            navigation_margin_m=0.03,
            navigation_clearance_weight=3.0,
            navigation_max_penetration_m=0.004,
        )
        self.plans = []
        root = self.collect(dataset, selection, calibration, Executor(SUCCESS), name="reuse-selection")
        self.assertEqual(rows(root / "pair_index.jsonl"), [])
        with self.assertRaisesRegex(ValueError, "configuration mismatch"):
            prepare_run(
                dataset,
                self.run_config("physics", attempt_timeout_s=60.0),
                self.root / "reuse-physics",
                [0],
                collect=True,
                calibration=calibration,
            )

    def test_numeric_success_selects_rates_without_claiming_a_visual_demonstration(self):
        dataset = self.dataset()
        trials = [{**row, "observation_complete": False} for row in trial_rows("one", self.task["task_id"], [1, 0], 1)]
        calibration = self.calibrate(dataset, trials, self.run_config())
        self.plans = [{"path": None, "reason": "navigation_search_exhausted"}]
        root = self.collect(dataset, self.run_config(), calibration, Executor(SUCCESS), name="numeric")
        self.assertEqual(rows(root / "train_index.jsonl"), [])
