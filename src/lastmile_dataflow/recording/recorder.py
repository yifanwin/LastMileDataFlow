"""每个 attempt 独立目录；失败也保留；无执行不生成执行视频。"""
from datetime import datetime, timezone
from pathlib import Path
import uuid

import imageio.v2 as imageio
import numpy as np

from ..io import canonical, digest, file_digest, write_json


class AttemptRecorder:
    def __init__(self, root, config, *, attempt_id=None, strategy="short_action", parent=None):
        self.attempt_id = attempt_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
        if not self.attempt_id or Path(self.attempt_id).name != self.attempt_id or self.attempt_id in (".", ".."):
            raise ValueError("invalid attempt id")
        self.path = Path(root) / "attempts" / self.attempt_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.collection = config["collection"]
        self.metadata = {"schema_version": "1.0", "attempt_id": self.attempt_id,
                         "created_at_utc": datetime.now(timezone.utc).isoformat(),
                         "strategy": strategy, "parent_attempt": parent,
                         "config_sha256": digest(config), "status": "preparing"}
        write_json(self.path / "config.json", config)
        write_json(self.path / "source.json", {"source": None})
        write_json(self.path / "attempt.json", self.metadata)
        self.trajectory = (self.path / "trajectory.jsonl").open("xb")
        self.events = (self.path / "events.jsonl").open("xb")
        self.writers = {}
        self.steps = 0
        self.frames = {}
        self.frame_index = []
        self.finished = False

    def event(self, code, **fields):
        self.events.write(canonical({"code": code, **fields}) + b"\n")
        self.events.flush()

    def observation(self, frames, *, step, time_s):
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
        row = {"step": self.steps, "raw_action": raw_action, "command": command,
               "state_before": before, "state_after": after, "validation": check,
               "observations": observations}
        self.trajectory.write(canonical(row) + b"\n")
        self.trajectory.flush()
        self.steps += 1

    def video_frame(self, frames, time_s):
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
        from ..io import read_json
        return read_json(self.path / "config.json")["robot"]["control_hz"]

    def finish(self, status, reason, results, *, extra=None):
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
        files = {str(p.relative_to(self.path)): file_digest(p)
                 for p in sorted(self.path.rglob("*")) if p.is_file() and p.name != "artifacts.json"}
        write_json(self.path / "artifacts.json", {"schema_version": "1.0", "files_sha256": files})
        self.finished = True
        return self.path
