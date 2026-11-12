"""(a)(b) 场景构建：按配方找目标、准备原场景和编辑后场景，保存场景库（站位由 scripts/station_map/sample.py 另采）。"""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, required=True, help="场景配方 YAML，见 configs/scenes（含 build_workers）"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="新 run 目录，如 outputs/experiments/<单元>/runs/<run>"
    )
    parser.add_argument("--gpu", type=int, nargs="+", required=True)
    parser.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = parser.parse_args()
    select_devices(args.gpu, args.egl_devices)
    # 导入 MuJoCo/torch 时会读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，相关模块必须在绑定设备之后导入
    from lastmile_dataflow.scenes.build import build_scenes

    setup_logging()
    result = build_scenes(args.config, args.output, gpu_ids=args.gpu)
    print(result)


if __name__ == "__main__":
    main()
