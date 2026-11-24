"""GraspDataGen-style finger-pad placement grasps for a scene target, checked against the target and fixed scene."""

import argparse
import logging
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.grasp_generation import build_scene, generate_candidates, target_symmetry
from lastmile_dataflow.utils.io import create_run, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--robot-config", type=Path, default=Path("configs/robots/panda_omron.yaml"))
    parser.add_argument("--points", type=int, default=1000)
    parser.add_argument("--orientations", type=int, default=8)
    parser.add_argument("--standoff-m", type=float, default=0.002)
    parser.add_argument("--resolution", type=int, default=5, help="pad rays per side")
    parser.add_argument("--min-hit-ratio", type=float, default=0.3)
    parser.add_argument("--clearance-m", type=float, default=0.002)
    parser.add_argument("--corridor-max-m", type=float, default=0.10)
    parser.add_argument("--corridor-step-m", type=float, default=0.005)
    parser.add_argument("--symmetry-tolerance-m", type=float, default=0.003)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    output = create_run(args.output, config)
    scene = build_scene(args.scene.absolute(), args.metadata.absolute(), args.target, args.robot_config,
                        resolution=args.resolution)
    candidates, counts = generate_candidates(
        scene, points=args.points, orientations=args.orientations, standoff_m=args.standoff_m,
        min_hit_ratio=args.min_hit_ratio, clearance_m=args.clearance_m,
        corridor_max_m=args.corridor_max_m, corridor_step_m=args.corridor_step_m, seed=args.seed)
    world = np.array([c.pose_world for c in candidates]).reshape(-1, 4, 4)
    local = np.linalg.inv(scene.object_world) @ world
    symmetry = target_symmetry(scene, args.symmetry_tolerance_m)
    clear = np.array([c.approach_clear_m for c in candidates])
    np.savez(output / "grasps.npz", transforms=local, world_transforms=world, source_rows=np.arange(len(local)),
             hit_ratio=np.array([c.hit_ratio for c in candidates]), approach_clear_m=clear,
             surface_point=np.array([c.point for c in candidates]), surface_normal=np.array([c.normal for c in candidates]),
             object_world=scene.object_world, table_z=scene.table_z, **symmetry)
    summary = dict(target_body=scene.model.body(scene.target).name, counts=counts, accepted=len(candidates),
                   approach_clear_m_quantiles=dict(zip(("0", "25", "50", "75", "100"),
                                                       np.percentile(clear, [0, 25, 50, 75, 100]).round(3).tolist()))
                   if len(clear) else None,
                   approach_clear_full=int((clear >= args.corridor_max_m - 1e-9).sum()),
                   **{k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in symmetry.items()},
                   frame="target_body_local TCP (gripper0_right_grip_site), +z approach",
                   scope="geometry only: open hand vs target and fixed scene; no dynamics")
    write_json(output / "summary.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
