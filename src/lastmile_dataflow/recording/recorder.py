"""每个 attempt 独立目录；失败也保留；无执行不生成执行视频。

【核心职责】把一次试验的全部原始证据落盘，并且**只增不删**。所有阶段
（一/二/三）都复用这个类，因为它已经实现了工程级的三条纪律：

  1. attempt 目录独占：`exist_ok=False`，已存在直接报错 → 永不覆盖。
  2. 边跑边刷盘：trajectory/events 每写一行都 flush，崩溃也不会丢已发生的运动。
  3. 结束才生成全文件摘要清单 artifacts.json，成为不可篡改证据。

【文件一览】config.json / source.json / attempt.json / trajectory.jsonl / events.jsonl /
  observations/（图片）/ observations.json / videos/（mp4）/ videos.json /
  final_snapshot.npz / result.json / artifacts.json
"""
from datetime import datetime, timezone
from pathlib import Path
import uuid

import imageio.v2 as imageio
import numpy as np

from ..io import canonical, digest, file_digest, write_json


class AttemptRecorder:
    def __init__(self, root, config, *, attempt_id=None, strategy="short_action", parent=None):
        # 未指定 ID 时用“UTC 时间戳 + 随机后缀”，保证全局唯一且可排序。
        self.attempt_id = attempt_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
        # 防御路径穿越：ID 里不能出现 / 或 ..，否则会写到目录外面。
        if not self.attempt_id or Path(self.attempt_id).name != self.attempt_id or self.attempt_id in (".", ".."):
            raise ValueError("invalid attempt id")
        self.path = Path(root) / "attempts" / self.attempt_id
        self.path.mkdir(parents=True, exist_ok=False)   # 已存在就抛错：永不覆盖
        self.collection = config["collection"]
        # config_sha256 把“这次到底用了什么配置”钉死在记录里，审计时会重新计算比对。
        self.metadata = {"schema_version": "1.0", "attempt_id": self.attempt_id,
                         "created_at_utc": datetime.now(timezone.utc).isoformat(),
                         "strategy": strategy, "parent_attempt": parent,
                         "config_sha256": digest(config), "status": "preparing"}
        write_json(self.path / "config.json", config)
        write_json(self.path / "source.json", {"source": None})
        write_json(self.path / "attempt.json", self.metadata)
        self.trajectory = (self.path / "trajectory.jsonl").open("xb")
        self.events = (self.path / "events.jsonl").open("xb")
        self.writers = {}          # 每台相机一个视频写入器
        self.steps = 0
        self.frames = {}           # 每台相机的真实时间戳列表
        self.frame_index = []      # observations.json 的内容
        self.finished = False

    def event(self, code, **fields):
        """追加一条事件（动作拒绝、异常、观察引用、协议违规等）。立即 flush。"""
        self.events.write(canonical({"code": code, **fields}) + b"\n")
        self.events.flush()

    def observation(self, frames, *, step, time_s):
        """把三相机当前帧写成 PNG，并返回相对路径引用。

        step=0 是初态，step=n 是第 n 个控制步之后的状态；这个编号是审计视频/轨迹对应的依据。
        """
        directory = self.path / "observations" / f"{step:06d}"
        directory.mkdir(parents=True, exist_ok=False)
        refs = {}
        for camera, pixels in frames.items():
            file = directory / f"{camera}.png"
            imageio.imwrite(file, pixels)
            refs[camera] = str(file.relative_to(self.path))
        self.frame_index.append({"step": step, "time_s": time_s, "images": refs})
        return refs

    def record_step(self, *, raw_action, command, before, after, check, observations):
        """写一行轨迹：动作、完整实际 ctrl、前后状态、验证结果、图像引用。

        顺序很重要：先写轨迹再渲染。这样即使渲染崩溃，物理上已发生的运动也有据可查。
        """
        row = {"step": self.steps, "raw_action": raw_action, "command": command,
               "state_before": before, "state_after": after, "validation": check,
               "observations": observations}
        self.trajectory.write(canonical(row) + b"\n")
        self.trajectory.flush()
        self.steps += 1

    def video_frame(self, frames, time_s):
        """追加一帧到各相机 MP4；record_video=False 时是空操作（但 PNG 仍然保存）。"""
        if not self.collection["record_video"]:
            return
        for camera, pixels in frames.items():
            if camera not in self.writers:
                video_dir = self.path / "videos"
                video_dir.mkdir(exist_ok=True)
                self.writers[camera] = imageio.get_writer(
                    video_dir / f"{camera}.mp4", fps=self._video_fps(),
                    codec="libx264", macro_block_size=2, ffmpeg_log_level="error")
            self.writers[camera].append_data(pixels)
            self.frames.setdefault(camera, []).append(float(time_s))

    def _video_fps(self):
        """视频 FPS 取配置里的控制频率（不是物理频率）。"""
        from ..io import read_json
        return read_json(self.path / "config.json")["robot"]["control_hz"]

    def finish(self, status, reason, results, *, extra=None):
        """收尾：关闭视频、写三个独立结果、写全文件摘要清单。

        注意：如果视频关闭失败（ffmpeg 问题），状态被强制改成 infrastructure_error。
        这是“记录设施异常”与“机器人任务失败”严格区分的体现——
        视频编不出来绝不能算成机器人抓取失败。
        """
        if self.finished:
            raise RuntimeError("attempt already finalized")
        close_errors = []
        for camera, writer in self.writers.items():
            try:
                writer.close()
            except Exception as exc:
                close_errors.append({"camera": camera, "error_type": type(exc).__name__, "message": str(exc)})
        if close_errors:
            status, reason = "infrastructure_error", "video_finalize_failed"
        self.trajectory.close()
        self.events.close()
        self.metadata.update(status=status, termination_reason=reason, executed_steps=self.steps,
                             finished_at_utc=datetime.now(timezone.utc).isoformat())
        write_json(self.path / "attempt.json", self.metadata)
        # result.json 是“结论文件”：终止状态 + 三个独立结果 + 附加诊断。
        write_json(self.path / "result.json", {"schema_version": "1.0", "status": status,
                   "termination_reason": reason, "executed_steps": self.steps,
                   "results": results, "extra": extra or {}, "recording_errors": close_errors})
        write_json(self.path / "observations.json", self.frame_index)
        write_json(self.path / "videos.json", {
            "enabled": self.collection["record_video"],
            "cameras": {k: {"path": f"videos/{k}.mp4", "frame_times_s": v,
                             "frame_count": len(v), "fps": self._video_fps(),
                             "kind": "real_execution", "timing": "constant_fps_with_actual_frame_timestamps"}
                        for k, v in self.frames.items()}})
        # 最后生成摘要清单：之后任何文件被改动，audit 都能发现。
        files = {str(p.relative_to(self.path)): file_digest(p)
                 for p in sorted(self.path.rglob("*")) if p.is_file() and p.name != "artifacts.json"}
        write_json(self.path / "artifacts.json", {"schema_version": "1.0", "files_sha256": files})
        self.finished = True
        return self.path
