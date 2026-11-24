"""Evaluator B batch: cuRobo + native tracking over a paired station table, K docking errors per station.

Example:
  python scripts/lastmile_benchmark/robocasa_curobo.py --scenes-root <runs/.../scenes> --station-table <station-table/.../stations.jsonl> \
    --scenes kettle-far kettle-left-125mm --grasps <grasps.npz> --output outputs/experiments/robocasa-station-eval/runs/<ts>-curobo \
    --gpus 3 4 --workers-per-gpu 4 --k 15
"""
import argparse
import collections
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.robocasa_curobo_trial import (CuroboWorker, DOCK_XY_M, DOCK_YAW_DEG, Trial,
                                                               docking_errors, run_trial)
from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.utils.io import append_row, create_run, rows, write_json, write_rows

ROOT = Path(__file__).resolve().parents[2]


def table_stations(table, station_ids):
    """Legal rows of a paired station table (scripts/scenes/robocasa_station_table.py), with their row index."""
    return [(i, r) for i, r in enumerate(rows(table)) if r["valid"] and (not station_ids or r["station_id"] in station_ids)]


def worker(slot, gpu, scenes_root, grasps, trials, output):
    out = Path(output)
    work = out / "work" / f"w{slot}"
    work.mkdir(parents=True, exist_ok=True)
    with np.load(grasps) as g:
        pool = g["object_to_tcp"]
    planner = CuroboWorker(ROOT, gpu, out / "logs" / f"curobo-w{slot}.log")
    runtime, scene = None, None
    try:
        for trial in trials:
            if trial.scene != scene:
                if runtime is not None:
                    runtime.close()
                runtime, scene = RoboCasaRuntime(Path(scenes_root) / trial.scene, arm_controller="joint_position"), trial.scene
            append_row(out / f"trials-w{slot}.jsonl", {**run_trial(runtime, planner, pool, trial, work), "gpu": gpu})
    finally:
        if runtime is not None:
            runtime.close()
        planner.close()
    return slot


def summarize(output):
    trials = [r for p in sorted(Path(output).glob("trials-w*.jsonl")) for r in rows(p)]
    trials.sort(key=lambda r: r["trial_id"])
    write_rows(Path(output) / "trials.jsonl", trials)
    by = collections.defaultdict(list)
    for r in trials:
        by[(r["scene"], r["station_id"])].append(r)
    stations = []
    for (scene, sid), items in sorted(by.items()):
        legal = [r for r in items if r["status"] != "invalid_docking"]
        stations.append({
            "scene": scene, "station_id": sid, "n": len(items), "success_rate": float(np.mean([r["success"] for r in items])),
            "collision_free_rate": float(np.mean([r.get("collision_free_success", False) for r in items])),
            "legal_docking": len(legal),
            "success_rate_legal": float(np.mean([r["success"] for r in legal])) if legal else None,
            "status": dict(collections.Counter(r["status"] for r in items)),
        })
    write_rows(Path(output) / "stations.jsonl", stations)
    total = collections.Counter(r["status"] for r in trials)
    write_json(Path(output) / "summary.json", {"trials": len(trials), "status": dict(total),
                                               "success_rate": float(np.mean([r["success"] for r in trials])) if trials else None})
    return stations


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenes-root", required=True)
    p.add_argument("--station-table", required=True)
    p.add_argument("--scenes", nargs="+", required=True)
    p.add_argument("--grasps", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--gpus", nargs="+", type=int, required=True)
    p.add_argument("--workers-per-gpu", type=int, default=2)
    p.add_argument("--k", type=int, default=15)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--stations", nargs="*", default=[], help="station ids; default all shared legal stations")
    p.add_argument("--no-docking-error", action="store_true", help="k=0 nominal docking only (smoke)")
    a = p.parse_args()
    stations = table_stations(a.station_table, set(a.stations))
    out = create_run(Path(a.output), {**vars(a), "criterion": PICK_PROTOCOL, "stations": [s["station_id"] for _, s in stations],
                                      "docking_error": {"xy_m": DOCK_XY_M, "yaw_deg": DOCK_YAW_DEG, "frame": "world"},
                                      "arm_controller": "joint_position (native robosuite, absolute)"})
    (out / "logs").mkdir(exist_ok=True)
    trials = []
    for scene in a.scenes:
        for row, s in stations:
            errors = np.zeros((1, 3)) if a.no_docking_error else docking_errors(row, a.k, a.seed)
            for k, (dx, dy, dyaw) in enumerate(errors):
                trials.append(Trial(scene, s["station_id"], s["x"], s["y"], s["yaw"], k, float(dx), float(dy), float(dyaw), a.seed * 1000 + k))
    slots = [(i, gpu) for i, gpu in enumerate(g for g in a.gpus for _ in range(a.workers_per_gpu))]
    blocks = np.array_split(np.arange(len(trials)), len(slots))
    write_json(out / "plan.json", {str(i): [trials[j].trial_id for j in b] for (i, _), b in zip(slots, blocks)})
    print(f"trials {len(trials)} stations {len(stations)} workers {len(slots)}", flush=True)
    with ProcessPoolExecutor(len(slots), mp_context=multiprocessing.get_context("spawn")) as ex:
        futures = [ex.submit(worker, i, gpu, a.scenes_root, a.grasps, [trials[j] for j in b], str(out)) for (i, gpu), b in zip(slots, blocks) if len(b)]
        for f in futures:
            f.result()
    for s in summarize(out):
        print(s["scene"], s["station_id"], s["n"], round(s["success_rate"], 3), s["status"])


if __name__ == "__main__":
    main()
