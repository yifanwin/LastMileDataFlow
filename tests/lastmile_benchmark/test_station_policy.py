"""Target-facing sampling, isolated repeats, protocol selection and complete coverage."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.lastmile_benchmark import station_policy
from lastmile_dataflow.lastmile_benchmark.policy_rollout import VLARollout
from lastmile_dataflow.operation.outcomes.pick_state import RL_PICK_THRESHOLDS
from lastmile_dataflow.operation.outcomes.task import evaluate_pick, constraint, evaluate
from lastmile_dataflow.station_map.rl.env.stations import station_candidates
from lastmile_dataflow.utils.io import read_json, rows, write_json, write_rows
from tests.helpers import evidence


class StationPolicyTests(unittest.TestCase):
    def test_facing_station_matches_original_zero_offset_identity(self):
        settings = {"radius_m": 0.2, "spacing_m": 0.1, "heading_offsets_deg": list(range(-60, 61, 15))}
        center = np.array([1., 2., 0.])
        candidates = station_candidates(center, settings)
        facing = [row for row in candidates if row["yaw_offset_rad"] in (None, 0.)]
        self.assertEqual(len(facing), len({r["xy_id"] for r in candidates}))
        for row in facing:
            if row["valid"]:
                self.assertTrue(row["station_id"].endswith("-y04"))
                self.assertAlmostEqual(row["yaw"], np.arctan2(2-row["y"], 1-row["x"]))
            else:
                self.assertEqual(row["invalid_reason"], "no_heading")

    def test_rl_thresholds_and_slip_do_not_change_default(self):
        initial, trace = evidence(1.04, slip=True)
        for row in trace:
            row["height_m"] = initial["height_m"] + 0.011
            row["base"][0] = 0.1  # policy base movement is allowed
        self.assertEqual(evaluate_pick(trace, initial, constraint_checker=constraint)["status"], "failure")
        verdict = evaluate_pick(trace, initial, constraint_checker=constraint, thresholds=RL_PICK_THRESHOLDS)
        self.assertEqual(verdict["status"], "success")
        self.assertEqual(verdict["reason"], "rl_pick")
        for row in trace:
            row["support_contact"] = True
        self.assertEqual(evaluate_pick(trace, initial, constraint_checker=constraint, thresholds=RL_PICK_THRESHOLDS)["status"], "failure")

    def test_online_rl_stops_on_physics_boundary_without_waiting_for_periodic_verdict(self):
        initial, trace = evidence(1.04, slip=True)
        for row in trace:
            row["height_m"] = initial["height_m"] + 0.011
        rollout = VLARollout.__new__(VLARollout)
        rollout.pick_thresholds = RL_PICK_THRESHOLDS
        rollout.pick_windows = {"right": (np.asarray(-1.), np.zeros(3), np.eye(3))}
        rollout.samples = {"right": []}
        monitor = SimpleNamespace(initial=initial)
        completed = []
        for sample in trace:
            rollout.samples["right"].append(sample)
            if rollout._pick_complete("right", monitor):
                completed.append(sample["time_s"])
                break
        self.assertAlmostEqual(completed[0], 1.)
        self.assertEqual(evaluate({"operation": "pick"}, rollout.samples, {"right": initial}, 1., pick_thresholds=RL_PICK_THRESHOLDS)["status"], "success")

    def test_worker_always_runs_three_seeds_and_keeps_baseline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = {"base_xyyaw": [-10, -10, 0], "arm_q": {"right": [0] * 7}, "lift": .2, "gripper_width": {}}
            saved = {"gpu_ids": [2], "robot": {}, "task": {"operation": "pick"}, "attempt_timeout_s": 300,
                     "scene_config": {"scene": {"target": "Cup_30"}}}
            write_json(root / "config.json", saved)
            write_rows(root / "selected-stations.jsonl", [{"station_id": "S0-y04", "x": 1., "y": 2., "yaw": .3}])
            baseline = Mock()
            policy = Mock()
            snapshots = []
            def attempt(sim, task, station, *args, seed, **kwargs):
                snapshots.append(deepcopy(station["initial"]))
                station["initial"]["arm_q"]["right"][0] = 99
                return {"status": "success", "station_id": station["station_id"], "exception_detail": None}
            with (
                patch.object(station_policy, "parse_robot_config", return_value=SimpleNamespace(initial=initial)),
                patch.object(station_policy.Simulation, "from_snapshot", return_value=baseline),
                patch.object(station_policy, "Pi05Client", return_value=policy),
                patch.object(station_policy, "run_vla_attempt", side_effect=attempt) as runner,
            ):
                station_policy.run_worker(root, gpu=2, worker_index=0)
            self.assertEqual([call.kwargs["seed"] for call in runner.call_args_list], [42, 43, 44])
            self.assertEqual([s["arm_q"]["right"][0] for s in snapshots], [0, 0, 0])
            self.assertEqual(initial["base_xyyaw"], [-10, -10, 0])
            self.assertEqual(len(rows(root / "trials-gpu-2.jsonl")), 3)
            policy.close.assert_called_once()
            baseline.close.assert_called_once()

    def test_missing_results_remain_incomplete_and_errors_are_separate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "unit" / "runs" / "test"
            write_json(root / "config.json", {"target_center": [0, 0]})
            write_rows(root / "selected-stations.jsonl", [{"station_id": "s", "x": 1, "y": 0}])
            write_rows(root / "trials-gpu-0.jsonl", [
                {"station_id": "s", "seed": 42, "status": "success", "trajectory_metrics": {"forbidden_contact_count": 0}},
                {"station_id": "s", "seed": 43, "status": "infrastructure_error", "reason": "model_lost"},
            ])
            summary = station_policy.summarize(root)
            self.assertEqual(summary["status"], "incomplete")
            self.assertEqual(summary["success_rate"], 1/3)
            self.assertEqual(summary["infrastructure_errors"], 1)
            self.assertEqual(summary["failure_reasons"], {})
            self.assertEqual(rows(root / "station-results.jsonl")[0]["missing_seeds"], [44])
            self.assertTrue((root.parent.parent / "analysis" / "test" / "station-success.png").is_file())
            self.assertEqual(read_json(root / "summary.json")["station_coverage"], 1.)
