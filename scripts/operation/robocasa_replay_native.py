"""Replay stored cuRobo plans in native RoboCasa and record per-control-step physics facts.

Run in .venv-robocasa. Each plan of a cuRobo run is executed as evaluator B does (nominal docking, locked
base, absolute JOINT_POSITION arm targets; approach, grasp, close, lift), followed by the full hold so both
simulators see the same command length. Every executed trial is replayed (successes and execution failures).
Writes <output>/<trial stem>.npz: the commanded arm targets and finger actuator controls (input of the mjlab
replay) and the measured state with PickJudge facts.

  python scripts/operation/robocasa_replay_native.py --run <curobo run> --scenes-root <scenes> --output <dir>
"""
import argparse
import json
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.outcomes.robocasa_pick import PickJudge
from lastmile_dataflow.operation.robocasa_curobo_trial import CLOSE_STEPS, HOLD_STEPS, _action
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.utils.io import rows

STAGES = ("approach", "grasp", "close", "lift", "hold")


def commands(plan):
    """Arm targets and gripper command per control step, in the evaluator's stage order."""
    with np.load(plan) as s:
        approach, grasp, lift = s["approach"], s["grasp"], s["lift"]
    stages = [(approach, -1.0), (grasp, -1.0), (np.repeat(grasp[-1:], CLOSE_STEPS, 0), 1.0),
              (lift, 1.0), (np.repeat(lift[-1:], HOLD_STEPS, 0), 1.0)]
    arm = np.concatenate([q for q, _ in stages])
    gripper = np.concatenate([np.full(len(q), c) for q, c in stages])
    return arm, gripper, {name: len(q) for name, (q, _) in zip(STAGES, stages)}


class Recorder:
    def __init__(self, rt, judge):
        self.rt, self.judge = rt, judge
        model = rt.env.sim.model._model
        gripper = rt.robot.gripper["right"]
        self.pads = [gripper.important_geoms[k] for k in ("left_fingerpad", "right_fingerpad")]
        self.object_geoms = rt.env.objects[rt.target].contact_geoms
        self.finger_actuators = [model.actuator(name).id for name in gripper.actuators]
        self.tcp = model.site("gripper0_right_grip_site").id
        self.link0 = model.body("robot0_link0").id
        self.trace = {}

    def record(self, ctrl=True):
        rt, data = self.rt, self.rt.env.sim.data._data
        state = rt.get_state()
        status = self.judge.update(state)
        joints = rt.joint_positions()
        values = {
            "time": state.time_s, "arm_q": joints["right"], "finger_q": joints["right_gripper"],
            "torso_q": joints["torso"],
            "finger_ctrl": data.ctrl[self.finger_actuators] if ctrl else np.full(2, np.nan),
            "tcp_pos": data.site_xpos[self.tcp], "tcp_mat": data.site_xmat[self.tcp],
            "link0_pos": data.xpos[self.link0], "link0_quat": data.xquat[self.link0],
            "target_pos": data.xpos[rt.target_body], "target_quat": data.xquat[rt.target_body],
            "pads": [rt.env.check_contact(p, self.object_geoms) for p in self.pads],
            "supported": state.target_supported, "lift": status.lift_m, "hold": status.hold_s,
            "success": status.success, "robot_env_contacts": rt.robot_env_contacts(include_target=False),
        }
        for key, value in values.items():
            self.trace.setdefault(key, []).append(np.array(value, dtype=float))


def replay(rt, trial, plan, path):
    initial = rt.set_station(trial["x"] + trial["dx"], trial["y"] + trial["dy"], trial["yaw"] + trial["dyaw"])
    rt.lock_base()
    tabletop = rt.tabletop_z(initial.support_contacts)
    arm, gripper, stages = commands(plan)
    recorder = Recorder(rt, PickJudge(rt.target, tabletop))
    recorder.record(ctrl=False)
    for q, g in zip(arm, gripper):
        rt.step(_action(rt, q, g))
        recorder.record()
    trace = {k: np.stack(v) for k, v in recorder.trace.items()}
    meta = {"scene": trial["scene"], "station_id": trial["station_id"], "trial_id": trial["trial_id"],
            "grasp_id": trial.get("grasp_id"), "evaluator_status": trial["evaluator_status"], "x": trial["x"], "y": trial["y"],
            "yaw": trial["yaw"], "tabletop_z": tabletop, "stages": stages, "plan": str(plan),
            "succeeded_at": recorder.judge.succeeded_at, "base_drift": [float(v) for v in rt.max_base_drift]}
    np.savez(path, arm_target=arm, gripper_command=gripper, meta=json.dumps(meta), **trace)
    return meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", type=Path, required=True, help="cuRobo run with trials-w*.jsonl and work/w*/*-plan.npz")
    p.add_argument("--scenes-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    # Every trial that reached native execution (successes and execution failures); planning failures have no plan.
    executed = lambda r: not r["status"].startswith(("plan_", "invalid_"))
    trials = [r for f in sorted(a.run.glob("trials-w*.jsonl")) for r in rows(f) if executed(r)]
    runtimes = {}
    try:
        for trial in sorted(trials, key=lambda r: r["trial_id"]):
            stem = trial["trial_id"].replace("/", "__")
            plan = next(a.run.glob(f"work/w*/{stem}-plan.npz"))
            scene = trial["scene"]
            if scene not in runtimes:
                runtimes[scene] = RoboCasaRuntime(a.scenes_root / scene, arm_controller="joint_position")
            meta = replay(runtimes[scene], {**trial, "evaluator_status": trial["status"]}, plan, a.output / f"{stem}.npz")
            keys = ("scene", "station_id", "trial_id", "evaluator_status", "succeeded_at", "base_drift")
            print(json.dumps({k: meta[k] for k in keys}), flush=True)
    finally:
        for rt in runtimes.values():
            rt.close()


if __name__ == "__main__":
    main()
