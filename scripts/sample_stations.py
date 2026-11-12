"""(b→c) 站位采样：对场景库逐场景采候选站位，结果写回场景库（stations.jsonl）；没有合法站位的场景在这一步淘汰。"""

import argparse
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--scenes", type=Path, required=True, help="build_scenes.py 输出的场景库 run 目录")
    cli.add_argument("--gpu", type=int, required=True, help="渲染目标可见性用的 GPU")
    cli.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = cli.parse_args()
    from lastmile_dataflow.gpu import select_devices

    select_devices([args.gpu], args.egl_devices)
    from lastmile_dataflow.station_map.sampling.build import sample_stations
    from lastmile_dataflow.utils.logging_config import setup_logging

    setup_logging()
    print(sample_stations(args.scenes))


if __name__ == "__main__":
    main()
