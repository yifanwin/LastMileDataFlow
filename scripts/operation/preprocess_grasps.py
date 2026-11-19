"""Filter object grasps by paired pad rays and palm clearance, then save at most 128 FPS poses."""

import argparse
import logging
import math
from pathlib import Path

from lastmile_dataflow.operation.grasp_preprocessing import preprocess_grasps
from lastmile_dataflow.operation.grasps import DEFAULT_PREPROCESSED_COUNT, DEFAULT_FPS_SEED


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--grasps", type=Path, required=True)
    parser.add_argument("--robot-config", type=Path, required=True)
    parser.add_argument("--robot-scale", type=float, default=0.9)
    parser.add_argument("--roll-rad", type=float, default=math.pi / 2)
    parser.add_argument("--resolution", type=int, default=21)
    parser.add_argument("--min-hit-ratio", type=float, default=0.3)
    parser.add_argument("--fps-count", type=int, default=DEFAULT_PREPROCESSED_COUNT)
    parser.add_argument("--fps-seed", type=int, default=DEFAULT_FPS_SEED)
    parser.add_argument("--position-scale-m", type=float, default=0.1)
    parser.add_argument("--rotation-scale-rad", type=float, default=math.pi / 2)
    parser.add_argument("--hand-clearance-m", type=float, default=0.003)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = preprocess_grasps(
        args.scene, args.metadata, args.target, args.grasps, args.output,
        robot_config=args.robot_config, robot_scale=args.robot_scale, roll_rad=args.roll_rad,
        resolution=args.resolution, min_hit_ratio=args.min_hit_ratio, fps_count=args.fps_count,
        fps_seed=args.fps_seed, position_scale_m=args.position_scale_m,
        rotation_scale_rad=args.rotation_scale_rad, hand_clearance_m=args.hand_clearance_m,
    )
    print(result)


if __name__ == "__main__":
    main()
