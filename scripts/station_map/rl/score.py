"""(c) RL 站位图打分：用冻结的在线双 Critic 给真实站位的初始观测打分。"""

import argparse
import json
import os
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import REPO_ROOT, read_config

DEFAULT_CONFIG = REPO_ROOT / "configs/station_map/rl/val103_cup30.yaml"
SOFT_CONFIG = REPO_ROOT / "configs/station_map/rl/val103_cube_random_stations_facing.yaml"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="soft模式默认使用随机站位方块配置")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--gpu", type=int, required=True, help="物理 GPU 编号")
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, help="q模式取配置evaluation.seed；soft模式默认20261109")
    parser.add_argument("--value-mode", choices=("q", "soft"), default="q")
    parser.add_argument("--action-samples", type=int, default=128, help="soft模式每站动作样本数")
    parser.add_argument("--batch-size", type=int, default=2048, help="soft模式单GPU推理批量")
    parser.add_argument("--plot", type=Path, help="soft模式热图路径，默认位于output中")
    args = parser.parse_args()
    if args.value_mode == "soft":
        os.environ["MUJOCO_GL"] = "disable"
    select_devices([args.gpu])
    # torch和mjlab读取设备绑定；相关模块必须在select_devices之后导入。
    if args.value_mode == "soft":
        from lastmile_dataflow.station_map.rl.soft_value import score_soft

        config = read_config(args.config or SOFT_CONFIG)
        result = score_soft(
            config, checkpoint=args.checkpoint, gpu=args.gpu,
            output=args.output, samples=args.action_samples, batch_size=args.batch_size,
            seed=20261109 if args.seed is None else args.seed, plot=args.plot,
        )
    else:
        from lastmile_dataflow.station_map.rl.evaluation import score

        config = read_config(args.config or DEFAULT_CONFIG)
        result = score(config, checkpoint=args.checkpoint, gpu=args.gpu, output=args.output, seed=args.seed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
