"""(c) RL 站位图训练：固定底盘 mjlab SAC，主进程 Learner，每张卡一个 Sampler（独立 RL 环境）。"""

import argparse
import json
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import REPO_ROOT, read_config

DEFAULT_CONFIG = REPO_ROOT / "configs/station_map/rl/val103_cup30.yaml"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--gpu",
        type=int,
        nargs="+",
        required=True,
        help="物理 GPU 编号，1–8 张互不相同；每张一个 Sampler，第一张同时放 Learner",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", help="从这个 checkpoint 继续训练")
    parser.add_argument("--max-transitions", type=int, help="覆盖配置的 training.max_transitions")
    parser.add_argument("--num-envs", type=int, help="覆盖配置的 environment.num_envs")
    args = parser.parse_args()
    if not 1 <= len(args.gpu) <= 8 or len(set(args.gpu)) != len(args.gpu):
        parser.error("--gpu requires 1–8 distinct devices")
    config = read_config(args.config)
    select_devices(args.gpu)
    # torch、Warp、mjlab 在导入时读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，训练代码必须在绑定设备之后导入
    from lastmile_dataflow.station_map.rl.training import train

    result = train(
        config,
        gpu=0,
        sampler_gpus=args.gpu,
        output=args.output,
        seed=args.seed,
        resume=args.resume,
        max_transitions=args.max_transitions,
        num_envs=args.num_envs,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
