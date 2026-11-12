"""(e) Benchmark 评测：用 MolmoBot 或 π0.5 闭环评测已构建的 benchmark。"""

import argparse
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--benchmark", type=Path, required=True, help="bench_build.py 的输出目录")
    cli.add_argument("--config", type=Path, required=True, help="策略配置 JSON，见 configs/policies")
    cli.add_argument("--output", type=Path, required=True, help="新评测目录")
    cli.add_argument("--gpu", type=int, required=True)
    cli.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = cli.parse_args()
    from lastmile_dataflow.gpu import select_devices

    select_devices([args.gpu], args.egl_devices)
    from lastmile_dataflow.utils.logging_config import setup_logging
    from lastmile_dataflow.lastmile_benchmark.evaluate import evaluate

    setup_logging()
    print(evaluate(args.benchmark, args.output, args.config))


if __name__ == "__main__":
    main()
