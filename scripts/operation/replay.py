"""离线回放：按记录的真实状态重新渲染一个 rollout 的视频，不重新执行控制和物理。"""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rollout", type=Path, required=True, help="rollouts/<id> 目录（含 recording.json、trajectory.npz）"
    )
    parser.add_argument("--output", type=Path, required=True, help="新输出目录，写 <camera>.mp4 和 replay.json")
    parser.add_argument("--camera", required=True, help="录制时的相机名，例如 head_camera")
    parser.add_argument("--fps", type=float, help="输出帧率，默认取录制控制频率")
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = parser.parse_args()
    select_devices([args.gpu], args.egl_devices)
    # 导入 MuJoCo/torch 时会读取 CUDA_VISIBLE_DEVICES、MUJOCO_EGL_DEVICE_ID，相关模块必须在绑定设备之后导入
    from lastmile_dataflow.operation.recording.replay import render_rollout

    result = render_rollout(args.rollout, args.output, camera=args.camera, fps=args.fps)
    print(result)


if __name__ == "__main__":
    main()
