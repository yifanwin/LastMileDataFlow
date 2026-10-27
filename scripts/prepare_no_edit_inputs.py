"""Prepare one-shot no-edit calibration inputs from a recipe (no calibration)."""
import argparse
import logging
from pathlib import Path

from lastmile_dataflow.workflows.scene_datasets import prepare_scene_inputs


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description='准备无编辑标定输入')
    parser.add_argument('--recipe', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--build-workers', type=int, default=32)
    parser.add_argument('--gpu', nargs='+', type=int, default=[0])
    parser.add_argument('--exclude-categories', nargs='+', default=None)
    parser.add_argument('--house-timeout-s', type=float, default=1200.)
    args = parser.parse_args(argv)
    root = prepare_scene_inputs(args.recipe, args.output, gpu_ids=args.gpu,
                                workers=args.build_workers,
                                exclude_categories=args.exclude_categories,
                                house_timeout_s=args.house_timeout_s)
    print(root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
