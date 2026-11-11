"""Continuous control context and shared execution helpers."""

from functools import partial
import copy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import time
import mujoco
import numpy as np
from .lifecycle import track_resource
from ...utils.io import canonical, write_json
from ...scenes.geometry import tcp_pose
from ...planning.curobo.planner import PlannerUnavailable
from ..control.interface import RobotCommand
from ..execution_config import TORSO_ERROR_POLICY
from ..control.action import sanitize_action
from ..control.action import (
    bounded_torso_command,
    torso_feedback_margin,
    COMMAND_TOLERANCE_RAD,
    TORSO_SEVERE_FEEDBACK_RAD,
)
from ...scenes.geometry import descendants
from ..outcomes.task import mobile_constraint_failure, rotation_error, torso_tracking_error
from ..outcomes.contacts import (
    ContactSampler,
    ContactStatistics,
    NavigationContacts,
    simulation_failure,
)
from .navigation import base_step_limits
from .protocol import OperationFailure, SimulationState
from .checkpoint import checkpoint_pick_success, timed


def joint_base_error(target, base):
    """Base target minus measured base, without yaw wrapping.

    RBY-1 base yaw is a bounded joint; wrapping across ±π would command an
    illegal jump, so semantic tracking compares raw joint values."""
    return np.asarray(target, float) - base


def clone_sim(sim, *, target=None, handle=None):
    """New MjData, shared read-only model; never restore the running instance."""
    result = sim.derive(target=target, spec=None)
    result.handle_id = result.model.body(handle).id if handle else sim.handle_id
    result.planning_geometry = sim.planning_geometry
    track_resource(result)
    SimulationState.capture(sim).initialize(result)
    return result


class ContinuousContext:
    def __init__(self, sim, recorder, config, *, budget=None, max_plans=None, prepared_state=None):
        (self.sim, self.recorder, self.config) = (sim, recorder, config)
        self.budget = budget
        # Split calibration plans from the prepared station state; remote planning swaps the factory.
        self.prepared_state = prepared_state
        self.planner_factory = None
        self.stop = None
        self.phase = "initialize"
        self.started_at = time.monotonic()
        self.initial_sim_time = float(sim.data.time)
        self.timings = {}
        self.pick_initial = None
        self.active_control = None
        self.record_video = recorder.collection["record_video"]
        self.contact_stats = ContactStatistics()
        self.contact_sampler = ContactSampler(sim)
        self.navigation_contacts = NavigationContacts()
        self.samples = []
        self.collisions = self.contact_stats.examples
        self.plans = []
        self.monitor = None
        self.fingers = set()
        self.target_bodies = descendants(sim.model, sim.target_id)
        self.plan_count = 0 if budget is None else budget.plan_count
        self.plan_serial = self.plan_count
        self.analysis = None
        self.initial_target_height = float(sim.data.xpos[sim.target_id, 2])
        self.max_plans = config.max_operation_plans if max_plans is None else max_plans
        self.last_legal_torso_target = None
        self.open_joint = None
        self.manip_base_target = None

    def deadline(self):
        if self.stop is not None and self.stop.is_set():
            raise PlannerUnavailable("shared planning failure stopped the batch")
        if self.budget is not None:
            self.budget.check()
            return
        if time.monotonic() - self.started_at > self.config.attempt_timeout_s:
            raise TimeoutError("attempt_wall_clock_budget")

    def begin(self):
        if self.sim.started:
            return
        self.deadline()
        self.sim.begin()
        if self.record_video:
            with timed(self, "rendering"):
                frames = self.sim.render(self.config.width, self.config.height)
            if self.sim.third_person_camera is not None:
                write_json(
                    self.recorder.path / "third_person_initial.json",
                    self.sim.third_person_camera.metadata(),
                )
            self.last_video_frames = self.video_frames(
                frames, float(self.sim.data.time)
            )

    def hold_torso_target(self):
        """Keep the last submitted target, not a physically overshooting qpos."""
        target = self.last_legal_torso_target
        if target is None:
            target = self.sim.robot.control_height()
        return target

    def _check_tick(self, failures, semantic):
        health = simulation_failure(self.sim)
        if health:
            failures.append(health)
            return health
        mujoco.mj_forward(self.sim.model, self.sim.data)
        contacts = self.contact_sampler.capture(
            target_bodies=self.target_bodies, fingers=self.fingers
        )
        self.contact_stats.update_frame(contacts)
        if self.phase in ("navigation", "arrival"):
            if self.navigation_contacts.observe(
                self.sim, self.contact_sampler, self.config.navigation_max_penetration_m
            ):
                failures.append("navigation_severe_collision")
        if not semantic:
            h_actual = float(self.sim.robot.group("torso")[1])
            margin = torso_feedback_margin(h_actual, self.sim.robot.config.torso_limits)
            if margin > TORSO_SEVERE_FEEDBACK_RAD:
                failures.append("torso_feedback_limit")
        if self.monitor:
            self.monitor.phase = self.phase
            self.monitor.command_h = self.sim.robot.control_height()
            facts = self.monitor.sample(contacts)
            if "torso" in facts:
                facts.update(
                    torso_tracking_error_rad=torso_tracking_error(facts),
                    torso_error_policy=TORSO_ERROR_POLICY,
                )
            self.samples.append(facts)
            fail = mobile_constraint_failure(facts, self.monitor.initial)
            if fail:
                failures.append(fail)
            if self.open_joint is not None:
                facts["joint_value"] = float(self.sim.data.qpos[self.open_joint])
        return failures[0] if failures else None

    def tick(self, action, *, fixed_base=False):
        if isinstance(action, RobotCommand):
            action = self.sim.robot.execution_action(action)
        semantic = (
            isinstance(action, RobotCommand)
            or self.sim.robot.config.control_profile == "robocasa-v1"
        )
        raw_action = (
            copy.deepcopy(action)
            if semantic
            else np.asarray(action, dtype=float).copy()
        )
        self.recorder.phase = self.phase
        action = raw_action
        if not semantic:
            action = raw_action.copy()
            action[19] = bounded_torso_command(
                action[19], self.sim.robot.config.torso_limits
            )
            if action[19] != raw_action[19]:
                event = {
                    "phase": self.phase,
                    "time_s": float(self.sim.data.time),
                    "input_h": float(raw_action[19]),
                    "submitted_h": float(action[19]),
                    "command_tolerance_rad": COMMAND_TOLERANCE_RAD,
                }
                with (self.recorder.path / "torso_command_adjustments.jsonl").open(
                    "ab"
                ) as stream:
                    stream.write(canonical(event) + b"\n")
            (action, adjustments) = sanitize_action(
                self.sim.robot, action, fixed_base=fixed_base
            )
            for adjustment in adjustments:
                adjustment.update(phase=self.phase, time_s=float(self.sim.data.time))
                with (self.recorder.path / "command_limit_adjustments.jsonl").open(
                    "ab"
                ) as stream:
                    stream.write(canonical(adjustment) + b"\n")
        self.deadline()
        self.begin()
        failures = []
        with timed(self, "simulation_and_physics_evidence"):
            (command, stop) = self.sim.step(
                action,
                fixed_base=fixed_base,
                substep_check=partial(self._check_tick, failures, semantic),
                command_raw=raw_action,
            )
        self.last_legal_torso_target = (
            self.sim.robot.control_height() if semantic else float(action[19])
        )
        after = float(self.sim.data.time)
        if self.record_video:
            with timed(self, "rendering"):
                frames = self.sim.render(self.config.width, self.config.height)
            with timed(self, "recording"):
                if self.recorder.steps == 1:
                    self.recorder.video_frame(self.last_video_frames)
                self.recorder.video_frame(self.video_frames(frames, after))
        if failures:
            raise OperationFailure(failures[0])
        checkpoint_pick_success(self)

    def video_frames(self, frames, time_s):
        if self.analysis is None or "third_person_camera" not in frames:
            return frames
        result = dict(frames)
        facts = self.samples[-1] if self.monitor is not None and self.samples else None
        result["third_person_camera"] = self.analysis.frame(
            frames,
            phase=self.phase,
            time_s=time_s,
            base_xy=self.sim.robot.base_pose()[:2],
            target_height=float(self.sim.data.xpos[self.sim.target_id, 2]),
            initial_height=self.initial_target_height,
            facts=facts,
            side=self.monitor.side if self.monitor is not None else None,
            collisions=self.contact_stats.forbidden_contact_count,
        )
        return result

    def arm_tick(self, side, q, grip, h):
        base = self.manip_base_target
        command = self.sim.robot.manipulation_command(side, q, grip, h, base)
        self.tick(command)

    def mobile_tick(self, planner, point, side, grip):
        """Synchronized semantic targets from the model's named trajectory layout."""
        point = np.asarray(point)
        robot = self.sim.robot
        target = planner.base_target_world(point)
        base = robot.base_pose()
        base_gap = joint_base_error(target, base)
        q = planner.model.arm_target(point)
        arm = robot.arm_positions(side)
        arm_gap = q - arm
        height = planner.model.height_target(point)
        measured_h = robot.control_height()
        h_gap = height - measured_h
        limit, yaw_limit = base_step_limits(
            robot.config, self.config.navigation_speed_m_s, self.config.navigation_yaw_speed_rad_s
        )
        scale = max(
            1.0,
            np.linalg.norm(base_gap[:2]) / limit,
            abs(base_gap[2]) / yaw_limit,
            np.max(np.abs(arm_gap)) / (robot.config.max_arm_delta * 0.9),
        )
        if planner.h_step_limit:
            scale = max(scale, abs(h_gap) / planner.h_step_limit)
        command = robot.manipulation_command(
            side,
            arm + arm_gap / scale,
            grip,
            measured_h + h_gap / scale,
            base + base_gap / scale,
        )
        self.manip_base_target = target
        self.tick(command)
        return scale

    def plan(self, planner, goal):
        self.deadline()
        if self.plan_count >= self.max_plans:
            raise OperationFailure("planning_budget_no_solution")
        self.plan_count += 1
        with timed(self, "motion_planning"):
            result = planner.plan(goal)
        self.plans.append(
            {"phase": self.phase, "goal_world": goal.tolist(), **asdict(result)}
        )
        write_json(self.recorder.path / "planning.json", self.plans)
        self.deadline()
        if result.status != "success":
            diagnostic = dict(result.diagnostics)
            raise OperationFailure(
                diagnostic.get("failure_reason")
                or (
                    "fov_constraint_failed"
                    if result.status == "fov_constraint_failed"
                    else "planning_no_solution"
                ),
                diagnostic,
            )
        return result

    def follow(self, planner, goal, side, grip):
        result = self.plan(planner, goal)
        self.follow_points(planner, result.positions, side, grip, goal)

    def follow_points(self, planner, positions, side, grip, goal=None):
        if not positions:
            raise OperationFailure("empty_trajectory")
        if np.asarray(positions).shape[1:] != (len(planner.names),):
            raise ValueError("planner joint ordering mismatch")
        if self.sim.robot.config.control_profile == "robocasa-v1":
            self._follow_robocasa(planner, positions, side, grip)
            self._check_tcp(side, goal)
            return
        for point in positions:
            for _ in range(120):
                if self.mobile_tick(planner, point, side, grip) <= 1.0:
                    break
            else:
                raise OperationFailure("arm_tracking_timeout")
        goal_q = np.asarray(positions[-1])
        for _ in range(100):
            base_gap = joint_base_error(planner.base_target_world(goal_q), self.sim.robot.group("base"))
            if (
                np.max(
                    np.abs(
                        self.sim.robot.arm_positions(side)
                        - planner.model.arm_target(goal_q)
                    )
                )
                < 0.01
                and np.linalg.norm(base_gap[:2]) < self.config.arrival_tolerance_m
                and (abs(base_gap[2]) < self.config.arrival_tolerance_rad)
            ):
                break
            self.mobile_tick(planner, goal_q, side, grip)
        else:
            raise OperationFailure("mobile_base_arm_tracking_timeout")
        self._check_tcp(side, goal)

    def _check_tcp(self, side, goal):
        if goal is None:
            return
        measured = tcp_pose(self.sim, side)
        if (np.linalg.norm(measured[:3, 3] - goal[:3, 3]) > 0.015
                or rotation_error(measured[:3, :3], goal[:3, :3]) > 0.06):
            raise OperationFailure("tcp_tracking_timeout")

    def _follow_robocasa(self, planner, positions, side, grip):
        """RoboCasa OSC: hold each reference until TCP and base reach it."""
        robot = self.sim.robot
        for point in positions:
            point = np.asarray(point)
            base = planner.base_target_world(point)
            self.manip_base_target = np.asarray(base).copy()
            command = RobotCommand(base_xyyaw=base, arm_q={side: planner.model.arm_target(point)},
                                   gripper_width={side: grip})
            robot.execution_action(command)
            site = robot.tcp_site(side)
            target_xyz = robot.scratch.site_xpos[site].copy()
            target_rotation = robot.scratch.site_xmat[site].reshape(3, 3).copy()
            for _ in range(120):
                self.tick(command)
                measured = tcp_pose(self.sim, side)
                gap = joint_base_error(base, robot.base_pose())
                if (np.linalg.norm(target_xyz - measured[:3, 3]) <= 0.01
                        and rotation_error(measured[:3, :3], target_rotation) <= 0.06
                        and np.linalg.norm(gap[:2]) <= self.config.arrival_tolerance_m
                        and abs(gap[2]) <= self.config.arrival_tolerance_rad):
                    break
            else:
                raise OperationFailure("robocasa_reference_tracking_timeout")

    def finish(self):
        if self.plans:
            write_json(self.recorder.path / "planning.json", self.plans)


def planner_options(config, assets_dir, seed):
    return SimpleNamespace(
        **{
            **asdict(config),
            "seed": seed,
            "max_candidates": config.max_grasp_candidates,
            "robot_planner_dir": str(Path(assets_dir) / "robots/rby1m/curobo_config"),
        }
    )
