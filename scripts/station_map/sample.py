"""(b→c) 站位采样：对场景库逐场景采候选站位，结果写回场景库（stations.jsonl）；没有合法站位的场景在这一步淘汰。"""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", type=Path, required=True, help="scripts/scenes/build.py 输出的场景库 run 目录")
    parser.add_argument("--gpu", type=int, required=True, help="渲染目标可见性用的 GPU")
    parser.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = parser.parse_args()
    select_devices([args.gpu], args.egl_devices)
    # 导入 MuJoCo/torch 时会读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，相关模块必须在绑定设备之后导入
    from lastmile_dataflow.station_map.sampling.build import sample_stations

    setup_logging()
    result = sample_stations(args.scenes)
    print(result)


if __name__ == "__main__":
    main()
