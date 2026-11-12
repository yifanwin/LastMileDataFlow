"""(d) Benchmark 构建：按已有标定的成功次数选困难 S0 和参考 S1，不需要 GPU。"""

import argparse
from pathlib import Path

from lastmile_dataflow.lastmile_benchmark.build import build
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="场景库 run 目录")
    parser.add_argument("--calibration", type=Path, required=True, help="cuRobo 标定 run 目录")
    parser.add_argument(
        "--config", type=Path, help="benchmark 配置 YAML，见 configs/lastmile_benchmark；省略时用 v0.1 默认值"
    )
    parser.add_argument("--output", type=Path, required=True, help="新 benchmark 目录")
    args = parser.parse_args()
    setup_logging()
    result = build(args.dataset, args.output, calibration_dir=args.calibration, config_path=args.config)
    print(result)


if __name__ == "__main__":
    main()
