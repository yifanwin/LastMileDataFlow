"""π0.5 val103-cup30：各正对目标的初始站位独立推理三次。"""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="新 run 目录")
    parser.add_argument("--gpu", type=int, nargs="+", required=True, help="物理 CUDA 卡号")
    parser.add_argument("--egl-devices", type=int, nargs="+")
    parser.add_argument("--scene-config", default="configs/station_map/rl/val103_cup30.yaml")
    parser.add_argument("--robot-config", default="configs/robots/panda_omron.yaml")
    parser.add_argument("--policy-config", default="configs/policies/pi05_panda_omron.yaml")
    parser.add_argument("--policy-python", type=Path, help="π0.5 环境的绝对解释器路径")
    parser.add_argument("--horizon", type=int, default=400)
    parser.add_argument("--attempt-timeout-s", type=float, default=300.0)
    parser.add_argument("--station-limit", type=int, help="仅调试：只运行前 N 个有效站位")
    parser.add_argument("--prepare-only", action="store_true", help="只准备场景和站位，不启动模型")
    parser.add_argument("--summarize", action="store_true", help="汇总已有 run，不执行回合")
    parser.add_argument("--worker-index", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    select_devices(args.gpu, args.egl_devices)
    # MuJoCo 在导入时读取设备绑定；策略子进程继承此处的物理卡及源码路径。
    from lastmile_dataflow.lastmile_benchmark.station_policy import run, run_worker, summarize

    setup_logging()
    if args.worker_index is not None:
        result = run_worker(args.output, gpu=args.gpu[0], worker_index=args.worker_index)
    elif args.summarize:
        result = summarize(args.output)
    else:
        result = run(
            args.output, gpus=args.gpu, scene_config=args.scene_config, robot_config=args.robot_config,
            policy_config=args.policy_config, policy_python=args.policy_python, horizon=args.horizon,
            attempt_timeout_s=args.attempt_timeout_s, station_limit=args.station_limit,
            prepare_only=args.prepare_only,
        )
    print(result)


if __name__ == "__main__":
    main()
