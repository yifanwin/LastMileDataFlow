"""Robust physics survivors -> symmetry-canonical FPS library for RL plus a robot-facing native sweep pool."""

import argparse
import math
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.grasp_generation import axis_rotation, canonical_grasp, grasp_azimuth
from lastmile_dataflow.operation.grasps import select_grasps
from lastmile_dataflow.utils.io import create_run, write_json


def kinds(poses, up):
    down = -(poses[:, :3, 2] @ up)
    return np.where(down > 0.9, "top", np.where(down > 0.34, "tilted", "side"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--physics", type=Path, required=True)
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--min-robustness", type=float, default=1.0, help="fraction of nominal+perturbed picks")
    parser.add_argument("--position-scale-m", type=float, default=0.05)
    parser.add_argument("--rotation-scale-rad", type=float, default=math.pi / 2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--face-xy", type=float, nargs=2, help="world XY the native pool's grasps should face")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = create_run(args.output, {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})
    geometry = dict(np.load(args.geometry / "grasps.npz"))
    physics = dict(np.load(args.physics / "physics.npz"))
    rows = physics["rows"]
    robustness = physics["success"].mean(1)
    keep = rows[(robustness >= args.min_robustness - 1e-9) & physics["success"][:, 0]]
    symmetric = bool(geometry.get("axisymmetric", False))
    axis = geometry["symmetry_axis_local"] if symmetric else None
    centre = geometry["symmetry_center_local"] if symmetric else None
    local = geometry["transforms"][keep]
    canonical = np.array([canonical_grasp(p, axis, centre) for p in local]).reshape(-1, 4, 4)
    candidates = [dict(row=int(r)) for r in keep]
    pool = select_grasps(candidates, canonical, args.count, args.position_scale_m, args.rotation_scale_rad,
                         seed=args.seed)
    index = np.array([candidates.index(c) for c in pool], dtype=int)
    chosen, chosen_rows = canonical[index], keep[index]
    up = geometry["symmetry_axis_local"]
    robust_by_row = dict(zip(rows.tolist(), robustness.tolist()))
    extra = {k: geometry[k] for k in ("axisymmetric", "symmetry_axis_local", "symmetry_center_local",
                                      "symmetry_deviation_m") if k in geometry}
    np.savez(output / "grasps.npz", transforms=chosen, source_rows=chosen_rows,
             source=str((args.geometry / "grasps.npz").resolve()),
             robustness=np.array([robust_by_row[r] for r in chosen_rows]),
             approach_clear_m=geometry["approach_clear_m"][chosen_rows], kind=kinds(chosen, up), **extra)
    native = chosen.copy()
    if symmetric and args.face_xy is not None:
        object_world = geometry["object_world"]
        toward = np.linalg.inv(object_world[:3, :3]) @ np.r_[np.asarray(args.face_xy) - object_world[:2, 3], 0.0]
        goal = grasp_azimuth(np.r_[np.c_[np.eye(3), centre + toward], [[0, 0, 0, 1]]], axis, centre)
        native = np.array([axis_rotation(axis, centre, goal - grasp_azimuth(p, axis, centre)) @ p for p in chosen])
    np.savez(output / "grasps-native.npz", object_to_tcp=native, grasp_id=chosen_rows, kind=kinds(native, up))
    summary = dict(physics_grasps=len(rows), robust=len(keep), selected=len(chosen), symmetric=symmetric,
                   selected_rows=chosen_rows.tolist(), kinds=dict(zip(*np.unique(kinds(chosen, up), return_counts=True))),
                   survivor_kinds=dict(zip(*np.unique(kinds(canonical, up), return_counts=True))),
                   native_faces_xy=args.face_xy)
    summary = {k: ({a: int(b) for a, b in v.items()} if isinstance(v, dict) else v) for k, v in summary.items()}
    write_json(output / "summary.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
