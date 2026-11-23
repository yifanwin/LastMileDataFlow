"""(c) 站位质量图阶段三：冻结 Actor 采严格终局标签（collect），拟合五头质量网络并输出 Q_phys / U 图（fit）。"""

import argparse
import json
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import read_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="每个 (合法站位, 抓法, σ 分层) 跑 repeats 个严格回合")
    collect.add_argument("--checkpoint", type=Path, required=True)
    collect.add_argument("--gpu", type=int, nargs="+", required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--repeats", type=int, default=2)
    collect.add_argument("--num-envs", type=int, default=512)
    collect.add_argument("--seed", type=int, default=20261123)
    collect.add_argument("--stochastic", action="store_true", help="按训练时的采样动作执行（默认均值动作）")
    collect.add_argument("--no-sigma-input", action="store_true",
                         help="噪声照常施加，但观测里的 σ 置 0（用于只在 σ=0 下训练过的策略）")
    collect.add_argument("--heading-offsets-deg", type=float, nargs="+", help="覆盖 checkpoint 配置的站位航向，例如只采正对目标：0")
    fit = sub.add_parser("fit", help="按 XY 空间块划分，五个成员各自 bootstrap 拟合，输出校准表和两张图")
    fit.add_argument("--labels", type=Path, required=True, help="collect 的输出目录")
    fit.add_argument("--gpu", type=int, required=True)
    fit.add_argument("--output", type=Path, required=True)
    fit.add_argument("--members", type=int, default=5)
    fit.add_argument("--epochs", type=int, default=40)
    fit.add_argument("--block-m", type=float, default=0.3)
    fit.add_argument("--seed", type=int, default=20261123)
    args = parser.parse_args()
    select_devices(args.gpu if args.command == "collect" else [args.gpu])
    from lastmile_dataflow.station_map.rl import quality

    if args.command == "collect":
        saved = __import__("torch").load(args.checkpoint, map_location="cpu", weights_only=False)
        config = saved["metadata"]["config"]
        if args.no_sigma_input:
            config["observation"]["sigma_input"] = False
        if args.heading_offsets_deg is not None:
            config["stations"]["heading_offsets_deg"] = args.heading_offsets_deg
        result = quality.collect(config, checkpoint=args.checkpoint, gpus=args.gpu, output=args.output,
                                 repeats=args.repeats, seed=args.seed, num_envs=args.num_envs,
                                 deterministic=not args.stochastic)
    else:
        result = quality.fit(args.labels, output=args.output, members=args.members, epochs=args.epochs,
                             block_m=args.block_m, seed=args.seed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
