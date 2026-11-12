"""(c) RL 站位图打分：用冻结的在线双 Critic 给真实站位的初始观测打分。"""

import argparse
import json
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import REPO_ROOT, read_config

DEFAULT_CONFIG = REPO_ROOT / "configs/station_map/rl/val103_cup30.yaml"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--gpu", type=int, required=True, help="物理 GPU 编号")
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, help="默认取配置的 evaluation.seed")
    args = parser.parse_args()
    config = read_config(args.config)
    select_devices([args.gpu])
    # torch、Warp、mjlab 在导入时读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，训练代码必须在绑定设备之后导入
    from lastmile_dataflow.station_map.rl.evaluation import score

    result = score(config, checkpoint=args.checkpoint, gpu=args.gpu, output=args.output, seed=args.seed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
