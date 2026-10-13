"""每个 attempt 独立目录；失败也保留；无执行不生成执行视频。

【核心职责】把一次试验的全部原始证据落盘，并且**只增不删**。所有阶段
（一/二/三）都复用这个类，因为它已经实现了工程级的三条纪律：

  1. attempt 目录独占：`exist_ok=False`，已存在直接报错 → 永不覆盖。
  2. 边跑边刷盘：trajectory/events 每写一行都 flush，崩溃也不会丢已发生的运动。
  3. 结束后原子提交 episode.json；未提交的目录不能视为完成。

【文件一览】config.json / source.json / attempt.json / trajectory.jsonl / events.jsonl /
  observations/（图片）/ observations.json / videos/（mp4）/ videos.json /
  final_snapshot.npz / result.json / episode.json
"""
from datetime import datetime, timezone
from pathlib import Path
import uuid

import imageio.v2 as imageio
import numpy as np

from ..io import canonical, digest, write_json
from ..runtime.episode_guard import recording_operation


class AttemptRecorder:
    def __init__(self, root, config, *, attempt_id=None, strategy="short_action", parent=None, attempt_group=()):
        # 未指定 ID 时用“UTC 时间戳 + 随机后缀”，保证全局唯一且可排序。
        self.attempt_id = attempt_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
        # 防御路径穿越：ID 里不能出现 / 或 ..，否则会写到目录外面。
        if not self.attempt_id or Path(self.attempt_id).name != self.attempt_id or self.attempt_id in (".", ".."):
            raise ValueError("invalid attempt id")
        if any(not part or Path(part).name != part or part in (".", "..") for part in attempt_group):
            raise ValueError("invalid attempt group")
        self.output_root = Path(root)
        self.path = self.output_root / "attempts"
        for part in attempt_group:
            self.path /= part
        self.path /= self.attempt_id
        self.writers = {}; self.trajectory = None; self.events = None; self.finished = False; self.steps = 0
        from ..runtime.episode_guard import track_recorder
        track_recorder(self)
        self.path.mkdir(parents=True, exist_ok=False)   # 已存在就抛错：永不覆盖
        self.collection = config["collection"]
        self.config = config
        self.canonical_recorder = None
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

    def abort_close(self):
        """Release uncommitted episode resources without editing its evidence."""
        if self.finished:
            return
        errors=[]
        canonical_streams = list(self.canonical_recorder.streams.values()) if self.canonical_recorder is not None else []
        for resource in [*self.writers.values(), self.trajectory, self.events, *canonical_streams]:
            if resource is None or getattr(resource, 'closed', False):
                continue
            try: resource.close()
            except Exception as exc: errors.append(exc)
        if errors:
            # Prefer fail-stop storage/GPU errors to a local encoder error.
            from ..runtime.episode_guard import classify_episode_exception
            raise next((e for e in errors if classify_episode_exception(e,'finalize')['failure_scope']=='shared'), errors[0])

    @recording_operation
    def bind_simulation(self, sim, *, width=None, height=None):
        """Attach live recording before begin(); never infer raw from legacy JSON."""
        if self.finished or self.canonical_recorder is not None or sim.started:
            raise RuntimeError('bind a fresh simulation exactly once before execution')
        if not self.collection.get('raw_first', True):
            self.event('raw_first_disabled', reason='explicit_collection_config')
            return
        from .canonical import CanonicalRecorder
        self.canonical_recorder = CanonicalRecorder(self, sim, self.config, width=width, height=height)
        sim.evidence_recorder = self.canonical_recorder

    @recording_operation
    def event(self, code, **fields):
        """追加一条事件（动作拒绝、异常、观察引用、协议违规等）。立即 flush。"""
        self.events.write(canonical({"code": code, **fields}) + b"\n")
        self.events.flush()
        if self.canonical_recorder is not None and not self.canonical_recorder.closed:
            self.canonical_recorder.event(code, **fields)

    @recording_operation
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

    @recording_operation
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

    @recording_operation
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

    @recording_operation
    def finish(self, status, reason, results, *, extra=None):
        """收尾：关闭记录、写独立结果、原子提交 episode。

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
                from ..runtime.episode_guard import classify_episode_exception
                close_errors.append({"camera": camera, "error_type": type(exc).__name__, "message": str(exc),
                                     "failure_scope":classify_episode_exception(exc,"finalize")["failure_scope"]})
        if close_errors:
            status, reason = "infrastructure_error", "video_finalize_failed"
        import os
        for stream in (self.trajectory, self.events):
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
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
                             "data_layer": "derived", "encoding": "lossy_libx264",
                             "raw_RGB_reference": "raw/observations.jsonl" if self.canonical_recorder is not None else None,
                             "producer_metadata": [str(p.name) for p in (self.path/'analysis_video.json', self.path/'delivery_video.json') if p.is_file()],
                             "kind": "real_execution", "timing": "constant_fps_with_actual_frame_timestamps"}
                        for k, v in self.frames.items()}})
        if self.canonical_recorder is not None:
            self.canonical_recorder.finish(status, reason, results, {**(extra or {}), 'recording_errors': close_errors})
        # A physical success can coexist with unusable recording. Preserve the
        # task verdict, but do not count its trial or admit it to training.
        if self.config.get('protocol', {}).get('lastmile_selection') and self.canonical_recorder is not None and not self.canonical_recorder.manifest['data_valid'] and status not in ('simulation_failure', 'infrastructure_error'):
            status, reason = 'recording_error', 'invalid_episode_data'
            self.metadata.update(status=status, termination_reason=reason)
            write_json(self.path / 'attempt.json', self.metadata)
            from ..io import read_json
            result = read_json(self.path / 'result.json')
            result.update(status=status, termination_reason=reason)
            write_json(self.path / 'result.json', result)
            outcome = read_json(self.path / 'canonical/outcomes.json')
            outcome.update(termination_status=status, termination_reason=reason)
            write_json(self.path / 'canonical/outcomes.json', outcome)
        from .protocol import code_identity
        canonical_record = self.canonical_recorder
        manifest = canonical_record.manifest if canonical_record is not None else {}
        cfg = self.config
        task = cfg.get('task', {})
        metrics = (extra or {}).get('trajectory_metrics', {})
        success = metrics.get('task_success')
        verdict = results.get('task_completion', {}).get('status')
        if success is None and verdict in ('success', 'failure') and status != 'simulation_failure':
            success = verdict == 'success'
        if canonical_record is not None and manifest.get('execution_status') != 'executed':
            success = None
        navigation = (extra or {}).get('navigation', {})
        required = ['config.json', 'attempt.json', 'result.json', 'trajectory.jsonl', 'events.jsonl',
                    'observations.json', 'videos.json']
        if canonical_record is not None:
            required += ['canonical/manifest.json', 'canonical/outcomes.json', 'canonical/frames.jsonl',
                         'canonical/executed-actions.jsonl', 'canonical/robot-state.jsonl',
                         'raw/commands.jsonl', 'raw/observations.jsonl', 'raw/state-index.jsonl',
                         'canonical/calibration.json']
            if not self.config.get('scene_dataset_binding'):
                required.append('raw/scene/model.mjb')
        station, goal = cfg.get('station'), cfg.get('goal')
        role = 'recovery' if cfg.get('path') else 'calibration'
        import re
        match = re.search(r'-t(\d+)-[0-9a-f]+$', self.attempt_id)
        trial = int(match[1]) if match else None
        logical = {'task_id': task.get('task_id'), 'role': role,
                   'station': station, 'goal': goal, 'trial': trial}
        write_json(self.path / 'episode.json', {
            'schema_version': 'research-episode-v1', 'status': 'complete',
            'episode_id': self.attempt_id, 'task_id': task.get('task_id'),
            'scene_id': manifest.get('scene_id'), 'role': role,
            'trial_id': digest(logical), 'trial': trial, 'retry_id': self.attempt_id,
            'station_id': station.get('station_id') if station else None,
            's0': station if role == 'recovery' else None, 's1': goal if role == 'recovery' else None,
            'seed': cfg.get('seed'), 'execution_status': manifest.get('execution_status', 'executed' if self.steps else 'not_executed'),
            'termination_status': status, 'termination_reason': reason,
            'failure_stage': (extra or {}).get('phase'),
            'navigation_success': True if navigation.get('status') == 'success' else False if navigation.get('status') == 'failure' else None,
            'task_success': success, 'simulation_valid': metrics.get('simulation_valid'),
            'recording_complete': bool(canonical_record is not None and manifest.get('recording_complete') and not close_errors),
            'data_valid': bool(manifest.get('data_valid')),
            'continuous_simulation': (extra or {}).get('continuous_simulation', True if canonical_record is not None else None),
            'executed_steps': manifest.get('executed_steps', self.steps),
            'compiled_model_sha256': manifest.get('compiled_model_sha256'),
            'config_sha256': self.metadata['config_sha256'], 'code': code_identity(),
            'scene_dataset_binding': cfg.get('scene_dataset_binding'),
            'trajectory_ref': 'canonical/executed-actions.jsonl' if canonical_record is not None else 'trajectory.jsonl',
            'required_files': required})
        self.finished = True
        return self.path
