"""离线回放：按记录的真实状态重新渲染一个 rollout 的视频，不重新执行控制和物理。"""

import argparse
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--rollout", type=Path, required=True, help="rollouts/<id> 目录（含 recording.json、trajectory.npz）"
    )
    cli.add_argument("--output", type=Path, required=True, help="新输出目录，写 <camera>.mp4 和 replay.json")
    cli.add_argument("--camera", required=True, help="录制时的相机名，例如 head_camera")
    cli.add_argument("--fps", type=float, help="输出帧率，默认取录制控制频率")
    cli.add_argument("--gpu", type=int, required=True)
    cli.add_argument("--egl-devices", type=int, nargs="+", help="与 --gpu 对应的 EGL 设备号，默认自动查询")
    args = cli.parse_args()
    from lastmile_dataflow.utils.gpu import select_devices

    select_devices([args.gpu], args.egl_devices)
    from lastmile_dataflow.operation.recording.replay import render_rollout

    print(render_rollout(args.rollout, args.output, camera=args.camera, fps=args.fps))


if __name__ == "__main__":
    main()
