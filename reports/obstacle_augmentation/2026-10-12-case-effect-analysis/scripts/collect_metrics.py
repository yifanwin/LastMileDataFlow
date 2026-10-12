#!/usr/bin/env python3
"""Collect all run / station / heatmap / E02 metrics for the 2026-10-12 analysis.

Read-only over the frozen `outputs/` trees in the shared checkout and the two
worktrees. Writes normalized CSV/JSON into ../data/.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path("/data0/wenyifan/MoMaTrajGen/LastMileDataFlow")
OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

NOEDIT = ROOT / ".worktrees/no-edit-lastmile-val/outputs/no_edit"
AUG = ROOT / ".worktrees/procedural-obstacle-augmentation/outputs/no_edit"
DIAG = ROOT / ".worktrees/no-edit-lastmile-val/outputs/diagnostics"

# run_id -> (backend, case, spacing_m, source-worktree label)
RUNS = {
    "no-edit-v080-reach-halfbase-val103-cup30-cuda5-20261011-1707": ("curobo", "baseline", 0.2217592080002628, "no-edit-lastmile-val"),
    "molmobot-val103-cup30-20261011-194653": ("vla", "baseline", 0.2217592080002628, "no-edit-lastmile-val"),
    "no-edit-curobo-case1-v000-20261011-222405": ("curobo", "case1", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-curobo-case2-v001-20261011-222602": ("curobo", "case2", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-molmobot-case1-v000-20261011-224025": ("vla", "case1", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-molmobot-case2-v001-20261011-230626": ("vla", "case2", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-final20-case1-v000-20261011-225943": ("curobo", "case1_final20", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-final20-case2-v001-20261011-225943": ("curobo", "case2_final20", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-molmobot-final20-case1-v000-20261011-232238": ("vla", "case1_final20", 0.1108796040001314, "procedural-obstacle-augmentation"),
    "no-edit-molmobot-final20-case2-v001-20261011-232238": ("vla", "case2_final20", 0.1108796040001314, "procedural-obstacle-augmentation"),
}


def run_path(run_id: str) -> Path:
    return (NOEDIT if "halfbase" in run_id or "194653" in run_id else AUG) / run_id


def task_dir(run_id: str) -> Path:
    return run_path(run_id) / "scenes/val_103/tasks/cup2"


# ---------------------------------------------------------------- run level
def run_rows():
    rows = []
    for run_id, (backend, case, spacing, wt) in RUNS.items():
        t = task_dir(run_id)
        row = {"run_id": run_id, "backend": backend, "case": case,
               "spacing_m": spacing, "worktree": wt}
        res = json.loads((t / "result.json").read_text())
        run_summary = json.loads((run_path(run_id) / "summary.json").read_text())
        timing = json.loads((run_path(run_id) / "timing.json").read_text())
        cfg = json.loads((run_path(run_id) / "frozen_config.json").read_text())["config"]
        row.update({
            "run_status": run_summary["status"],
            "task_status": res.get("status"),
            "dataset_eligible": res.get("dataset_eligible"),
            "successful_rollouts": res.get("successful_rollouts"),
            "wall_s": round(timing["elapsed_wall_s"], 1),
            "active_s": round(timing["elapsed_active_s"], 1),
            "radius_m": cfg["radius_m"],
            "trials_per_station": cfg["trials_per_station"],
            "max_attempts": cfg["max_attempts"],
            "obs_height": cfg["height"], "obs_width": cfg["width"],
            "seed": cfg["seed"],
            "base_mode": cfg["operation_base_mode"],
            "planner": cfg["planner_backend"],
        })
        st = json.loads((t / "station_statistics.json").read_text())
        term = sum(s["terminal_trials"] for s in st)
        succ = sum((s["successes"] or 0) for s in st)
        valid = sum(1 for s in st if s["geometry"] == "valid")
        row.update({
            "stations_total": len(st),
            "stations_valid": valid,
            "stations_geometry_filtered": len(st) - valid,
            "stations_measured": sum(1 for s in st if s["terminal_trials"] > 0),
            "terminal_trials": term, "successes": succ,
            "success_rate": (succ / term) if term else None,
        })
        reasons = Counter()
        for s in st:
            for k, v in (s.get("failure_reasons") or {}).items():
                reasons[k] += v
        row["failure_reasons"] = json.dumps(dict(reasons), ensure_ascii=False)
        for key in ("PlanningFailure", "PolicyFailure", "GraspSlip", "TrackingFailure"):
            row[f"fr_{key}"] = reasons.get(key, 0)
        try:
            tr = json.loads((t / "trials.json").read_text())
            tc = Counter(x.get("status") for x in tr)
            row.update({"trials_rows": len(tr),
                        "trial_success": tc.get("success", 0),
                        "trial_planning_no_solution": tc.get("planning_no_solution", 0),
                        "trial_failure": tc.get("failure", 0),
                        "trial_incomplete": tc.get("incomplete", 0)})
            steps = [x.get("executed_steps") for x in tr if x.get("executed_steps")]
            row["exec_steps_median"] = float(np.median(steps)) if steps else None
        except FileNotFoundError:
            row.update({"trials_rows": 0, "trial_success": 0,
                        "trial_planning_no_solution": 0, "trial_failure": 0,
                        "trial_incomplete": 0, "exec_steps_median": None})
        try:
            prog = json.loads((t / "progress.json").read_text())
            row["progress_expected"] = prog.get("expected_trials")
            row["progress_points"] = prog.get("execution_point_count")
        except FileNotFoundError:
            row["progress_expected"] = None
            row["progress_points"] = None
        rows.append(row)
    return rows


# ------------------------------------------------------------ station level
def station_rows():
    rows = []
    anchor = None
    for run_id, (backend, case, spacing, _) in RUNS.items():
        t = task_dir(run_id)
        task = json.loads((t / "task.json").read_text())
        anchor = task["anchor_world"]
        st = json.loads((t / "station_statistics.json").read_text())
        for s in st:
            x, y = s["xy"]
            vis = s.get("visibility") or {}
            rows.append({
                "run_id": run_id, "backend": backend, "case": case,
                "station_id": s["station_id"],
                "x": round(x, 4), "y": round(y, 4),
                "grid_i": s["grid_index"][0], "grid_j": s["grid_index"][1],
                "dist_to_target_m": round(math.hypot(x - anchor[0], y - anchor[1]), 4),
                "bearing_rad": round(math.atan2(y - anchor[1], x - anchor[0]), 4),
                "geometry": s["geometry"],
                "ground": s.get("ground"),
                "n_collisions": len(s.get("collisions") or []),
                "visible_pixels": vis.get("visible_pixels"),
                "successes": s["successes"], "terminal_trials": s["terminal_trials"],
                "success_rate": s["success_rate"],
                "base_yaw": round((s.get("base") or [0, 0, 0])[2], 4),
            })
    return rows, anchor


# ----------------------------------------------------------------- heatmaps
def heatmaps():
    out = {}
    for run_id in RUNS:
        t = task_dir(run_id)
        npz = np.load(t / "maps/success_heatmap.npz")
        out[run_id] = {
            "origin": npz["origin"].tolist(),
            "resolution_m": float(npz["resolution_m"]),
            "sigma_m": float(npz["sigma_m"]),
            "success_rate": npz["success_rate"],
            "free": npz["free"].astype(bool),
            "weighted_trials": npz["weighted_trials"],
        }
    return out


# ------------------------------------------------------------ case variants
def case_variants():
    rows = []
    for case, run_variant in (("case1", "dense-case1-five-20261011-r5-views"),
                              ("case2", "dense-case2-five-20261011-r5-views")):
        base = AUG.parent / "diagnostics" / run_variant / "variants"
        for vdir in sorted(base.glob("v*")):
            edits = json.loads((vdir / "edits.json").read_text())
            res = json.loads((vdir / "result.json").read_text())
            for e in edits:
                rows.append({
                    "case": case, "variant": vdir.name,
                    "asset_id": e["asset_id"], "category": e["category"], "kind": e["kind"],
                    "width_m": round(e["measured_size_m"]["width"], 3),
                    "depth_m": round(e["measured_size_m"]["depth"], 3),
                    "height_m": round(e["measured_size_m"]["height"], 3),
                    "support_height_m": e.get("support_height_m"),
                    "selected_points": res.get("final_station_count"),
                    "nav_free_before": res.get("navigation_free_cells_before"),
                    "nav_free_after": res.get("navigation_free_cells_after"),
                })
    return rows


# --------------------------------------------------------------------- E02
def e02_rows():
    rows = []
    d = DIAG / "molmobot-transfer-S0009-S0023-20261011-E02"
    for adir in sorted((d / "attempts/pick/S0009").glob("transfer-*")):
        traj = [json.loads(l) for l in (adir / "trajectory.jsonl").read_text().splitlines()]
        nav = json.loads((adir / "navigation.json").read_text())
        out = json.loads((adir / "outcome.json").read_text())
        final = json.loads((adir / "final_state.json").read_text())
        reqs = [json.loads(l) for l in (adir / "policy_requests.jsonl").read_text().splitlines()]
        phases = Counter(r["observations"].get("phase") for r in traj
                         if isinstance(r.get("observations"), dict))
        nav_steps = phases.get("navigation", 0)
        op = traj[nav_steps:]
        z = [r["state_after"]["target"]["pose_world_xyz_wxyz"][2] for r in op]
        base = [r["state_after"]["robot"]["base"] for r in traj]
        grip = [r["state_after"]["robot"]["right_gripper"][0] for r in op]
        inf = [q["inference_s"] for q in reqs if "inference_s" in q]
        tgt = out["attribution"]
        rows.append({
            "attempt": adir.name,
            "nav_status": nav["status"], "nav_steps": nav["steps"],
            "op_status": out["status"], "reason": out["reason"],
            "attribution": tgt.get("category") if isinstance(tgt, dict) else tgt,
            "policy_steps": len(op),
            "target_z_start_m": round(z[0], 4),
            "target_z_end_m": round(z[-1], 4),
            "max_lift_m": round(max(z) - z[0], 4),
            "base_x_span_m": round(max(b[0] for b in base) - min(b[0] for b in base), 4),
            "base_y_span_m": round(max(b[1] for b in base) - min(b[1] for b in base), 4),
            "base_yaw_span_rad": round(max(b[2] for b in base) - min(b[2] for b in base), 4),
            "gripper_min": round(min(grip), 4), "gripper_final": round(grip[-1], 4),
            "inference_s_mean": round(float(np.mean(inf)), 4),
            "inference_s_max": round(float(np.max(inf)), 4),
            "n_forbidden_collisions": len(tgt.get("collisions", [])) if isinstance(tgt, dict) else 0,
        })
    return rows


def main():
    runs = run_rows()
    with (OUT / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(runs[0].keys()))
        w.writeheader(); w.writerows(runs)

    stations, anchor = station_rows()
    with (OUT / "stations.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(stations[0].keys()))
        w.writeheader(); w.writerows(stations)

    variants = case_variants()
    with (OUT / "case_variants.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(variants[0].keys()))
        w.writeheader(); w.writerows(variants)

    e02 = e02_rows()
    with (OUT / "e02_trials.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(e02[0].keys()))
        w.writeheader(); w.writerows(e02)

    np.savez_compressed(OUT / "heatmaps.npz",
                        **{f"{k}::{field}": (v[field] if field != "success_rate" else np.nan_to_num(v[field], nan=-1.0))
                           for k, v in heatmaps().items()
                           for field in ("origin", "success_rate", "free")})
    (OUT / "anchor.json").write_text(json.dumps({"anchor_world": anchor}, indent=1))
    (OUT / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1))
    print(f"runs={len(runs)} stations={len(stations)} variants={len(variants)} e02={len(e02)}")
    print(f"anchor={anchor}")


if __name__ == "__main__":
    main()
