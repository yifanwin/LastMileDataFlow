"""B0/B1/B2 station-quality baselines over a candidate station table (Vahrenkamp 2013 RD/ORM).

  export (RoboCasa env): per (scene, valid station) planning problems + native spawn
  rd     (cuRobo env):   build the 7-DoF arm reachability distribution
  score  (cuRobo env):   B0/B1/B2 per (scene, station), one shard per process
  summary(any env):      merge shards, paired-difference checks, timings
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from lastmile_dataflow.utils.io import append_row, rows, write_json, write_rows


def export(a):
    from lastmile_dataflow.planning.robocasa_bridge import export_problem
    from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime

    out = Path(a.output); (out / "problems").mkdir(parents=True, exist_ok=True)
    stations = [s for s in rows(a.table) if s["valid"]]
    with np.load(a.grasps) as g:
        pool = g["object_to_tcp"]
    index = []
    for scene in a.scenes:
        rt = RoboCasaRuntime(Path(a.scenes_root) / scene)
        spawn = rt.reset() and rt.base_pose()
        target_xy = rt.env.sim.data._data.xpos[rt.target_body][:2].tolist()
        for s in stations:
            rt.set_station(s["x"], s["y"], s["yaw"])
            rt.lock_base()  # torso at 0.00 m, as in the evaluators
            path = out / "problems" / f"{scene}__{s['station_id']}.npz"
            export_problem(rt, pool, path)
            index.append({"scene": scene, "station_id": s["station_id"], "x": s["x"], "y": s["y"], "yaw": s["yaw"],
                          "problem": str(path), "spawn_xyyaw": spawn.tolist(), "target_xy": target_xy})
        rt.close()
    write_rows(out / "index.jsonl", index)
    print("exported", len(index))


def _planner(gpu_pool=152):
    from lastmile_dataflow.planning.robocasa_curobo import RoboCasaGraspPlanner
    return RoboCasaGraspPlanner(gpu_pool)


def rd(a):
    from lastmile_dataflow.planning.robocasa_curobo import ARM_JOINTS
    from lastmile_dataflow.station_map import irm
    import torch

    kin = _planner(1).mp.kinematics
    grid, stats = irm.build_rd(kin, ARM_JOINTS, a.samples)
    # FK look-back: fresh non-self-colliding samples must land in filled voxels
    from curobo.types import JointState
    lim = kin.get_joint_limits().position
    q = lim[0] + (lim[1] - lim[0]) * torch.rand(20000, 7, device="cuda")
    s = kin.compute_kinematics(JointState.from_position(q, joint_names=ARM_JOINTS))
    v = grid.query(torch.cat([torch.cat([irm.quat_to_matrix(s.tool_poses.quaternion.reshape(-1, 4)), s.tool_poses.position.reshape(-1, 3, 1)], 2),
                              torch.tensor([0, 0, 0, 1.0], device="cuda").expand(20000, 1, 4)], 1).cpu().numpy())
    stats["lookback_nonzero"] = float((v > 0).mean())
    stats.update(pos_res_m=irm.POS_RES_M, ang_bins=irm.ANG_BINS, bounds_m=irm.BOUNDS_M, k_l=irm.K_L, d_self_m=irm.D_SELF_M)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    grid.save(a.output, stats)
    write_json(Path(a.output).with_suffix(".json"), stats)
    print(json.dumps(stats))


def score(a):
    from dataclasses import asdict
    from lastmile_dataflow.station_map import irm

    items = rows(a.index)[a.shard::a.shards]
    grid = irm.ReachabilityGrid.load(a.rd)
    world, free = irm.IKChecker(_planner()), irm.IKChecker(_planner())
    out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
    done = {(r["scene"], r["station_id"]) for p in out.glob("scores-*.jsonl") for r in rows(p)}
    for it in items:
        if (it["scene"], it["station_id"]) in done:
            continue
        with np.load(it["problem"]) as f:
            problem = dict(f)
        t0 = time.monotonic()
        spawn = np.array(it["spawn_xyyaw"])
        b0 = irm.b0_score(np.array([it["x"], it["y"]]), it["yaw"], np.array(it["target_xy"]),
                          float(np.hypot(*(np.array(it["target_xy"]) - spawn[:2]))))
        b0_s = time.monotonic() - t0
        world.p._load_world(problem)
        sc = irm.score_station(grid, world, problem, trace_planner=free)
        append_row(out / f"scores-{a.shard}.jsonl", {"scene": it["scene"], "station_id": it["station_id"], "b0": b0, "b0_s": b0_s, **asdict(sc)})
        print(it["scene"], it["station_id"], round(b0, 3), round(sc.b1_mean, 3), round(sc.b2_mean, 3), round(sc.b2_s, 1), flush=True)


def summary(a):
    out = Path(a.output)
    scores = sorted((r for p in out.glob("scores-*.jsonl") for r in rows(p)), key=lambda r: (r["scene"], r["station_id"]))
    by = {(r["scene"], r["station_id"]): r for r in scores}
    scenes = sorted({r["scene"] for r in scores})
    stations = sorted({r["station_id"] for r in scores})
    diff = {k: max(abs(by[(scenes[0], s)][k] - by[(sc, s)][k]) for sc in scenes[1:] for s in stations if (sc, s) in by and (scenes[0], s) in by)
            for k in ("b0", "b1_mean", "b1_max", "b2_mean")} if len(scenes) > 1 else {}
    verdicts = {}
    for r in scores:
        for g in r["grasps"]:
            key = g["verdict"] + (":" + g["waypoint"] if "waypoint" in g else "")
            verdicts.setdefault(r["scene"], {}).setdefault(key, 0)
            verdicts[r["scene"]][key] += 1
    table = [{k: r[k] for k in ("scene", "station_id", "b0", "b1_mean", "b1_max", "b2_mean", "b2_max", "b1_s", "b2_s")} for r in scores]
    write_rows(out / "scores.jsonl", table)
    write_rows(out / "method_scores.jsonl", [{"method": m, "scene": r["scene"], "station_id": r["station_id"], "score": r[k]}
                                             for r in scores for m, k in (("B0", "b0"), ("B1", "b1_mean"), ("B2", "b2_mean"), ("B1-max", "b1_max"), ("B2-max", "b2_max"))])
    s = {"rows": len(scores), "scenes": scenes, "stations": len(stations), "paired_max_abs_diff": diff, "grasp_verdicts": verdicts,
         "time_s": {k: {"median": float(np.median([r[k] for r in scores])), "map_total": float(np.sum([r[k] for r in scores]) / max(1, len(scenes)))}
                    for k in ("b0_s", "b1_s", "b2_s")}}
    write_json(out / "summary.json", s)
    print(json.dumps(s, indent=1))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--scenes-root", required=True); e.add_argument("--table", required=True)
    e.add_argument("--scenes", nargs="+", required=True); e.add_argument("--grasps", required=True); e.add_argument("--output", required=True)
    r = sub.add_parser("rd"); r.add_argument("--samples", type=int, default=100_000_000); r.add_argument("--output", required=True)
    s = sub.add_parser("score"); s.add_argument("--index", required=True); s.add_argument("--rd", required=True); s.add_argument("--output", required=True)
    s.add_argument("--shard", type=int, default=0); s.add_argument("--shards", type=int, default=1)
    m = sub.add_parser("summary"); m.add_argument("--output", required=True)
    a = p.parse_args()
    {"export": export, "rd": rd, "score": score, "summary": summary}[a.cmd](a)


if __name__ == "__main__":
    main()
