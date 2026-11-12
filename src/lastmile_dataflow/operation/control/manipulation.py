"""Native grasp planning and semantic physical execution; no scene resets."""

from ..grasps import select_grasps

import copy
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

import numpy as np

from lastmile_dataflow.planning.curobo.planner import V2Planner
from lastmile_dataflow.planning.result import GRASP_APPROACH_DISTANCE_M
from lastmile_dataflow.sim.model.geometry import body_pose, tcp_pose
from lastmile_dataflow.utils.io import write_json
from ..config import PLANNER_BACKEND
from lastmile_dataflow.planning.curobo.tracing import NATIVE_MAX_ATTEMPTS
from ..outcomes.task import PickMonitor, evaluate_open
from .checkpoint import hold_pick, timed
from .context import planner_options
from lastmile_dataflow.sim.simulation import OperationFailure


def _candidate_score(sim, reference, method, candidate):
    goal = body_pose(sim, sim.model.body(candidate["body"]).id) @ candidate["pose_local"]
    if method == "tcp_distance":
        return float(np.linalg.norm(goal[:3, 3] - reference))
    vector = goal[:3, 3] - np.r_[reference[:2], goal[2, 3]]
    return float(np.dot(goal[:3, 2], vector) / max(np.linalg.norm(vector), 1e-9))


def candidate_pool(sim, config, candidates, seed, forced):
    rng = np.random.default_rng(seed)
    if forced:
        pool = [c for c in candidates if c["row"] == forced["grasp_row"] and c["source"] == forced["grasp_source"]]
        if not pool:
            raise ValueError("missing winning grasp")
        return pool[: config.max_grasp_candidates]
    method = config.grasp_candidate_order
    reference = tcp_pose(sim, sim.robot.arms[0])[:3, 3] if method == "tcp_distance" else sim.robot.base_pose()
    score = partial(_candidate_score, sim, reference, method)
    if method == "tcp_distance":
        return select_grasps(candidates, [body_pose(sim, sim.model.body(c["body"]).id) @ c["pose_local"] for c in candidates], reference, config.max_grasp_candidates, config.grasp_fps_position_scale_m, config.grasp_fps_rotation_scale_rad)
    pool = sorted(candidates, key=score, reverse=True)[: max(config.max_grasp_candidates * 8, 16)]
    return [pool[i] for i in rng.permutation(len(pool))[: config.max_grasp_candidates]]


def grasp_goals(sim, config, pool, *, retreat=False):
    """World grasp goals; open retreats along the approach, pick pushes in."""
    goals = [body_pose(sim, sim.model.body(c["body"]).id) @ c["pose_local"] for c in pool]
    angle = config.grasp_roll_rad
    roll = np.array([[np.cos(angle), -np.sin(angle), 0.0], [np.sin(angle), np.cos(angle), 0.0], [0.0, 0.0, 1.0]])
    offset = -config.approach_offset_m if retreat else config.approach_offset_m
    for goal in goals:
        goal[:3, 3] += goal[:3, 2] * offset
        goal[:3, :3] = goal[:3, :3] @ roll
    return goals


@dataclass
class GraspPlan:
    """The arm and grasp the planner accepted, plus where its artefacts live."""

    side: str
    candidate: dict
    goal: np.ndarray
    pregrasp: np.ndarray
    stages: dict
    directory: Path


def _rotation_about(axis, angle):
    """Rodrigues rotation about a unit axis."""
    skew = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * skew + (1 - np.cos(angle)) * (skew @ skew)


class _Manipulation:
    def __init__(self, ctx, task, candidates, assets_dir, seed, forced=None):
        self.ctx, self.sim, self.task = ctx, ctx.sim, task
        self.seed, self.assets_dir = seed, assets_dir
        self.pool = candidate_pool(ctx.sim, ctx.config, candidates, seed, forced)
        if forced:
            self.sides = [forced["arm"]]
        else:
            arms = list(self.sim.robot.arms)
            self.sides = arms if seed % 2 == 0 else arms[::-1]
        self.options = planner_options(ctx.config, assets_dir, seed)
        self.planner = None
        self.factory = ctx.planner_factory or V2Planner

    def close(self):
        if self.planner is not None:
            self.planner.detach()
            self.planner.close()
            self.planner = None

    def _query(self, phase):
        ctx = self.ctx
        ctx.deadline()
        if ctx.plan_count >= ctx.max_plans:
            raise OperationFailure("planning_budget_no_solution")
        ctx.plan_count += 1
        ctx.plan_serial += 1
        ctx.phase = "plan_" + phase
        ctx.plans.append(
            {
                "phase": ctx.phase,
                "query_index": ctx.plan_count,
                "plan_serial": ctx.plan_serial,
                "planner": PLANNER_BACKEND,
                "native_max_attempts": NATIVE_MAX_ATTEMPTS,
            }
        )
        write_json(ctx.recorder.path / "planning.json", ctx.plans)

    def _make_planner(self, options, side, directory):
        directory.mkdir(parents=True, exist_ok=True)
        with timed(self.ctx, "planner_setup"):
            self.planner = self.factory(
                self.sim, options, side, directory, workspace=(self.task["anchor_world"], self.ctx.config.radius_m)
            )

    def _operation_state(self):
        """Measured state the planner starts from; split calibration reuses the prepared station state."""
        if self.ctx.prepared_state is not None:
            return self.ctx.prepared_state
        path = self.ctx.recorder.path / f"operation-state-{self.ctx.plan_serial}.npz"
        self.sim.save_snapshot(path)
        return path

    def _plan(self):
        ctx = self.ctx
        diagnostic = None
        operation_state = None
        lift = self.task["operation"] == "pick" and self.sim.robot.plan_lift_before_close
        for side in self.sides:
            ctx.deadline()
            directory = ctx.recorder.path / f"v080-{ctx.plan_serial}-{side}"
            self._make_planner(self.options, side, directory)
            goals = grasp_goals(self.sim, ctx.config, self.pool, retreat=self.task["operation"] == "open")
            write_json(
                directory / "grasp_candidates.json",
                {"lanes": [{"seed": self.seed, "arm": side, "candidates": self.pool}]},
            )
            if not goals:
                raise OperationFailure("no_grasp_candidates")
            if operation_state is None:
                operation_state = self._operation_state()
            with timed(ctx, "grasp_planning"):
                success, index, stages, diagnostic = self.planner.grasp(goals, self._query, lift=lift)
            ctx.deadline()
            ctx.plans.append(
                {
                    "phase": "native_grasp_result",
                    "planning_state_ref": str(Path(operation_state).resolve()),
                    **diagnostic,
                }
            )
            if success:
                goal = goals[index]
                pregrasp = goal.copy()
                pregrasp[:3, 3] -= goal[:3, 2] * GRASP_APPROACH_DISTANCE_M
                return GraspPlan(side, self.pool[index], goal, pregrasp, stages, directory)
            self.close()
        raise OperationFailure((diagnostic or {}).get("failure_reason") or "planning_no_solution", diagnostic)

    def _control_record(self, plan):
        return {
            "arm": plan.side,
            "height_target": self.sim.robot.control_height(),
            "grasp_row": plan.candidate["row"],
            "grasp_source": plan.candidate["source"],
            "seed": self.seed,
        }

    def _initialize_monitor(self, plan):
        ctx = self.ctx
        ctx.monitor = PickMonitor(self.sim, plan.side)
        ctx.fingers = ctx.monitor.allowed_target_fingers
        ctx.samples = []
        ctx.monitor.initial.update(
            workspace_center_xy=self.task["anchor_world"][:2], workspace_radius_m=ctx.config.radius_m
        )
        initial = copy.deepcopy(ctx.monitor.initial)
        ctx.pick_initial = initial if self.task["operation"] == "pick" else None
        ctx.active_control = self._control_record(plan)
        write_json(
            ctx.recorder.path / "task_evidence_initial.json",
            {"operation": self.task["operation"], "initial": initial, "control": ctx.active_control},
        )
        ctx.manip_base_target = self.sim.robot.group("base").copy()
        if self.task["operation"] == "open":
            joint = self.sim.model.joint(self.task["joint_name"]).id
            ctx.open_joint = int(self.sim.model.jnt_qposadr[joint])

    def _approach_and_close(self, plan):
        ctx, robot = self.ctx, self.sim.robot
        ctx.begin()
        ctx.phase = "pregrasp"
        ctx.follow_points(self.planner, plan.stages["pregrasp"], plan.side, robot.open_grip, plan.pregrasp)
        ctx.phase = "approach"
        ctx.follow_points(self.planner, plan.stages["approach"], plan.side, robot.open_grip, plan.goal)
        ctx.phase = "close"
        q = robot.arm_positions(plan.side).copy()
        torso = ctx.hold_torso_target()
        for _ in range(20):
            ctx.arm_tick(plan.side, q, 0.0, torso)

    def _closed_planner(self, plan):
        """Re-plan with the grasp closed; Panda locks its base once the object is held."""
        self.close()
        config = self.ctx.config
        if self.sim.robot.lock_base_after_close:
            config = replace(config, operation_base_mode="fixed")
        plan.directory = self.ctx.recorder.path / f"v080-closed-{self.ctx.plan_serial}-{plan.side}"
        self._make_planner(planner_options(config, self.assets_dir, self.seed), plan.side, plan.directory)

    def _lift_and_hold(self, plan):
        ctx = self.ctx
        write_json(ctx.recorder.path / "attached_object.json", self.planner.attach_target_geometry())
        ctx.phase = "lift_actual"
        lift = tcp_pose(self.sim, plan.side).copy()
        lift[2, 3] += 0.1
        ctx.follow(self.planner, lift, plan.side, 0.0)
        ctx.phase = "hold"
        q = self.sim.robot.arm_positions(plan.side).copy()
        return hold_pick(ctx, plan.side, q, ctx.hold_torso_target())

    def _open_target(self, side, delta):
        tcp = tcp_pose(self.sim, side).copy()
        joint = self.sim.model.joint(self.task["joint_name"]).id
        axis = self.sim.data.xaxis[joint].copy()
        if self.task["joint_kind"] == "slide":
            tcp[:3, 3] += axis * delta
            return tcp
        rotation = _rotation_about(axis, delta)
        origin = self.sim.data.xanchor[joint]
        tcp[:3, 3] = origin + rotation @ (tcp[:3, 3] - origin)
        tcp[:3, :3] = rotation @ tcp[:3, :3]
        return tcp

    def _open_and_hold(self, plan):
        ctx, task = self.ctx, self.task
        if not ctx.samples or len(ctx.samples[-1]["finger_forces_n"]) != 2:
            raise OperationFailure("open_no_grip")
        self.planner.single_phase = "approach_contact"
        slide = task["joint_kind"] == "slide"
        step = ctx.config.open_waypoint_step_m if slide else ctx.config.open_waypoint_step_rad
        count = int(abs(task["joint_goal"] - task["joint_initial"]) / step) * 3 + 12
        direction = np.sign(task["joint_goal"] - task["joint_initial"])
        torso = ctx.hold_torso_target()
        for index in range(count):
            remaining = direction * (task["joint_goal"] - float(self.sim.data.qpos[ctx.open_joint]))
            if remaining <= 0.001:
                break
            tcp = self._open_target(plan.side, direction * min(step, remaining + 0.002))
            world = plan.directory / f"world-{index}"
            world.mkdir(parents=True, exist_ok=True)
            self.planner.refresh_world(world)
            ctx.phase = "open"
            ctx.follow(self.planner, tcp, plan.side, 0.0)
            torso = ctx.hold_torso_target()
            if len(ctx.samples[-1]["finger_forces_n"]) != 2:
                raise OperationFailure("open_contact_lost")
        ctx.phase = "open_hold"
        q = self.sim.robot.arm_positions(plan.side).copy()
        for _ in range(int(np.ceil((ctx.config.open_hold_s + 0.1) * self.sim.robot.config.control_hz))):
            ctx.arm_tick(plan.side, q, 0.0, torso)
        return evaluate_open(task, ctx.samples, ctx.config.open_hold_s)

    def run(self):
        try:
            plan = self._plan()
            self._initialize_monitor(plan)
            self._approach_and_close(plan)
            self._closed_planner(plan)
            if self.task["operation"] == "pick":
                verdict = self._lift_and_hold(plan)
            else:
                verdict = self._open_and_hold(plan)
            write_json(self.ctx.recorder.path / f"verdict-{len(self.ctx.plans)}.json", verdict)
            return verdict, self._control_record(plan)
        finally:
            self.close()


def manipulate(ctx, task, candidates, assets_dir, seed, forced=None):
    return _Manipulation(ctx, task, candidates, assets_dir, seed, forced).run()
