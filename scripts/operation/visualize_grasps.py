"""Show a full-corpus FPS grasp pool on the target object's visual mesh."""

import argparse
import math
from pathlib import Path

from lastmile_dataflow.operation.grasps import DEFAULT_FPS_COUNT, DEFAULT_FPS_SEED
from lastmile_dataflow.operation.grasp_visualization import visualize_grasps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--robot-config", type=Path, required=True)
    parser.add_argument("--robot-scale", type=float, default=0.9)
    parser.add_argument("--grasps", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=DEFAULT_FPS_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_FPS_SEED)
    parser.add_argument("--position-scale-m", type=float, default=0.1)
    parser.add_argument("--rotation-scale-rad", type=float, default=math.pi / 2)
    parser.add_argument("--roll-rad", type=float, default=0.0)
    args = parser.parse_args()
    result = visualize_grasps(
        args.scene, args.metadata, args.target, args.grasps, args.output,
        count=args.count, seed=args.seed, position_scale_m=args.position_scale_m,
        rotation_scale_rad=args.rotation_scale_rad, roll_rad=args.roll_rad,
        robot_config=args.robot_config, robot_scale=args.robot_scale,
    )
    print(result)


if __name__ == "__main__":
    main()
