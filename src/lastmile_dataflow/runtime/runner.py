"""阶段一编排：原始/导入/快照来源 → 初始化 → 连续执行 → 完整落盘。"""
import shutil
from pathlib import Path

from ..config import freeze_config
from ..io import file_digest, read_json, write_json
from ..recording.recorder import AttemptRecorder
from ..robots.action import InvalidAction
from ..scenes.initialization import initialize_robot
from ..validation.lightweight import independent_results, inspect_state
from .simulation import InitializationError, Simulation


def run_attempt(source, robot_config, task, collection, *, actions=None, base=None,
                restoration=None, frozen_dir=None, attempt_id=None, strategy="short_action"):
    recorder = AttemptRecorder(collection.output_dir, freeze_config(robot_config, task, collection),
                               attempt_id=attempt_id, strategy=strategy,
                               parent=str(Path(frozen_dir).resolve()) if frozen_dir else None)
    sim = None
    stage = "load"
    results = independent_results()
    status, reason = "infrastructure_error", "unexpected_error"
    extra = {}
    warning_count = 0
    try:
        recorder.metadata["task_id"] = task.task_id
        from dataclasses import asdict
        write_json(recorder.path / "source.json", {
            "source": asdict(source) if source else None,
            "frozen_dir": str(Path(frozen_dir).resolve()) if frozen_dir else None,
            "target": task.target, "restoration": restoration})
        if frozen_dir:
            sim = Simulation.from_snapshot(frozen_dir, robot_config, target=task.target)
        else:
            sim = Simulation.from_source(source, robot_config, target=task.target, restoration=restoration)
        recorder.event("loaded", scene_id=source.scene_id if source else "snapshot",
                       nq=sim.model.nq, ngeom=sim.model.ngeom)
        stage = "initialize"
        if frozen_dir or restoration:
            check = inspect_state(sim, collection)
            initialization = {"status": "valid" if check["valid"] else "invalid", "check": check,
                              "method": "snapshot" if frozen_dir else "legacy_restoration"}
        else:
            initialization = initialize_robot(sim, collection, base=base)
        write_json(recorder.path / "initialization.json", initialization)
        # 即使初始化无效，仍保存可复查现场。
        stage = "freeze"
        if frozen_dir:
            shutil.copytree(frozen_dir, recorder.path / "scene")
            version = read_json(recorder.path / "scene" / "version.json")
        else:
            version = sim.freeze(recorder.path / "scene")
        recorder.metadata.update(scene_version=version["version_id"],
                                 task_id=task.task_id, initial_base=sim.robot.group("base").tolist())
        write_json(recorder.path / "attempt.json", recorder.metadata)
        write_json(recorder.path / "initial_state.json", sim.observe_state())
        if initialization["status"] != "valid":
            results["scene_validity"] = {"status": "invalid", "reason": "initialization_rejected"}
            status, reason = "invalid_initialization", "initialization_rejected"
        else:
            results["scene_validity"] = {"status": "valid", "reason": "lightweight_initial_checks",
                                          "scope": "load_finite_state_and_penetration_only"}
            stage = "render"
            initial_frames = sim.render(collection.width, collection.height)
            recorder.observation(initial_frames, step=0, time_s=float(sim.data.time))
            initial_base = sim.robot.group("base").copy()
            sim.begin()
            stage = "execute"
            status, reason = "execution_complete", "action_budget_completed"
            stream = iter(actions) if actions is not None else None
            for index in range(collection.max_steps):
                if stream is not None:
                    try:
                        raw = next(stream)
                    except StopIteration:
                        reason = "action_stream_exhausted"
                        break
                else:
                    raw = sim.robot.neutral_action()
                    # 非抓取短动作：小幅左臂关节增量，保留夹爪和躯干绝对值。
                    raw[3] = .005 if index % 2 == 0 else -.005
                before = sim.observe_state()
                checks = []

                def substep_check():
                    value = inspect_state(sim, collection)
                    if value["issues"]:
                        checks.append({"time_s": float(sim.data.time), **value})
                    return value

                try:
                    command, stop = sim.step(raw, fixed_base=task.fixed_base, substep_check=substep_check)
                except InvalidAction as exc:
                    recorder.event("action_rejected", step=index, error=str(exc), raw_action_repr=repr(raw))
                    status, reason = "invalid_control", "action_validation_failed"
                    extra["error"] = str(exc)
                    break
                check = stop or inspect_state(sim, collection)
                check["substep_issues"] = checks
                warning_count += sum(i["severity"] == "warning" for c in checks for i in c["issues"])
                after = sim.observe_state()
                # 先落盘动作/状态；渲染或编码出错不会抹去已发生的运动。
                recorder.record_step(raw_action=command["submitted_action"], command=command,
                                     before=before, after=after, check=check,
                                     observations={"index_step": index + 1, "manifest": "observations.json"})
                stage = "render"
                frames = sim.render(collection.width, collection.height)
                refs = recorder.observation(frames, step=index + 1, time_s=float(sim.data.time))
                recorder.event("step_observations", step=index, images=refs)
                if index == 0:
                    recorder.video_frame(initial_frames, before["time_s"])
                recorder.video_frame(frames, after["time_s"])
                stage = "execute"
                if not check["valid"]:
                    results["scene_validity"] = {"status": "invalid", "reason": "severe_execution_anomaly"}
                    status, reason = "execution_failed", "severe_physics_anomaly"
                    break
                if task.fixed_base:
                    import numpy as np
                    drift = np.abs(sim.robot.group("base") - initial_base)
                    if np.linalg.norm(drift[:2]) > .02 or drift[2] > .05:
                        recorder.event("fixed_base_protocol_invalid", measured_drift=drift.tolist())
                        status, reason = "protocol_invalid", "fixed_base_measured_motion"
                        break
            extra["warning_count"] = warning_count
        if sim is not None:
            sim.save_snapshot(recorder.path / "final_snapshot.npz")
            write_json(recorder.path / "final_state.json", sim.observe_state())
    except Exception as exc:
        recorder.event("exception", stage=stage, error_type=type(exc).__name__, message=str(exc))
        status = "invalid_initialization" if isinstance(exc, InitializationError) or stage == "initialize" else "load_error" if stage == "load" else "infrastructure_error"
        reason = f"{stage}_error"
        extra.update(error_type=type(exc).__name__, error=str(exc), stage=stage)
        # 异常也尽量保留现场。不把基础设施错误写成机器人任务失败。
        if sim is not None and sim.model_hash is not None:
            try:
                if not (recorder.path / "final_snapshot.npz").exists():
                    sim.save_snapshot(recorder.path / "final_snapshot.npz")
            except Exception as snapshot_error:
                extra["snapshot_error"] = str(snapshot_error)
    finally:
        if sim is not None:
            sim.close()
    recorder.finish(status, reason, results, extra=extra)
    return recorder.path


def audit_attempt(path):
    """轻量一致性校验，后续质量/发布层可复用；不会产生成功标签。"""
    path = Path(path)
    manifest = read_json(path / "artifacts.json")
    issues = []
    required = {"attempt.json", "config.json", "source.json", "result.json", "trajectory.jsonl",
                "events.jsonl", "observations.json", "videos.json"}
    files = manifest["files_sha256"]
    if not required.issubset(files):
        issues.append(f"missing required evidence: {sorted(required - set(files))}")
    actual = {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() and p.name != "artifacts.json"}
    if actual != set(files):
        issues.append("artifact manifest does not cover all files")
    for name, checksum in manifest["files_sha256"].items():
        file = path / name
        if not file.resolve().is_relative_to(path.resolve()):
            issues.append(f"unsafe artifact path: {name}")
        elif not file.is_file() or file_digest(file) != checksum:
            issues.append(f"missing or modified artifact: {name}")
    rows = [read_json_line(line) for line in (path / "trajectory.jsonl").read_text().splitlines()]
    result = read_json(path / "result.json")
    attempt = read_json(path / "attempt.json")
    from ..io import digest
    if digest(read_json(path / "config.json")) != attempt["config_sha256"]:
        issues.append("configuration digest mismatch")
    if attempt["executed_steps"] != result["executed_steps"] or attempt["status"] != result["status"]:
        issues.append("attempt/result summary mismatch")
    if result["executed_steps"] != len(rows):
        issues.append("trajectory step count mismatch")
    videos = read_json(path / "videos.json")
    if not rows and videos["cameras"]:
        issues.append("execution video without execution")
    for camera, video in videos["cameras"].items():
        if video["frame_count"] != len(video["frame_times_s"]) or video["frame_count"] != len(rows) + 1:
            issues.append(f"video frame count mismatch: {camera}")
        if video["path"] not in files:
            issues.append(f"video absent from manifest: {camera}")
    previous_time = None
    for index, row in enumerate(rows):
        if row["step"] != index or len(row["command"]["submitted_action"]) != 20:
            issues.append(f"invalid trajectory row {index}")
        before, after = row["state_before"]["time_s"], row["state_after"]["time_s"]
        if after <= before or previous_time is not None and abs(before - previous_time) > 1e-9:
            issues.append(f"noncontinuous simulation time at row {index}")
        previous_time = after
    if result["results"]["task_completion"]["status"] == "success":
        issues.append("phase 1 cannot label task success")
    return {"valid": not issues, "issues": issues, "executed_steps": len(rows)}


def read_json_line(line):
    import json
    return json.loads(line)
