"""Evaluator B (pure physics): cuRobo plan from the actual docked state, native joint tracking, PickJudge.

Runs in the RoboCasa env; planning runs in a persistent cuRobo worker (main .venv) over a line protocol.
Failures are classified by the first broken stage: docking, planning (goalset/approach/grasp/lift),
tracking, grasp, lift, hold.
"""
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL, PickJudge
from lastmile_dataflow.planning.robocasa_bridge import export_problem

CLOSE_STEPS = 20  # 1 s closing at the grasp pose
HOLD_STEPS = 30  # 1.5 s after the lift; the judge needs 1 s
TRACK_TOL_RAD = 0.05
DOCK_XY_M = 0.01
DOCK_YAW_DEG = 1.0


@dataclass(frozen=True)
class Trial:
    scene: str
    station_id: str
    x: float
    y: float
    yaw: float
    k: int
    dx: float
    dy: float
    dyaw: float
    seed: int

    @property
    def trial_id(self):
        return f"{self.scene}/{self.station_id}/k{self.k:02d}"


def docking_errors(station_number, k, seed):
    """K docking errors per station, identical across scenes (seeded by the station table row)."""
    rng = np.random.default_rng([seed, station_number])
    return rng.uniform(-1, 1, (k, 3)) * [DOCK_XY_M, DOCK_XY_M, np.radians(DOCK_YAW_DEG)]


class CuroboWorker:
    def __init__(self, root, gpu, log_path):
        root = Path(root)
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu), "PYTHONPATH": str(root / "src"),
               "LD_LIBRARY_PATH": str(root / ".venv/lib/native"), "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"}
        self.log = open(log_path, "a")
        python = root.parents[1] / ".venv/bin/python"
        self.proc = subprocess.Popen([str(python), "-m", "lastmile_dataflow.planning.robocasa_curobo"], cwd=root, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True)

    def plan(self, problem, plan, seed):
        self.proc.stdin.write(json.dumps({"problem": str(problem), "plan": str(plan), "seed": seed}) + "\n")
        self.proc.stdin.flush()
        for line in self.proc.stdout:
            if line.startswith("RESULT "):
                return json.loads(line[7:])
        raise RuntimeError(f"cuRobo worker exited ({self.proc.poll()}), see {self.log.name}")

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=60)
        self.log.close()


def _action(runtime, arm, gripper):
    a, s = np.zeros(runtime.action_spec.dim), 0
    for name, size in runtime.action_spec.parts:
        if name == "right":
            a[s:s + size] = arm
        elif name == "right_gripper":
            a[s:s + size] = gripper
        elif name == "base_mode":
            a[s:s + size] = -1.0
        s += size
    return a


def _object_positions(runtime):
    data = runtime.env.sim.data._data
    model = runtime.env.sim.model._model
    return {n: data.xpos[model.body(o.root_body).id].copy() for n, o in runtime.env.objects.items() if n != runtime.target}


class Execution:
    """Steps commanded arm goals and keeps the per-step facts the classification needs."""

    def __init__(self, runtime, judge):
        self.rt, self.judge = runtime, judge
        self.status = None
        self.best_lift = self.best_hold = 0.0
        self.grasp_steps = self.contact_steps = self.steps = 0
        self.track_error = {}

    def run(self, stage, points, gripper):
        for q in points:
            self.rt.step(_action(self.rt, q, gripper))
            self.status = self.judge.update(self.rt.get_state())
            self.steps += 1
            self.best_lift = max(self.best_lift, self.status.lift_m)
            self.best_hold = max(self.best_hold, self.status.hold_s)
            self.grasp_steps += self.status.grasped
            self.contact_steps += self.rt.robot_env_contacts(include_target=False) > 0
        arm = np.array(self.rt.joint_positions()["right"])
        self.track_error[stage] = float(np.max(np.abs(arm - points[-1])))


def _failure(execution, grasped_after_close):
    if execution.status.success:
        return "success"
    if max(execution.track_error.values()) > TRACK_TOL_RAD:
        return "tracking_deviation"
    if not grasped_after_close:
        return "not_grasped"
    if execution.best_lift < PICK_PROTOCOL["lift_m"]:
        return "lift_insufficient"
    return "hold_broken"


def run_trial(runtime, worker, pool, trial, workdir):
    started = time.monotonic()
    initial = runtime.set_station(trial.x + trial.dx, trial.y + trial.dy, trial.yaw + trial.dyaw)
    runtime.lock_base()
    row = {**asdict(trial), "trial_id": trial.trial_id, "success": False}
    docking_contacts = runtime.robot_env_contacts()
    if docking_contacts:
        return {**row, "status": "invalid_docking", "docking_contacts": docking_contacts, "wall_s": time.monotonic() - started}
    stem = Path(workdir) / trial.trial_id.replace("/", "__")
    export_problem(runtime, pool, f"{stem}-problem.npz")
    plan = worker.plan(f"{stem}-problem.npz", f"{stem}-plan.npz", trial.seed)
    row["plan"] = plan
    if not plan["success"]:
        return {**row, "status": f"plan_{plan['failed_stage']}", "wall_s": time.monotonic() - started}
    with np.load(f"{stem}-plan.npz") as stages:
        stages = {k: stages[k] for k in ("approach", "grasp", "lift")}
    objects0 = _object_positions(runtime)
    execution = Execution(runtime, PickJudge(runtime.target, initial.target_pose[2]))
    execution.run("approach", stages["approach"], -1.0)
    execution.run("grasp", stages["grasp"], -1.0)
    execution.run("close", np.repeat(stages["grasp"][-1:], CLOSE_STEPS, axis=0), 1.0)
    grasped = execution.status.grasped
    execution.run("lift", stages["lift"], 1.0)
    if not execution.status.success:
        execution.run("hold", np.repeat(stages["lift"][-1:], HOLD_STEPS, axis=0), 1.0)
    objects1 = _object_positions(runtime)
    moved = {n: float(np.linalg.norm(objects1[n] - objects0[n])) for n in objects0}
    status = _failure(execution, grasped)
    return {
        **row, "status": status, "success": status == "success",
        "collision_free_success": status == "success" and execution.contact_steps == 0,
        "steps": execution.steps, "max_lift_m": execution.best_lift, "max_hold_s": execution.best_hold,
        "grasp_steps": execution.grasp_steps, "robot_env_contact_steps": execution.contact_steps,
        "track_error_rad": execution.track_error, "object_displacement_m": moved,
        "base_drift": [float(v) for v in runtime.max_base_drift], "wall_s": time.monotonic() - started,
    }
