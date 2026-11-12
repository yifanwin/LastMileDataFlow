"""(d) Benchmark 构建：按已有标定的成功次数选困难 S0 和参考 S1，不需要 GPU。"""
import argparse
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--dataset', type=Path, required=True, help='场景库 run 目录')
    cli.add_argument('--calibration', type=Path, required=True, help='cuRobo 标定 run 目录')
    cli.add_argument('--config', type=Path, help='benchmark 配置 YAML，见 configs/benchmark；省略时用 v0.1 默认值')
    cli.add_argument('--output', type=Path, required=True, help='新 benchmark 目录')
    args = cli.parse_args()
    from lastmile_dataflow.utils.logging_config import setup_logging
    from lastmile_dataflow.workflows.lastmilebench import build
    setup_logging()
    print(build(args.dataset, args.output, calibration_dir=args.calibration, config_path=args.config))


if __name__ == '__main__':
    main()
