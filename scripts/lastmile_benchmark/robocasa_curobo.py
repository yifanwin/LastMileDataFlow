"""cuRobo and native tracking with either docking perturbations or one trial per grasp at each nominal station.

Example:
  python scripts/lastmile_benchmark/robocasa_curobo.py --scenes-root <runs/.../scenes> --station-table <station-table/.../stations.jsonl> \
    --scenes kettle-far kettle-left-125mm --grasps <grasps.npz> --output outputs/experiments/robocasa-station-eval/runs/<ts>-curobo \
    --gpus 3 4 --workers-per-gpu 4 --k 15
"""
import argparse
import collections
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.utils.gpu import bind_device, select_devices
from lastmile_dataflow.utils.io import append_row, create_run, rows, write_json, write_rows

ROOT = Path(__file__).resolve().parents[2]


def table_stations(table, station_ids):
    """Legal rows of a paired station table (scripts/scenes/robocasa_station_table.py), with their row index."""
    return [(i, r) for i, r in enumerate(rows(table)) if r["valid"] and (not station_ids or r["station_id"] in station_ids)]


def worker(slot, gpu, scenes_root, grasps, trials, output):
    bind_device(gpu)
    # Bind EGL before importing the MuJoCo planning bridge.
    from lastmile_dataflow.operation.robocasa_curobo_trial import CuroboWorker, run_trial

    out = Path(output)
    work = out / "work" / f"w{slot}"
    work.mkdir(parents=True, exist_ok=True)
    with np.load(grasps) as g:
        pool = g["object_to_tcp"]
        if trials[0].grasp_id is not None:
            rows_by_id = {int(grasp_id): i for i, grasp_id in enumerate(g["grasp_id"])}
            kinds = g["kind"]
    planner = CuroboWorker(ROOT, gpu, out / "logs" / f"curobo-w{slot}.log")
    runtime, scene = None, None
    try:
        for trial in trials:
            if trial.scene != scene:
                if runtime is not None:
                    runtime.close()
                runtime, scene = RoboCasaRuntime(Path(scenes_root) / trial.scene, arm_controller="joint_position"), trial.scene
            if trial.grasp_id is None:
                result = run_trial(runtime, planner, pool, trial, work)
            else:
                i = rows_by_id[trial.grasp_id]
                result = run_trial(runtime, planner, pool[i:i + 1], trial, work)
                result["grasp_kind"] = str(kinds[i])
            append_row(out / f"trials-w{slot}.jsonl", {**result, "gpu": gpu})
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenes-root", required=True)
    parser.add_argument("--station-table", required=True)
    parser.add_argument("--scenes", nargs="+", required=True)
    parser.add_argument("--grasps", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gpus", nargs="+", type=int, required=True)
    parser.add_argument("--workers-per-gpu", type=int, default=2)
    parser.add_argument("--k", type=int, default=15)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stations", nargs="*", default=[], help="station ids; default all shared legal stations")
    parser.add_argument("--no-docking-error", action="store_true", help="k=0 nominal docking only (smoke)")
    parser.add_argument("--per-grasp", nargs="*", type=int, metavar="ID",
                        help="test each grasp independently at nominal docking, optionally selecting IDs")
    a = parser.parse_args()
    select_devices(a.gpus)
    # The trial module imports MuJoCo through the planning bridge.
    from lastmile_dataflow.operation.robocasa_curobo_trial import DOCK_XY_M, DOCK_YAW_DEG, Trial, docking_errors

    stations = table_stations(a.station_table, set(a.stations))
    station_ids = [s["station_id"] for _, s in stations]
    criterion = {**PICK_PROTOCOL, "lift_reference": "lowest target collision vertex above the initial support geometry top"}
    config = {**vars(a), "criterion": criterion, "stations": station_ids,
              "docking_error": {"xy_m": DOCK_XY_M, "yaw_deg": DOCK_YAW_DEG, "frame": "world"},
              "arm_controller": "joint_position (native robosuite, absolute)"}
    sweep = a.per_grasp is not None
    if sweep:
        if not stations or set(a.stations) - set(station_ids):
            parser.error("--per-grasp requires valid station IDs")
        with np.load(a.grasps) as g:
            grasp_ids = [int(i) for i in g["grasp_id"]]
            if not grasp_ids or len(set(grasp_ids)) != len(grasp_ids):
                parser.error("the grasp pool must contain unique grasp IDs")
        selected = set(a.per_grasp)
        if selected - set(grasp_ids):
            parser.error(f"unknown grasp IDs: {sorted(selected - set(grasp_ids))}")
        config["per_grasp"] = [i for i in grasp_ids if not selected or i in selected]
        config["no_docking_error"] = True
        config["docking_error"] = {"xy_m": 0.0, "yaw_deg": 0.0, "frame": "world"}
    else:
        config.pop("per_grasp")

    trials = []
    for scene in a.scenes:
        for row, s in stations:
            if sweep:
                trials.extend(Trial(scene, s["station_id"], s["x"], s["y"], s["yaw"],
                                    0, 0.0, 0.0, 0.0, a.seed, grasp_id=i) for i in config["per_grasp"])
                continue
            errors = np.zeros((1, 3)) if a.no_docking_error else docking_errors(row, a.k, a.seed)
            for k, (dx, dy, dyaw) in enumerate(errors):
                trials.append(Trial(scene, s["station_id"], s["x"], s["y"], s["yaw"], k, float(dx), float(dy), float(dyaw), a.seed * 1000 + k))
    out = create_run(Path(a.output), config)
    (out / "logs").mkdir(exist_ok=True)
    slots = [(i, gpu) for i, gpu in enumerate(g for g in a.gpus for _ in range(a.workers_per_gpu))]
    if sweep:
        blocks = [trials[i::len(slots)] for i, _ in slots]
    else:
        blocks = [[trials[j] for j in block] for block in np.array_split(np.arange(len(trials)), len(slots))]
    write_json(out / "plan.json", {str(i): [trial.trial_id for trial in block] for (i, _), block in zip(slots, blocks)})
    print(f"trials {len(trials)} stations {len(stations)} workers {len(slots)}", flush=True)
    with ProcessPoolExecutor(len(slots), mp_context=multiprocessing.get_context("spawn")) as ex:
        futures = [ex.submit(worker, i, gpu, a.scenes_root, a.grasps, block, str(out))
                   for (i, gpu), block in zip(slots, blocks) if block]
        for f in futures:
            f.result()
    for s in summarize(out):
        print(s["scene"], s["station_id"], s["n"], round(s["success_rate"], 3), s["status"])


if __name__ == "__main__":
    main()
