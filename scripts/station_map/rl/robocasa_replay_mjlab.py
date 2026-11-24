"""Replay native RoboCasa traces in the mjlab RL scene and compare the two simulators step by step.

Input: the per-scene traces of scripts/operation/robocasa_replay_native.py. mjlab gets the same station
(base_site from the native link0 table convention), the native frozen arm pose, and per control step the
same absolute arm joint targets and finger actuator controls, with the torso target at 0. The managers are
bypassed (no DLS, no termination or reset); the physics loop is the one of ManagerBasedRlEnv.step.
The simulators are allowed to differ (model, contact, controller); the comparison measures how much. PickJudge runs on mjlab facts with the native semantics (both pads touch the target, no environment
support, lowest target vertex >= 2 cm above the tabletop, held 0.5 s at the control rate).

  python scripts/station_map/rl/robocasa_replay_mjlab.py --native <dir> --config <yaml> \
    --rl-scenes <dir> --table <stations.jsonl> --gpu 0 --output <dir>
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from lastmile_dataflow.operation.outcomes.robocasa_pick import PickJudge
from lastmile_dataflow.station_map.rl.config import resolve_config
from lastmile_dataflow.utils.gpu import bind_device
from lastmile_dataflow.utils.io import read_config, rows

STEP_TOLERANCE = 1  # descriptive only: share of common successes whose steps differ by at most one control step


def scene_config(base, scene_dir, table, home):
    config = json.loads(json.dumps(base))
    config["scene"].update(scene_xml=str(scene_dir / "scene.xml"), scene_metadata=str(scene_dir / "metadata.json"),
                           home=[float(q) for q in home])
    config["environment"]["num_envs"] = 1
    config["environment"]["impratio"] = 20.0  # native robosuite base.xml
    config["scene"]["multiccd"] = True  # native RoboCasa kitchen enables multiccd
    config["noise"] = {"sigma_max": 0.0, "action": 0.0, "target_m": 0.0, "dock_m": 0.0, "dock_yaw_rad": 0.0}
    config["stations"]["table"] = str(table)
    return config


class MjlabReplay:
    def __init__(self, env):
        from lastmile_dataflow.station_map.rl.env.terms import target_bottom_height

        self.env, self.task = env, env.task
        self.bottom = target_bottom_height
        model = self.task.model
        names = [model.sensor(i).name for i in range(model.nsensor)]
        self.pad_names = [n for n in names if n.startswith("pad_") and n.endswith("_found")]
        self.link0 = next(i for i in range(model.nbody) if model.body(i).name.endswith("robot0_link0"))
        self.target_name = model.body(self.task.target).name

    def run(self, station_index, arm_target, finger_ctrl, arm_state=None):
        """arm_state: native arm_q per control step; when given, the arm joints are written each substep (contact-only comparison)."""
        task, env, robot = self.task, self.env, self.task.robot
        task.requested_station_ids = torch.tensor([station_index], device=env.device)
        env.reset()
        env.sim.forward()
        judge = PickJudge(self.target_name, task.table_height_m)
        trace = {}
        self.record(trace, judge, 0.0)
        device = env.device
        for k, (q, ctrl) in enumerate(zip(arm_target, finger_ctrl), 1):
            robot.set_joint_position_target(torch.tensor(q, device=device, dtype=torch.float32)[None], joint_ids=task.arm)
            robot.set_joint_position_target(torch.zeros(1, len(task.lift), device=device), joint_ids=task.lift)
            robot.set_joint_position_target(torch.tensor(ctrl, device=device, dtype=torch.float32)[None],
                                            joint_ids=task.fingers)
            for j in range(env.cfg.decimation):
                if arm_state is not None:
                    w = (j + 1) / env.cfg.decimation
                    qa = torch.tensor((1 - w) * arm_state[k - 1] + w * arm_state[k], device=device, dtype=torch.float32)[None]
                    qd = torch.tensor((arm_state[k] - arm_state[k - 1]) / env.step_dt, device=device, dtype=torch.float32)[None]
                    robot.write_joint_state_to_sim(qa, qd, joint_ids=task.arm)
                env.scene.write_data_to_sim()
                env.sim.step()
                env.scene.update(dt=env.physics_dt)
            self.record(trace, judge, k * env.step_dt)
        return {k: np.stack(v) for k, v in trace.items()}, judge

    def record(self, trace, judge, time_s):
        task, data = self.task, self.env.sim.data
        values = data.sensordata[0]
        tcp_p, tcp_r, _, _, target_p, target_r = task.poses()
        pads = (values[task.pad_found] > 0).cpu().numpy()
        supported = bool((values[task.support_addresses] > 0).any())
        bottom = float(self.bottom(task, target_p, target_r)[0])
        q = task.robot.data.joint_pos[0]
        state = SimpleNamespace(time_s=time_s, target=self.target_name, target_bottom_z=bottom,
                                grasped_objects=(self.target_name,) if pads.all() else (), target_supported=supported)
        status = judge.update(state)
        item = {
            "time": time_s, "arm_q": q[task.arm].cpu().numpy(), "finger_q": q[task.fingers].cpu().numpy(),
            "torso_q": q[task.lift].cpu().numpy(), "tcp_pos": tcp_p[0].cpu().numpy(), "tcp_mat": tcp_r[0].cpu().numpy().ravel(),
            "link0_pos": data.xpos[0, self.link0].cpu().numpy(), "link0_quat": data.xquat[0, self.link0].cpu().numpy(),
            "target_pos": target_p[0].cpu().numpy(), "target_quat": data.xquat[0, task.target].cpu().numpy(),
            "pads": pads.astype(float), "supported": supported, "lift": status.lift_m, "hold": status.hold_s,
            "success": status.success,
        }
        for key, value in item.items():
            trace.setdefault(key, []).append(np.asarray(value, dtype=float))


def first(flags):
    hit = np.flatnonzero(flags)
    return int(hit[0]) if len(hit) else None


def relative_target(trace):
    """Target position in the TCP frame per step (grasp slip view)."""
    R = trace["tcp_mat"].reshape(-1, 3, 3)
    return np.einsum("nji,nj->ni", R, trace["target_pos"] - trace["tcp_pos"])


def yaw(quat):
    w, x, y, z = quat.T
    return np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def compare(native, mjlab, meta):
    stages = np.cumsum([0, *meta["stages"].values()])
    close_end, lift_end = int(stages[3]), int(stages[4])
    out = {}
    for name, t in (("native", native), ("mjlab", mjlab)):
        both = t["pads"].all(-1)
        succ = first(t["success"] > 0)
        out[name] = {
            "success": succ is not None, "success_step": succ,
            "grasp_step": first(both), "release_step": first(t["supported"] == 0),
            "lifted_step": first(t["lift"] >= 0.02), "max_lift_m": float(t["lift"].max()),
            "grasped_after_close": bool(both[close_end]), "grasped_at_end": bool(both[-1]),
            "track_error_rad": float(np.abs(t["arm_q"][1:] - native["arm_target"]).max()),
            "finger_width_after_close_m": float(t["finger_q"][close_end, 0] - t["finger_q"][close_end, 1]),
            "torso_max_abs_m": float(np.abs(t["torso_q"]).max(initial=0.0)),
            "slip_after_lift_m": float(np.linalg.norm(relative_target(t)[-1] - relative_target(t)[close_end])),
        }
    d_yaw = yaw(native["link0_quat"]) - yaw(mjlab["link0_quat"])
    out["difference"] = {
        "initial_link0_xy_m": float(np.linalg.norm(native["link0_pos"][0, :2] - mjlab["link0_pos"][0, :2])),
        "initial_link0_z_m": float(native["link0_pos"][0, 2] - mjlab["link0_pos"][0, 2]),
        "initial_link0_yaw_rad": float(np.arctan2(np.sin(d_yaw[0]), np.cos(d_yaw[0]))),
        "initial_tcp_m": float(np.linalg.norm(native["tcp_pos"][0] - mjlab["tcp_pos"][0])),
        "initial_target_m": float(np.linalg.norm(native["target_pos"][0] - mjlab["target_pos"][0])),
        "max_tcp_m": float(np.linalg.norm(native["tcp_pos"] - mjlab["tcp_pos"], axis=-1).max()),
        "max_arm_q_rad": float(np.abs(native["arm_q"] - mjlab["arm_q"]).max()),
        "target_end_m": float(np.linalg.norm(native["target_pos"][-1] - mjlab["target_pos"][-1])),
        "finger_width_after_close_m": out["native"]["finger_width_after_close_m"] - out["mjlab"]["finger_width_after_close_m"],
        "tabletop_z_m": meta["tabletop_z"] - meta["mjlab_table_z"],
    }
    s_n, s_m = out["native"]["success_step"], out["mjlab"]["success_step"]
    out["success_step_delta"] = None if s_n is None or s_m is None else s_m - s_n
    out["outcome_agree"] = out["native"]["success"] == out["mjlab"]["success"]
    out["step_within_tolerance"] = out["success_step_delta"] is not None and abs(out["success_step_delta"]) <= STEP_TOLERANCE
    out["lift_end_step"] = lift_end
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--native", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--rl-scenes", type=Path, required=True)
    p.add_argument("--table", type=Path, required=True)
    p.add_argument("--grasps", type=Path, required=True, help="rl_transforms.npz of the grasp pool")
    p.add_argument("--gpu", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--force-arm", action="store_true", help="write the native arm joint positions each substep")
    a = p.parse_args()
    bind_device(a.gpu)
    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv

    base = read_config(a.config)
    base["grasp"]["preprocessed_path"] = str(a.grasps.resolve())
    traces = sorted(a.native.glob("*.npz"))
    a.output.mkdir(parents=True, exist_ok=True)
    by_scene = {}
    for path in traces:
        by_scene.setdefault(path.name.split("__")[0], []).append(path)
    results = []
    for scene, paths in by_scene.items():
        loaded = [np.load(path) for path in paths]
        metas = [json.loads(str(z["meta"])) for z in loaded]
        ids = {m["station_id"] for m in metas}
        table = a.output / f"{scene}-stations.jsonl"
        table.write_text("".join(json.dumps(r) + "\n" for r in rows(a.table) if r["station_id"] in ids))
        home = loaded[0]["arm_q"][0]
        config = resolve_config(scene_config(base, a.rl_scenes / scene, table, home))
        env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=0, num_envs=1)
        try:
            replay = MjlabReplay(env)
            index = {s["station_id"]: i for i, s in enumerate(env.task.stations)}
            for z, meta, path in zip(loaded, metas, paths):
                native = {k: z[k] for k in z.files if k != "meta"}
                mjlab, judge = replay.run(index[meta["station_id"]], native["arm_target"], native["finger_ctrl"][1:],
                                          native["arm_q"] if a.force_arm else None)
                np.savez(a.output / path.name, **mjlab)
                meta["mjlab_table_z"] = float(env.task.table_height_m)
                result = {"scene": scene, "station_id": meta["station_id"], "trial_id": meta.get("trial_id"),
                          "grasp_id": meta.get("grasp_id"), "evaluator_status": meta.get("evaluator_status"),
                          "pad_sensors": replay.pad_names,
                          **compare(native, mjlab, meta)}
                results.append(result)
                print(json.dumps(result), flush=True)
        finally:
            env.close()
    (a.output / "comparison.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))
    both = [r for r in results if r["native"]["success"] and r["mjlab"]["success"]]
    cells = {f"native_{n}_mjlab_{m}": sum(r["native"]["success"] == (n == "success") and r["mjlab"]["success"] == (m == "success")
                                          for r in results)
             for n in ("success", "fail") for m in ("success", "fail")}
    summary = {"pairs": len(results), "outcome_agree": sum(r["outcome_agree"] for r in results), **cells,
               "success_step_delta": sorted(r["success_step_delta"] for r in both),
               "step_within_tolerance": sum(r["step_within_tolerance"] for r in both), "step_tolerance": STEP_TOLERANCE}
    (a.output / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
