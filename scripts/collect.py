"""(d) Last-mile 数据集：复用标定（或先标定），执行 S0→S1→操作连续采集。"""

import argparse
from pathlib import Path

from lastmile_dataflow.config import add_station_run_arguments, station_run


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--dataset", type=Path, required=True, help="场景库 run 目录")
    cli.add_argument("--calibration", type=Path, help="复用的标定 run；省略时在本 run 内先标定")
    cli.add_argument("--output", type=Path, required=True, help="新 run 目录")
    cli.add_argument("--samples", nargs="+", help="只运行这些场景")
    cli.add_argument("--gpu", type=int, nargs="+", required=True)
    cli.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 一一对应的 EGL 设备号")
    add_station_run_arguments(cli)
    args = cli.parse_args()
    from lastmile_dataflow.gpu import select_devices

    select_devices(args.gpu, args.egl_devices)
    from lastmile_dataflow.utils.logging_config import setup_logging
    from lastmile_dataflow.workflows.collect import run_collection

    setup_logging()
    print(
        run_collection(
            args.dataset,
            station_run(args),
            args.output,
            gpu_ids=args.gpu,
            calibration=args.calibration,
            samples=args.samples,
        )
    )


if __name__ == "__main__":
    main()
