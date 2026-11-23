"""Reload frozen pick variants, check shared stations and produce selection figures."""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robot", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--min-pixels", type=int, default=64)
    parser.add_argument("--min-visible-fraction", type=float, default=0.80)
    args = parser.parse_args()
    select_devices([args.gpu])
    # MuJoCo reads EGL selection on import.
    from lastmile_dataflow.station_map.sampling.robocasa_pick_review import review_pick_variants

    result = review_pick_variants(
        args.run, args.output, args.robot, min_pixels=args.min_pixels, min_fraction=args.min_visible_fraction,
    )
    print(result)


if __name__ == "__main__":
    main()
