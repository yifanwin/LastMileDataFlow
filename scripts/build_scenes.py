"""(a)(b) 场景构建：按配方找目标、准备原场景和编辑后场景，保存场景库（站位由 sample_stations.py 另采）。"""

import argparse
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, required=True, help="场景配方 JSON（含 build_workers）")
    cli.add_argument("--output", type=Path, required=True, help="新 run 目录，如 outputs/experiments/<单元>/runs/<run>")
    cli.add_argument("--gpu", type=int, nargs="+", required=True)
    cli.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = cli.parse_args()
    from lastmile_dataflow.gpu import select_devices

    select_devices(args.gpu, args.egl_devices)
    from lastmile_dataflow.utils.logging_config import setup_logging
    from lastmile_dataflow.workflows.scene_datasets import build_scenes

    setup_logging()
    print(build_scenes(args.config, args.output, gpu_ids=args.gpu))


if __name__ == "__main__":
    main()
