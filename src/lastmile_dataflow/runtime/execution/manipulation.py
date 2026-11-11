"""Native grasp planning and semantic physical execution; no scene resets."""

import copy
from pathlib import Path
import numpy as np
from ...utils.io import write_json
from ...scenes.geometry import body_pose, tcp_pose
from ...planning.curobo.planner import V2Planner
from ...planning.waypoints import GRASP_APPROACH_DISTANCE_M
from ..outcomes.pick import PickMonitor
from ..outcomes.evaluation import evaluate_open
from ..outcomes.pick import hold_pick, timed
from dataclasses import replace
from functools import partial
from .context import planner_options
from .protocol import OperationFailure


def _candidate_score(sim, reference, method, candidate):
    goal = (
        body_pose(sim, sim.model.body(candidate["body"]).id) @ candidate["pose_local"]
    )
    if method == "tcp_distance":
        return float(np.linalg.norm(goal[:3, 3] - reference))
    vector = goal[:3, 3] - np.r_[reference[:2], goal[2, 3]]
    return float(np.dot(goal[:3, 2], vector) / max(np.linalg.norm(vector), 1e-9))


def candidate_pool(ctx, candidates, seed, forced):
    rng = np.random.default_rng(seed)
    if forced:
        pool = [
            c
            for c in candidates
            if c["row"] == forced["grasp_row"] and c["source"] == forced["grasp_source"]
        ]
        if not pool:
            raise ValueError("missing winning grasp")
        return pool[: ctx.config.max_grasp_candidates]
    method = ctx.config.grasp_candidate_order
    reference = (
        tcp_pose(ctx.sim, ctx.sim.robot.arms[0])[:3, 3]
        if method == "tcp_distance"
        else ctx.sim.robot.base_pose()
    )
    score = partial(_candidate_score, ctx.sim, reference, method)
    if method == "tcp_distance":
        return sorted(candidates, key=score)[: ctx.config.max_grasp_candidates]
    pool = sorted(candidates, key=score, reverse=True)[
        : max(ctx.config.max_grasp_candidates * 8, 16)
    ]
    return [
        pool[i] for i in rng.permutation(len(pool))[: ctx.config.max_grasp_candidates]
    ]


def grasp_goals(sim, config, pool, *, retreat=False):
    """World grasp goals; open retreats along the approach, pick pushes in."""
    goals = [
        body_pose(sim, sim.model.body(c["body"]).id) @ c["pose_local"] for c in pool
    ]
    angle = config.grasp_roll_rad
    roll = np.array(
        [
            [np.cos(angle), -np.sin(angle), 0.0],
            [np.sin(angle), np.cos(angle), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    offset = -config.approach_offset_m if retreat else config.approach_offset_m
    for goal in goals:
        goal[:3, 3] += goal[:3, 2] * offset
        goal[:3, :3] = goal[:3, :3] @ roll
    return goals


class _Manipulation:
    def __init__(self, ctx, task, candidates, assets_dir, seed, forced=None):
        self.ctx = ctx
        self.task = task
        self.seed = seed
        self.assets_dir = assets_dir
        self.forced = forced
        self.candidates = candidates
        self.pool = candidate_pool(self.ctx, self.candidates, self.seed, self.forced)
        self.sides = (
            [self.forced["arm"]]
            if self.forced
            else list(self.ctx.sim.robot.arms)
            if self.seed % 2 == 0
            else list(reversed(self.ctx.sim.robot.arms))
        )
        self.sim = self.ctx.sim
        self.options = planner_options(self.ctx.config, self.assets_dir, self.seed)
        self.planner = None
        self.factory = getattr(self.ctx, "planner_factory", V2Planner)

    def close(self):
        if self.planner is not None:
            self.planner.detach()
            self.planner.close()
            self.planner = None

    def _query(self, phase):
        self.ctx.deadline()
        if self.ctx.plan_count >= self.ctx.max_plans:
            raise OperationFailure("planning_budget_no_solution")
        self.ctx.plan_count += 1
        self.ctx.plan_serial += 1
        self.ctx.phase = "plan_" + phase
        self.ctx.plans.append(
            {
                "phase": self.ctx.phase,
                "query_index": self.ctx.plan_count,
                "plan_serial": self.ctx.plan_serial,
                "planner": "curobo_v2_v080",
                "native_max_attempts": 5,
            }
        )
        write_json(self.ctx.recorder.path / "planning.json", self.ctx.plans)

    def _plan(self):
        self.diagnostic = None
        self.operation_state = None
        for self.side in self.sides:
            self.ctx.deadline()
            self.directory = (
                self.ctx.recorder.path / f"v080-{self.ctx.plan_serial}-{self.side}"
            )
            self.directory.mkdir(parents=True, exist_ok=True)
            with timed(self.ctx, "planner_setup"):
                self.planner = self.factory(
                    self.sim,
                    self.options,
                    self.side,
                    self.directory,
                    workspace=(self.task["anchor_world"], self.ctx.config.radius_m),
                )
            self.goals = grasp_goals(
                self.sim,
                self.ctx.config,
                self.pool,
                retreat=self.task["operation"] == "open",
            )
            write_json(
                self.directory / "grasp_candidates.json",
                {
                    "lanes": [
                        {"seed": self.seed, "arm": self.side, "candidates": self.pool}
                    ]
                },
            )
            if not self.goals:
                raise OperationFailure("no_grasp_candidates")
            if self.operation_state is None:
                if self.ctx.budget is not None:
                    self.operation_state = (
                        Path(self.ctx.recorder.config["preparation"]["path"])
                        / "state.npz"
                    )
                else:
                    self.operation_state = (
                        self.ctx.recorder.path
                        / f"operation-state-{self.ctx.plan_serial}.npz"
                    )
                    self.sim.save_snapshot(self.operation_state)
            if self.task["operation"] == "pick":
                with timed(self.ctx, "grasp_planning"):
                    (success, self.index, self.stages, self.diagnostic) = (
                        self.planner.grasp(
                            self.goals,
                            self._query,
                            lift=self.sim.robot.plan_lift_before_close,
                        )
                    )
            else:
                with timed(self.ctx, "grasp_planning"):
                    (success, self.index, self.stages, self.diagnostic) = (
                        self.planner.grasp(self.goals, self._query, lift=False)
                    )
            self.ctx.deadline()
            self.ctx.plans.append(
                {
                    "phase": "native_grasp_result",
                    "planning_state_ref": str(self.operation_state.resolve()),
                    **self.diagnostic,
                }
            )
            if success:
                break
            self.close()
        else:
            raise OperationFailure(
                (self.diagnostic or {}).get("failure_reason") or "planning_no_solution",
                self.diagnostic,
            )

    def _initialize_monitor(self):
        self.c = self.pool[self.index]
        self.goal = self.goals[self.index]
        self.pre = self.goal.copy()
        self.pre[:3, 3] -= self.goal[:3, 2] * GRASP_APPROACH_DISTANCE_M
        self.ctx.monitor = PickMonitor(self.sim, self.side)
        self.ctx.fingers = self.ctx.monitor.allowed_target_fingers
        self.ctx.samples = []
        self.ctx.monitor.initial.update(
            workspace_center_xy=self.task["anchor_world"][:2],
            workspace_radius_m=self.ctx.config.radius_m,
        )
        initial = copy.deepcopy(self.ctx.monitor.initial)
        self.ctx.pick_initial = initial if self.task["operation"] == "pick" else None
        self.ctx.active_control = {
            "arm": self.side,
            "height_target": self.sim.robot.control_height(),
            "grasp_row": self.c["row"],
            "grasp_source": self.c["source"],
            "seed": self.seed,
        }
        write_json(
            self.ctx.recorder.path / "task_evidence_initial.json",
            {
                "operation": self.task["operation"],
                "initial": initial,
                "control": self.ctx.active_control,
            },
        )
        self.ctx.manip_base_target = self.sim.robot.group("base").copy()
        if self.task["operation"] == "open":
            self.j = self.sim.model.joint(self.task["joint_name"]).id
            self.ctx.open_joint = int(self.sim.model.jnt_qposadr[self.j])

    def _approach_and_close(self):
        self.ctx.begin()
        self.ctx.phase = "pregrasp"
        self.ctx.follow_points(
            self.planner,
            self.stages["pregrasp"],
            self.side,
            self.sim.robot.open_grip,
            self.pre,
        )
        self.ctx.phase = "approach"
        self.ctx.follow_points(
            self.planner,
            self.stages["approach"],
            self.side,
            self.sim.robot.open_grip,
            self.goal,
        )
        self.ctx.phase = "close"
        q = self.sim.robot.arm_positions(self.side).copy()
        self.h = self.ctx.hold_torso_target()
        for _ in range(20):
            self.ctx.arm_tick(self.side, q, 0.0, self.h)

    def _closed_planner(self):
        self.close()
        self.directory = (
            self.ctx.recorder.path / f"v080-closed-{self.ctx.plan_serial}-{self.side}"
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        with timed(self.ctx, "planner_setup"):
            closed_options = (
                replace(self.ctx.config, operation_base_mode="fixed")
                if self.sim.robot.lock_base_after_close
                else self.ctx.config
            )
            closed_options = planner_options(closed_options, self.assets_dir, self.seed)
            self.planner = self.factory(
                self.sim,
                closed_options,
                self.side,
                self.directory,
                workspace=(self.task["anchor_world"], self.ctx.config.radius_m),
            )

    def _lift_and_hold(self):
        write_json(
            self.ctx.recorder.path / "attached_object.json",
            self.planner.attach_target_geometry(),
        )
        self.ctx.phase = "lift_actual"
        lift = tcp_pose(self.sim, self.side).copy()
        lift[2, 3] += 0.1
        self.ctx.follow(self.planner, lift, self.side, 0.0)
        self.ctx.phase = "hold"
        q = self.sim.robot.arm_positions(self.side).copy()
        self.h = self.ctx.hold_torso_target()
        self.verdict = hold_pick(self.ctx, self.side, q, self.h)

    def _open_target(self, delta):
        tcp = tcp_pose(self.sim, self.side).copy()
        axis = self.sim.data.xaxis[self.j].copy()
        if self.task["joint_kind"] == "slide":
            tcp[:3, 3] += axis * delta
        else:
            skew = np.array(
                [
                    [0, -axis[2], axis[1]],
                    [axis[2], 0, -axis[0]],
                    [-axis[1], axis[0], 0],
                ]
            )
            rotation = (
                np.eye(3) + np.sin(delta) * skew + (1 - np.cos(delta)) * (skew @ skew)
            )
            origin = self.sim.data.xanchor[self.j]
            tcp[:3, 3] = origin + rotation @ (tcp[:3, 3] - origin)
            tcp[:3, :3] = rotation @ tcp[:3, :3]
        return tcp

    def _open_and_hold(self):
        if not self.ctx.samples or len(self.ctx.samples[-1]["finger_forces_n"]) != 2:
            raise OperationFailure("open_no_grip")
        self.planner.single_phase = "approach_contact"
        step = (
            self.ctx.config.open_waypoint_step_m
            if self.task["joint_kind"] == "slide"
            else self.ctx.config.open_waypoint_step_rad
        )
        count = (
            int(abs(self.task["joint_goal"] - self.task["joint_initial"]) / step) * 3
            + 12
        )
        direction = np.sign(self.task["joint_goal"] - self.task["joint_initial"])
        for index in range(count):
            remaining = direction * (
                self.task["joint_goal"] - float(self.sim.data.qpos[self.ctx.open_joint])
            )
            if remaining <= 0.001:
                break
            delta = direction * min(step, remaining + 0.002)
            tcp = self._open_target(delta)
            world = self.directory / f"world-{index}"
            world.mkdir(parents=True, exist_ok=True)
            self.planner.refresh_world(world)
            self.ctx.phase = "open"
            self.ctx.follow(self.planner, tcp, self.side, 0.0)
            self.h = self.ctx.hold_torso_target()
            if len(self.ctx.samples[-1]["finger_forces_n"]) != 2:
                raise OperationFailure("open_contact_lost")
        self.ctx.phase = "open_hold"
        q = self.sim.robot.arm_positions(self.side).copy()
        for _ in range(
            int(
                np.ceil(
                    (self.ctx.config.open_hold_s + 0.1)
                    * self.sim.robot.config.control_hz
                )
            )
        ):
            self.ctx.arm_tick(self.side, q, 0.0, self.h)
        self.verdict = evaluate_open(
            self.task, self.ctx.samples, self.ctx.config.open_hold_s
        )

    def run(self):
        try:
            self._plan()
            self._initialize_monitor()
            self._approach_and_close()
            self._closed_planner()
            if self.task["operation"] == "pick":
                self._lift_and_hold()
            else:
                self._open_and_hold()
            control = {
                "arm": self.side,
                "height_target": self.sim.robot.control_height(),
                "grasp_row": self.c["row"],
                "grasp_source": self.c["source"],
                "seed": self.seed,
            }
            write_json(
                self.ctx.recorder.path / f"verdict-{len(self.ctx.plans)}.json",
                self.verdict,
            )
            return (self.verdict, control)
        finally:
            self.close()


def manipulate(ctx, task, candidates, assets_dir, seed, forced=None):
    return _Manipulation(ctx, task, candidates, assets_dir, seed, forced).run()
