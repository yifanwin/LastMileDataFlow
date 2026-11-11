"""(c) 规划器站位图：对场景库逐站位执行 cuRobo 标定，保存实测成功率和热图。"""
import argparse
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--dataset', type=Path, required=True, help='场景库 run 目录')
    cli.add_argument('--config', type=Path, required=True,
                     help='逐站位运行配置 JSON（执行配置、记录开关、进程数），见 configs/collection')
    cli.add_argument('--output', type=Path, required=True, help='新 run 目录')
    cli.add_argument('--samples', nargs='+', help='只运行这些场景')
    cli.add_argument('--gpu', type=int, nargs='+', required=True)
    cli.add_argument('--egl-devices', type=int, nargs='+', help='与 --gpu 一一对应的 EGL 设备号')
    args = cli.parse_args()
    from lastmile_dataflow.gpu import select_devices
    select_devices(args.gpu, args.egl_devices)
    from lastmile_dataflow.utils.logging_config import setup_logging
    from lastmile_dataflow.workflows.collect import run_stations
    setup_logging()
    print(run_stations(args.dataset, args.config, args.output, gpu_ids=args.gpu, collect=False,
                       samples=args.samples, egl_devices=args.egl_devices))


if __name__ == '__main__':
    main()
