"""独立试验失败隔离、资源关闭与规划重试目录。"""

import errno
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.policies.molmobot import (
    MolmoBotClient,
    PolicyEpisodeError,
    PolicyInfrastructureError,
)
from lastmile_dataflow.runtime.execution.context import OperationFailure
from lastmile_dataflow.runtime.execution.lifecycle import (
    classify_episode_exception,
    execute_isolated_episode,
    mark_episode_phase,
    track_resource,
)
from lastmile_dataflow.runtime.execution.manipulation import manipulate_v2
from lastmile_dataflow.runtime.execution_config import ExecutionConfig


class LifecycleIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = {"operation": "pick", "task_id": "t"}
        self.station = {"station_id": "S0001", "xy": [0, 0], "geometry": "valid"}

    def execute(self, fn, attempt="test"):
        return execute_isolated_episode(fn, root=self.root, station=self.station, attempt_id=attempt)

    def test_phase_errors_are_local_and_resources_are_closed(self):
        for phase, status in [
            ("initialize", "initialization_error"),
            ("execution", "episode_error"),
            ("finalize", "recording_error"),
        ]:
            resource = io.StringIO()

            def run():
                track_resource(resource)
                mark_episode_phase(phase)
                raise ValueError("injected " + phase)

            result = self.execute(run, phase)
            self.assertEqual(result["status"], status)
            self.assertTrue(resource.closed)
            self.assertFalse(result["artifact_complete"])
            self.assertIsNone(result["executed_steps"])
            self.assertIn("injected", result["error"])
            self.assertFalse(Path(result["attempt"]).exists())

    def test_generic_runtime_error_local_but_gpu_rpc_storage_are_shared(self):
        for exc in [ValueError("bad station"), KeyError("target"), RuntimeError("bad episode geometry")]:
            self.assertEqual(classify_episode_exception(exc)["failure_scope"], "episode")
        for exc in [
            PolicyInfrastructureError("model response timeout"),
            RuntimeError("CUDA out of memory"),
            MemoryError("allocation failed"),
            OSError(errno.ENOSPC, "disk full"),
            OSError(errno.EIO, "I/O failed"),
        ]:
            self.assertEqual(classify_episode_exception(exc)["failure_scope"], "shared")

    def test_model_reply_consumed_data_error_local_connection_error_shared(self):
        client = MolmoBotClient.__new__(MolmoBotClient)
        client.connection = Mock()
        client.close = Mock()
        client.connection.poll.return_value = True
        for kind in ["ValueError", "RuntimeError", "KeyError"]:
            client.connection.recv.return_value = {"error": "bad observation", "type": kind}
            with self.assertRaises(PolicyEpisodeError):
                client.receive(1)
        client.connection.recv.return_value = {"error": "CUDA out of memory", "type": "RuntimeError"}
        with self.assertRaises(PolicyInfrastructureError):
            client.receive(1)
        client.connection.poll.return_value = False
        with self.assertRaises(PolicyInfrastructureError):
            client.receive(1)
        client.close.assert_called_once()

    def test_interruption_not_swallowed_and_resources_closed(self):
        resource = io.StringIO()

        def run():
            track_resource(resource)
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.execute(run)
        self.assertTrue(resource.closed)

    def test_real_synthetic_mjdata_initialization_failure_closes_clone_not_baseline(self):
        from lastmile_dataflow.runtime.execution.context import clone_sim
        from lastmile_dataflow.runtime.execution.curobo import run_curobo_attempt
        from lastmile_dataflow.runtime.execution.molmobot import run_vla_attempt
        from tests.helpers import make_sim

        baseline, robot = make_sim(self.root)
        self.addCleanup(baseline.close)
        before = baseline.state_vector().copy()
        clones = []

        def clone(*args, **kwargs):
            probe = clone_sim(*args, **kwargs)
            clones.append(probe)
            return probe

        task = {**self.task, "target_body": "target"}
        for backend in ("curobo", "molmobot"):
            module = "lastmile_dataflow.runtime." + (
                "execution.curobo" if backend == "curobo" else "execution.molmobot"
            )
            with (
                patch(module + ".clone_sim", side_effect=clone),
                patch(module + ".place_frozen_station", side_effect=ValueError("bad initial pose")),
            ):
                if backend == "curobo":
                    result = run_curobo_attempt(
                        baseline,
                        task,
                        self.station,
                        [],
                        self.root,
                        ExecutionConfig(),
                        CollectionConfig(output_dir=str(self.root)),
                        root=self.root,
                        attempt_id=backend,
                        seed=1,
                    )
                else:
                    result = run_vla_attempt(
                        baseline,
                        task,
                        self.station,
                        ExecutionConfig(),
                        CollectionConfig(output_dir=str(self.root)),
                        root=self.root,
                        attempt_id=backend,
                        seed=1,
                        policy=object(),
                    )
            self.assertEqual(result["status"], "initialization_error")
            self.assertTrue(clones[-1].closed)
            self.assertFalse(baseline.closed)
            np.testing.assert_array_equal(before, baseline.state_vector())

    def test_mid_execution_recorder_failure_classifies_as_recording_error(self):
        def run():
            recorder = AttemptRecorder(
                self.root, {"collection": {"record_video": False}}, attempt_id="bad-recording"
            )
            mark_episode_phase("execution")
            recorder.event("bad-event", bad=object())

        result = self.execute(run, "bad-recording")
        self.assertEqual(result["status"], "recording_error")
        self.assertEqual(result["attribution"]["phase"], "recording")
        self.assertFalse(result["execution_started"])

    def test_poll_connection_failure_is_shared(self):
        client = MolmoBotClient.__new__(MolmoBotClient)
        client.connection = Mock()
        client.connection.poll.side_effect = OSError(errno.EPIPE, "closed channel")
        with self.assertRaises(PolicyInfrastructureError):
            client.receive(1)


class RetryDirectoryTests(unittest.TestCase):
    def context(self, path):
        return SimpleNamespace(
            timings={},
            recorder=SimpleNamespace(path=path),
            config=ExecutionConfig(),
            sim=SimpleNamespace(model=Mock(), robot=Mock(control_height=Mock(return_value=0.), open_grip=-.05, semantic_control=False, plan_lift_before_close=True, lock_base_after_close=False)),
            plan_count=0,
            plan_serial=0,
            max_plans=10,
            plans=[],
            deadline=Mock(),
            begin=Mock(),
            follow_points=Mock(),
            follow=Mock(),
            arm_tick=Mock(),
            hold_torso_target=Mock(return_value=0.0),
        )

    def test_attempt_retry_never_overwrites_earlier_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d) / "attempt")
            planner = Mock()

            def grasp(goals, query, **kwargs):
                query("goalset")
                return (False, 0, {}, {"reason": "no_solution"})

            planner.grasp.side_effect = grasp
            candidate = {"body": "target", "pose_local": np.eye(4), "row": 0, "source": "fixture"}
            with (
                patch(
                    "lastmile_dataflow.runtime.execution.manipulation.candidate_pool",
                    return_value=[candidate],
                ),
                patch("lastmile_dataflow.runtime.execution.manipulation.body_pose", return_value=np.eye(4)),
                patch("lastmile_dataflow.runtime.execution.manipulation.V2Planner", return_value=planner),
            ):
                for _ in range(2):
                    ctx.plan_count = 0
                    with self.assertRaisesRegex(OperationFailure, "planning_no_solution"):
                        manipulate_v2(
                            ctx,
                            {"operation": "pick", "anchor_world": [0, 0, 0]},
                            [],
                            d,
                            0,
                            forced={"arm": "left"},
                        )
            self.assertTrue((ctx.recorder.path / "v080-0-left").is_dir())
            self.assertTrue((ctx.recorder.path / "v080-1-left").is_dir())
            self.assertEqual(planner.grasp.call_count, 2)

    def test_closed_directory_can_be_reused(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d))
            ctx.plan_serial = 1
            closed = Path(d) / "v080-closed-1-left"
            closed.mkdir()
            marker = closed / "previous.json"
            marker.write_text("{}")
            planner = Mock()
            planner.grasp.return_value = (True, 0, {"pregrasp": [], "approach": []}, {})
            candidate = {"body": "target", "pose_local": np.eye(4), "row": 0, "source": "fixture"}
            ctx.sim.robot.group.return_value = np.zeros(7)
            monitor = SimpleNamespace(fingers=[], allowed_target_fingers=set(), initial={})
            with (
                patch(
                    "lastmile_dataflow.runtime.execution.manipulation.candidate_pool",
                    return_value=[candidate],
                ),
                patch("lastmile_dataflow.runtime.execution.manipulation.body_pose", return_value=np.eye(4)),
                patch("lastmile_dataflow.runtime.execution.manipulation.PickMonitor", return_value=monitor),
                patch(
                    "lastmile_dataflow.runtime.execution.manipulation.V2Planner",
                    side_effect=[planner, RuntimeError("reached closed planner")],
                ) as factory,
            ):
                with self.assertRaisesRegex(RuntimeError, "reached closed planner"):
                    manipulate_v2(
                        ctx,
                        {"operation": "pick", "anchor_world": [0, 0, 0]},
                        [],
                        d,
                        0,
                        forced={"arm": "left"},
                    )
            self.assertEqual(factory.call_args.args[3], closed)
            self.assertTrue(marker.exists())

    def test_open_world_directory_can_be_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "attempt"
            ctx = self.context(path)
            ctx.plan_serial = 1
            ctx.open_joint = 0
            ctx.arm_tick = Mock(
                side_effect=lambda *a, **k: ctx.samples.append({"finger_forces_n": [0.0, 0.0]})
            )
            ctx.sim = SimpleNamespace(
                model=Mock(),
                robot=SimpleNamespace(
                    config=SimpleNamespace(control_hz=20), group=Mock(return_value=np.zeros(7)),
                    arm_positions=Mock(return_value=np.zeros(7)), control_height=lambda:0., open_grip=-.05, semantic_control=False, plan_lift_before_close=True, lock_base_after_close=False
                ),
                data=SimpleNamespace(qpos=np.zeros(1), xaxis=np.zeros((1, 3)), xanchor=np.zeros(3)),
            )
            ctx.sim.model.jnt_qposadr = np.zeros(1, dtype=int)
            ctx.sim.model.joint = Mock(return_value=SimpleNamespace(id=0))
            closed = path / "v080-closed-1-left"
            world = closed / "world-0"
            world.mkdir(parents=True)
            marker = world / "previous.json"
            marker.write_text("{}")
            planner = Mock()
            planner.grasp.return_value = (True, 0, {"pregrasp": [], "approach": []}, {})
            candidate = {"body": "target", "pose_local": np.eye(4), "row": 0, "source": "fixture"}
            monitor = SimpleNamespace(fingers=[], allowed_target_fingers=set(), initial={})
            task = {
                "operation": "open",
                "anchor_world": [0, 0, 0],
                "joint_name": "door",
                "joint_kind": "slide",
                "joint_initial": 0.0,
                "joint_goal": 0.1,
            }
            with (
                patch(
                    "lastmile_dataflow.runtime.execution.manipulation.candidate_pool",
                    return_value=[candidate],
                ),
                patch("lastmile_dataflow.runtime.execution.manipulation.body_pose", return_value=np.eye(4)),
                patch("lastmile_dataflow.runtime.execution.manipulation.PickMonitor", return_value=monitor),
                patch("lastmile_dataflow.runtime.execution.manipulation.tcp_pose", return_value=np.eye(4)),
                patch(
                    "lastmile_dataflow.runtime.execution.manipulation.evaluate_open",
                    return_value={"status": "success", "reason": "open"},
                ),
                patch(
                    "lastmile_dataflow.runtime.execution.manipulation.V2Planner",
                    side_effect=[planner, Mock()],
                ),
            ):
                verdict, control = manipulate_v2(ctx, task, [], d, 0, forced={"arm": "left"})
            self.assertEqual(verdict["status"], "success")
            self.assertTrue(marker.exists())
