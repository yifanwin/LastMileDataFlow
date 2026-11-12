"""Explicit real val103/mjlab stepping and scripted-control smoke.

This is separate from unittest. Scripted actions pass through the identical
7D/DLS/servo/physics path as the Actor, without planner prechecks.
"""

import argparse

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import REPO_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(REPO_ROOT / "configs/station_map/rl/val103_cup30.yaml"))
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--num-envs", type=int, default=1)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--upstream", action="store_true", help="run the shipped YAM lift-cube environment")
    parser.add_argument(
        "--scripted-batch", action="store_true", help="scripted pick from one sampled station per environment"
    )
    parser.add_argument("--station-seed", type=int, default=42)
    parser.add_argument("--policy-checkpoint", help="report grasp-sequence progress of a frozen Actor")
    parser.add_argument("--station-id")
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--random-actions", action="store_true")
    parser.add_argument("--learner-updates", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--monitor", action="store_true", help="validate independent evaluation and all four metric figures"
    )
    parser.add_argument("--output", default="/tmp/lastmile-rl-smoke.json")
    args = parser.parse_args()
    args.split = "train"
    if args.monitor and args.learner_updates <= 0:
        parser.error("--monitor requires --learner-updates > 0")
    select_devices([args.gpu])
    args.physical_gpu = str(args.gpu)
    # torch、Warp、mjlab 在导入时读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，检查代码必须在绑定设备之后导入
    from rl_checks import run

    run(args)


if __name__ == "__main__":
    main()
