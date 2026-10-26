"""躯干命令边界、反馈与导航保持。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.runtime.control.action import InvalidAction
from lastmile_dataflow.runtime.control.torso_command import (
    FEEDBACK_TOLERANCE_RAD,
    TORSO_SEVERE_FEEDBACK_RAD,
    bounded_torso_command,
    torso_feedback_margin,
)
from lastmile_dataflow.runtime.execution.context import (
    ContinuousContext,
    OperationFailure,
)
from lastmile_dataflow.runtime.execution.navigation import navigate
from lastmile_dataflow.runtime.outcomes.contacts import NavigationContacts
from lastmile_dataflow.runtime.execution_config import ExecutionConfig


class TorsoCommandTests(unittest.TestCase):
    def test_recorded_incident_and_limits(self):
        self.assertEqual(bounded_torso_command(0.739327605117437, (0, 0.738)), 0.738)
        self.assertLess(torso_feedback_margin(0.739327605117437, (0, 0.738)), FEEDBACK_TOLERANCE_RAD)
        self.assertEqual(bounded_torso_command(-0.001, (0, 0.738)), 0)
        self.assertEqual(bounded_torso_command(0.4, (0, 0.738)), 0.4)
        for h in [0.75, -0.01, float("nan"), float("inf")]:
            with self.assertRaises(InvalidAction):
                bounded_torso_command(h, (0, 0.738))

    def test_severe_guard_is_far_above_the_recorded_tolerance(self):
        # record_only semantics: a small excursion is telemetry, a gross one stops.
        self.assertGreater(TORSO_SEVERE_FEEDBACK_RAD, FEEDBACK_TOLERANCE_RAD * 10)
        # the exact excursion observed in the 20261010 221404 run must not stop.
        self.assertLess(torso_feedback_margin(-0.0030406353838408396, (0, 0.738)), TORSO_SEVERE_FEEDBACK_RAD)

    def test_hold_uses_command_not_feedback(self):
        ctx = ContinuousContext.__new__(ContinuousContext)
        ctx.sim = SimpleNamespace(
            robot=SimpleNamespace(
                control_height=lambda: .738, group=lambda _: [0, 0.739327605117437], config=SimpleNamespace(torso_limits=(0, 0.738))
            )
        )
        ctx.last_legal_torso_target = 0.737261950969696
        self.assertEqual(ctx.hold_torso_target(), 0.737261950969696)
        ctx.last_legal_torso_target = None
        self.assertEqual(ctx.hold_torso_target(), 0.738)

    def test_tick_clips_records_and_checks_feedback(self):
        # (feedback, expect_stop): a mild overshoot is recorded (record_only), a
        # gross one still stops the attempt.
        for feedback, fails in [(0.739327605117437, False), (0.745, False), (0.80, True)]:
            with self.subTest(feedback=feedback), tempfile.TemporaryDirectory() as directory:
                ctx = ContinuousContext.__new__(ContinuousContext)
                ctx.config = ExecutionConfig(planner_backend="curobo_v2_v080", max_attempts=5)
                ctx.phase = "close"
                ctx.deadline = lambda: None
                ctx.begin = lambda: None
                from lastmile_dataflow.runtime.outcomes.contacts import (
                    ContactStatistics,
                )

                ctx.contact_stats = ContactStatistics()
                ctx.record_physics = True
                ctx.state = lambda: {"time_s": 0.0}
                ctx.monitor = None
                ctx.collisions = []
                ctx.fingers = set()
                ctx.target_bodies = set()
                ctx.physics = io.BytesIO()
                ctx.record_video = False
                ctx.timings = {}
                ctx.recorder = SimpleNamespace(path=Path(directory), steps=0)
                data = SimpleNamespace(
                    time=0.0,
                    qpos=np.array([feedback]),
                    qvel=np.zeros(1),
                    qacc=np.zeros(1),
                    ctrl=np.zeros(1),
                    warning=[],
                )
                robot = SimpleNamespace(
                    config=SimpleNamespace(torso_limits=(0, 0.738), control_profile='position-v1'), group=lambda _: np.array([0, feedback])
                )
                submitted = []

                def step(action, **kw):
                    submitted.append((action.copy(), kw["command_raw"].copy()))
                    check = kw["substep_check"]()
                    return {"submitted_action": action.tolist()}, None if check["valid"] else check

                ctx.sim = SimpleNamespace(data=data, robot=robot, model=None, step=step)
                from lastmile_dataflow.runtime.outcomes.contacts import ContactFrame
                ctx.contact_sampler = SimpleNamespace(capture=lambda **kw: ContactFrame(ctx.sim, []))
                action = np.zeros(20)
                action[19] = 0.739327605117437
                with (
                    patch(
                        "lastmile_dataflow.runtime.execution.context.sanitize_action",
                        side_effect=lambda robot, a, **kw: (a, []),
                    ),
                    patch("lastmile_dataflow.runtime.execution.context.mujoco.mj_forward"),
                ):
                    if fails:
                        with self.assertRaisesRegex(OperationFailure, "torso_feedback_limit"):
                            ctx.tick(action)
                    else:
                        ctx.tick(action)
                self.assertEqual(submitted[0][0][19], 0.738)
                self.assertEqual(action[19], 0.739327605117437)
                self.assertEqual(submitted[0][1][19], action[19])
                self.assertEqual(ctx.last_legal_torso_target, 0.738)
                event = json.loads((Path(directory) / "torso_command_adjustments.jsonl").read_text())
                self.assertEqual(event["submitted_h"], 0.738)
                row = json.loads(ctx.physics.getvalue())
                self.assertEqual(row["torso_feedback_tolerance_rad"], 0.003)
                # the raw measurement is still recorded even when it does not stop.
                self.assertAlmostEqual(row["torso_limit_excess_rad"], max(0.0, feedback - 0.738))


class NavigateTorsoLoopTests(unittest.TestCase):
    def fake_ctx(self, hold_value):
        state = {"base": np.array([0.0, 0.0, 0.0])}
        ticks = []
        config = SimpleNamespace(
            max_navigation_steps=2000,
            navigation_speed_m_s=0.2,
            navigation_yaw_speed_rad_s=0.4,
            arrival_tolerance_m=0.015,
            arrival_tolerance_rad=0.025,
            planner_backend="curobo_v2_v080",
        )
        robot = SimpleNamespace(
            config=SimpleNamespace(control_hz=20.0, max_base_delta=0.1, max_yaw_delta=0.2),
            neutral_action=lambda: np.zeros(20),
        )

        def group(name):
            if name == "base":
                return state["base"].copy()
            if name == "torso":
                return np.array([0.0, 0.0, -0.0, 0.0, 0.0, 0.0])
            return np.zeros(7)

        robot.group = group
        robot.arms = ('left','right')
        robot.arm_positions = lambda arm: group(arm+'_arm')
        robot.base_pose = lambda: group('base')
        ctx = SimpleNamespace(
            sim=SimpleNamespace(robot=robot),
            navigation_contacts=NavigationContacts(),
            config=config,
            yaw_gap=lambda t, c: t - c,
            hold_torso_target=lambda: hold_value,
            phase="navigation",
        )

        def tick(a, **kw):
            action = np.zeros(20)
            action[:3] = np.asarray(a.base_xyyaw)-state['base']
            action[19] = hold_value
            ticks.append(action)
            state["base"] = np.asarray(a.base_xyyaw).copy()

        ctx.tick = tick
        return ctx, ticks

    def test_navigation_and_arrival_hold_the_last_legal_torso_target(self):
        ctx, ticks = self.fake_ctx(0.4717424850553562)
        path = {"poses": [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]}
        goal = {"base": [2.0, 0.0, 0.0]}
        result = navigate(ctx, path, goal)
        self.assertEqual(result["status"], "success")
        self.assertTrue(ticks)
        # neutral_action() echoes ctrl (0.0 here); navigate must override it with
        # the closed-loop hold, not leave the open-loop value.
        self.assertTrue(all(a[19] == 0.4717424850553562 for a in ticks), [a[19] for a in ticks])

    def test_navigation_tracks_explicit_intermediate_yaw(self):
        ctx, ticks = self.fake_ctx(.4)
        path = {'poses': [[0., 0., 0.], [.4, 0., .5], [.8, 0., 0.]]}
        result = navigate(ctx, path, {'base': [.8, 0., 0.]})
        self.assertEqual(result['status'], 'success')
        yaw = np.cumsum([tick[2] for tick in ticks])
        self.assertGreater(yaw.max(), .45)
        self.assertLess(abs(yaw[-1]), .025)
