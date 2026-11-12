"""(e) Benchmark 评测：用 MolmoBot 或 π0.5 闭环评测已构建的 benchmark。"""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True, help="scripts/lastmile_benchmark/build.py 的输出目录")
    parser.add_argument("--config", type=Path, required=True, help="策略配置 YAML，见 configs/policies")
    parser.add_argument("--output", type=Path, required=True, help="新评测目录")
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = parser.parse_args()
    select_devices([args.gpu], args.egl_devices)
    # 导入 MuJoCo/torch 时会读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，相关模块必须在绑定设备之后导入
    from lastmile_dataflow.lastmile_benchmark.evaluate import evaluate

    setup_logging()
    result = evaluate(args.benchmark, args.output, args.config)
    print(result)


if __name__ == "__main__":
    main()
