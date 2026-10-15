"""真实判定函数上的合成 pick/open 轨迹与超时边界。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.runtime.execution.checks import (
    PickEvidenceComplete,
    checkpoint_pick_success,
    hold_pick,
    resolve_timeout,
    timed,
)
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.outcomes.evaluation import (
    attribute,
    evaluate_mobile_pick,
    evaluate_open,
    torso_tracking_error,
)
from lastmile_dataflow.runtime.outcomes.pick import evaluate_pick
from tests.helpers import evidence, make_sim


def context(duration=2.08, **conditions):
    initial, samples = evidence(duration, **conditions)
    return SimpleNamespace(pick_initial=initial, samples=samples, phase="hold", timings={}, arm_tick=Mock())


class EvidenceTests(unittest.TestCase):
    def test_already_successful_hold_stops_before_next_tick_deadline(self):
        ctx = context()
        ctx.arm_tick.side_effect = TimeoutError("attempt_wall_clock_budget")
        verdict = hold_pick(ctx, "right", np.zeros(7), 0.0)
        self.assertEqual(verdict["status"], "success")
        ctx.arm_tick.assert_not_called()

    def test_success_after_one_control_interval_does_not_wait_for_fifty(self):
        ctx = context(1.98)
        complete = context(2.04).samples
        ctx.arm_tick.side_effect = lambda *args: setattr(ctx, "samples", complete)
        self.assertEqual(hold_pick(ctx, "right", np.zeros(7), 0.0)["status"], "success")
        self.assertEqual(ctx.arm_tick.call_count, 1)

    def test_lift_control_boundary_ends_at_first_complete_success(self):
        ctx = context()
        ctx.phase = "lift_actual"
        with self.assertRaises(PickEvidenceComplete) as caught:
            checkpoint_pick_success(ctx)
        self.assertEqual(caught.exception.verdict["status"], "success")
        ctx.phase = "pregrasp"
        checkpoint_pick_success(ctx)

    def test_timeout_preserves_termination_and_full_physical_success(self):
        termination, verdict = resolve_timeout(context(), {"operation": "pick"}, "attempt_wall_clock_budget")
        self.assertEqual(termination["status"], "timeout")
        self.assertEqual(verdict["status"], "success")
        self.assertGreaterEqual(verdict["tail_held_s"], 2.0)

    def test_short_hold_timeout_is_not_success(self):
        ctx = context(1.86)
        ctx.arm_tick.side_effect = TimeoutError("deadline")
        with self.assertRaises(TimeoutError):
            hold_pick(ctx, "right", np.zeros(7), 0.0)
        termination, verdict = resolve_timeout(ctx, {"operation": "pick"}, "deadline")
        self.assertEqual(termination["status"], "timeout")
        self.assertEqual(verdict["status"], "failure")

    def test_support_slip_and_physics_gap_never_promoted(self):
        for kwargs in ({"support": True}, {"slip": True}, {"gap": True}):
            with self.subTest(kwargs=kwargs):
                verdict = resolve_timeout(context(**kwargs), {"operation": "pick"}, "deadline")[1]
                self.assertNotEqual(verdict["status"], "success")

    def test_complete_trace_constraints_checked_not_only_last_frame(self):
        ctx = context()
        ctx.samples[1]["head"][0] = 0.1
        self.assertEqual(resolve_timeout(ctx, {"operation": "pick"}, "deadline")[1]["reason"], "head_drift")

    def test_missing_monitor_and_open_timeout_unknown(self):
        ctx = SimpleNamespace(phase="pregrasp", pick_initial=None)
        self.assertEqual(resolve_timeout(ctx, {"operation": "pick"}, "deadline")[1]["status"], "unknown")
        self.assertEqual(resolve_timeout(ctx, {"operation": "open"}, "deadline")[1]["status"], "unknown")

    def test_timers_account_exception(self):
        ctx = SimpleNamespace(timings={})
        with (
            patch("lastmile_dataflow.runtime.execution.checks.time.monotonic", side_effect=[1.0, 4.0]),
            self.assertRaises(ValueError),
        ):
            with timed(ctx, "planning"):
                raise ValueError("fixture")
        self.assertEqual(ctx.timings["planning"], {"wall_s": 3.0, "calls": 1})

    def test_hold_maximum_not_extended_when_no_success(self):
        ctx = context(support=True)
        self.assertEqual(hold_pick(ctx, "right", np.zeros(7), 0.0)["status"], "failure")
        self.assertEqual(ctx.arm_tick.call_count, 50)

    def test_timeout_runtime_finalizer_retains_task_success_and_control(self):
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.runtime.execution.curobo import run_curobo_attempt

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            baseline, _ = make_sim(root)
            self.addCleanup(baseline.close)

            def manipulation(ctx, *args):
                fixture = context()
                ctx.pick_initial = fixture.pick_initial
                ctx.samples = fixture.samples
                ctx.phase = "hold"
                ctx.active_control = {"arm": "right", "grasp_row": 1, "grasp_source": "fixture"}
                ctx.sim.begin()
                raise TimeoutError("attempt_wall_clock_budget")

            with (
                patch("lastmile_dataflow.runtime.execution.curobo.place_frozen_station"),
                patch("lastmile_dataflow.runtime.execution.curobo.manipulate", side_effect=manipulation),
            ):
                result = run_curobo_attempt(
                    baseline,
                    {"target_body": "target", "operation": "pick"},
                    {"station_id": "s", "base": [0.0, 0.0, 0.0]},
                    [],
                    root,
                    ExecutionConfig(third_person_enabled=False),
                    CollectionConfig(output_dir=d, record_video=False, record_images=False),
                    root=root,
                    attempt_id="fixture-timeout",
                    seed=1,
                )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["execution_termination"]["status"], "timeout")
            self.assertEqual(result["task_outcome"]["status"], "success")
            self.assertEqual(result["control"]["grasp_source"], "fixture")
            self.assertIn("recorder_finalize_wall_s", result["execution_diagnostics"])
            self.assertFalse(baseline.closed)


class MobileManipulationTests(unittest.TestCase):
    def test_mobile_validator_allows_base_motion_but_fixed_protocol_still_rejects(self):
        initial, samples = evidence()
        for row in samples:
            row["base"] = [0.3, 0.2, 0.5]
        self.assertEqual(evaluate_pick(samples, initial)["reason"], "base_drift")
        mobile = evaluate_mobile_pick(samples, initial)
        self.assertEqual((mobile["status"], mobile["protocol"]), ("success", "mobile-pick-v2"))
        samples[-1]["base"] = [2.1, 0.0, 0.0]
        self.assertEqual(evaluate_mobile_pick(samples, initial)["reason"], "base_outside_workspace")

    def test_mobile_validator_records_collision_and_keeps_idle_arm_check(self):
        initial, samples = evidence()
        samples[-1]["illegal"] = ["robot_environment_collision"]
        self.assertEqual(evaluate_mobile_pick(samples, initial)["status"], "success")
        samples[-1]["illegal"] = []
        samples[-1]["idle_arm"][0] = 0.02
        self.assertEqual(evaluate_mobile_pick(samples, initial)["reason"], "idle_arm_drift")

    def test_mobile_torso_error_is_telemetry_but_strict_protocol_unchanged(self):
        initial, samples = evidence()
        samples[-1]["torso"][0] = 0.03
        self.assertAlmostEqual(torso_tracking_error(samples[-1]), 0.03)
        self.assertEqual(evaluate_pick(samples, initial)["reason"], "torso_protocol")
        self.assertEqual(evaluate_mobile_pick(samples, initial)["status"], "success")
        samples[-1]["illegal"] = ["robot_environment_collision"]
        self.assertEqual(evaluate_mobile_pick(samples, initial)["status"], "success")

    def test_mobile_torso_nonfinite_is_still_invalid_evidence(self):
        initial, samples = evidence()
        samples[-1]["torso"][0] = float("nan")
        self.assertEqual(evaluate_mobile_pick(samples, initial)["status"], "infrastructure_error")

    def test_torso_adjust_telemetry_uses_measured_h(self):
        row = {"phase": "torso_adjust", "torso": [0, 0.3, -0.6, 0.3, 0, 0], "command_h": 0.4}
        self.assertEqual(torso_tracking_error(row), 0.0)
        row["phase"] = "pregrasp"
        self.assertAlmostEqual(torso_tracking_error(row), 0.2)


class OpenAndAttributionTests(unittest.TestCase):
    def test_open_requires_real_joint_progress_and_contact_hold(self):
        task = {"joint_initial": 0, "joint_goal": 0.1}
        samples = [
            {"time_s": i * 0.004, "joint_value": 0.1, "finger_forces_n": {"1": 1, "2": 1}} for i in range(276)
        ]
        self.assertEqual(evaluate_open(task, samples, 1.0)["status"], "success")
        samples[-1]["finger_forces_n"] = {}
        self.assertEqual(evaluate_open(task, samples, 1.0)["status"], "failure")

    def test_ik_failure_without_diagnostic_is_not_claimed_unreachable(self):
        self.assertEqual(attribute("planning_no_solution")["category"], "PlanningFailure")
