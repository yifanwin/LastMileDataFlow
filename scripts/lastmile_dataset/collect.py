"""(d) Last-mile 数据集：复用标定（或先标定），执行 S0→S1→操作连续采集。"""

import argparse
from pathlib import Path

from lastmile_dataflow.station_map.config import add_station_run_arguments, station_run
from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="场景库 run 目录")
    parser.add_argument("--calibration", type=Path, help="复用的标定 run；省略时在本 run 内先标定")
    parser.add_argument("--output", type=Path, required=True, help="新 run 目录")
    parser.add_argument("--samples", nargs="+", help="只运行这些场景")
    parser.add_argument("--gpu", type=int, nargs="+", required=True)
    parser.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 一一对应的 EGL 设备号")
    add_station_run_arguments(parser)
    args = parser.parse_args()
    select_devices(args.gpu, args.egl_devices)
    # 导入 MuJoCo/torch 时会读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，相关模块必须在绑定设备之后导入
    from lastmile_dataflow.lastmile_dataset.collect import run_collection

    setup_logging()
    run = station_run(args)
    result = run_collection(
        args.dataset, run, args.output, gpu_ids=args.gpu, calibration=args.calibration, samples=args.samples
    )
    print(result)


if __name__ == "__main__":
    main()
