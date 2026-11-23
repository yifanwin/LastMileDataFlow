"""Make movable-obstacle scenes from one frozen RoboCasa pick instance."""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    args = parser.parse_args()
    select_devices([args.gpu])
    # MuJoCo reads EGL selection on import.
    from lastmile_dataflow.scenes.robocasa_pick_variants import build_pick_variants

    result = build_pick_variants(args.parent, args.config, args.output)
    print(result)


if __name__ == "__main__":
    main()
