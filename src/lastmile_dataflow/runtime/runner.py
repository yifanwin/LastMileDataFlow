"""阶段一编排：原始/导入/快照来源 → 初始化 → 连续执行 → 完整落盘。

【阅读入口】如果你想理解“一次 attempt 从头到尾发生了什么”，先读这个文件。
它是阶段一的主角，也是阶段二/三 handoff 时复用的执行组件（workflows 复用它而不重写）。

【贯穿全函数的设计模式：阶段变量 `stage`】
  函数用 `stage = "load"/"initialize"/"freeze"/"render"/"execute"` 标记当前进行到哪一步。
  一旦抛异常，except 块根据 stage 把错误归类为 load_error / invalid_initialization /
  infrastructure_error —— 这就是“不把基础设施错误伪装成机器人任务失败”的实现方式。

【三个结论的初始值】independent_results() 给出 scene_validity / case_condition /
  task_completion 三个 unknown，只有确实发生时才会被改写。
"""
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
    """执行一次有界短动作 attempt，返回该 attempt 目录。

    参数要点：
      source      原始场景来源（否则用 frozen_dir 从快照恢复）
      actions     显式动作列表；为 None 时使用内置的小幅摆臂默认动作
      base        显式指定底盘初态 [x,y,yaw]；与快照/导入互斥
      frozen_dir  从冻结场景创建**独立新 attempt**（不是在同一轨迹里重置）
    """
    # 第 1 步：先建记录器。注意它立刻创建目录且目录已存在就报错——attempt 唯一、不覆盖。
    recorder = AttemptRecorder(collection.output_dir, freeze_config(robot_config, task, collection),
                               attempt_id=attempt_id, strategy=strategy,
                               parent=str(Path(frozen_dir).resolve()) if frozen_dir else None)
    sim = None
    stage = "load"
    results = independent_results()
    # 默认按最保守的方式收尾：基础设施错误 + 未知原因；只有真正跑通才改写。
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
        # 第 2 步：加载场景。快照路径会做完整摘要校验（见 Simulation.from_snapshot）。
        if frozen_dir:
            sim = Simulation.from_snapshot(frozen_dir, robot_config, target=task.target)
        else:
            sim = Simulation.from_source(source, robot_config, target=task.target, restoration=restoration)
        recorder.event("loaded", scene_id=source.scene_id if source else "snapshot",
                       nq=sim.model.nq, ngeom=sim.model.ngeom)
        # 第 3 步：初始化底盘。仅此处允许写 qpos；快照/导入来源则复检其状态是否合规。
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
            # 快照来源：直接拷贝冻结目录，不重新编译、不改变任何东西。
            shutil.copytree(frozen_dir, recorder.path / "scene")
            version = read_json(recorder.path / "scene" / "version.json")
        else:
            version = sim.freeze(recorder.path / "scene")
        recorder.metadata.update(scene_version=version["version_id"],
                                 task_id=task.task_id, initial_base=sim.robot.group("base").tolist())
        write_json(recorder.path / "attempt.json", recorder.metadata)
        write_json(recorder.path / "initial_state.json", sim.observe_state())
        if initialization["status"] != "valid":
            # 初态不合法：直接归类 invalid_initialization，不进入执行。
            results["scene_validity"] = {"status": "invalid", "reason": "initialization_rejected"}
            status, reason = "invalid_initialization", "initialization_rejected"
        else:
            results["scene_validity"] = {"status": "valid", "reason": "lightweight_initial_checks",
                                          "scope": "load_finite_state_and_penetration_only"}
            # 第 4 步：先记录初态三相机，再 begin() 进入不可逆的连续执行阶段。
            stage = "render"
            initial_frames = sim.render(collection.width, collection.height)
            recorder.observation(initial_frames, step=0, time_s=float(sim.data.time))
            initial_base = sim.robot.group("base").copy()
            sim.begin()
            # 第 5 步：逐控制步执行。默认乐观地假定跑到预算结束。
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
                    # 默认：保持夹爪/躯干绝对量，只让左臂小幅往复。用于产生有意义的短轨迹。
                    raw = sim.robot.neutral_action()
                    # 非抓取短动作：小幅左臂关节增量，保留夹爪和躯干绝对值。
                    raw[3] = .005 if index % 2 == 0 else -.005
                before = sim.observe_state()
                checks = []

                def substep_check():
                    """每个物理子步都检查；记录所有出现问题的时刻，返回是否继续。"""
                    value = inspect_state(sim, collection)
                    if value["issues"]:
                        checks.append({"time_s": float(sim.data.time), **value})
                    return value

                try:
                    command, stop = sim.step(raw, fixed_base=task.fixed_base, substep_check=substep_check)
                except InvalidAction as exc:
                    # 动作本身非法：归 invalid_control，保留原始动作的 repr 作为证据。
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
                    # 严重物理异常：这是真实的物理问题，标 execution_failed（不是设施错误）。
                    results["scene_validity"] = {"status": "invalid", "reason": "severe_execution_anomaly"}
                    status, reason = "execution_failed", "severe_physics_anomaly"
                    break
                if task.fixed_base:
                    # 固定底盘试验：实测底盘若真有明显移动，数据仍保留，但标 protocol_invalid。
                    import numpy as np
                    drift = np.abs(sim.robot.group("base") - initial_base)
                    if np.linalg.norm(drift[:2]) > .02 or drift[2] > .05:
                        recorder.event("fixed_base_protocol_invalid", measured_drift=drift.tolist())
                        status, reason = "protocol_invalid", "fixed_base_measured_motion"
                        break
            extra["warning_count"] = warning_count
        if sim is not None:
            # 无论正常结束还是提前 break，都保存终止现场。
            sim.save_snapshot(recorder.path / "final_snapshot.npz")
            write_json(recorder.path / "final_state.json", sim.observe_state())
    except Exception as exc:
        # 异常路径：按 stage 归类，并尽力保留现场；绝不把基础设施错误写成任务失败。
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
    # 收尾：写 result.json + 全文件摘要清单。此后该 attempt 目录即为不可变证据。
    recorder.finish(status, reason, results, extra=extra)
    return recorder.path


def audit_attempt(path):
    """轻量一致性校验，后续质量/发布层可复用；不会产生成功标签。

    它检查的是“这份记录自己跟自己是否自洽”，而不是“任务是否成功”：
      - artifacts.json 是否覆盖目录下全部文件，摘要是否匹配（防篡改）
      - attempt.json / result.json / trajectory 的步数与状态是否一致
      - 仿真时间是否严格连续（防“偷偷重置再续跑”）
      - 视频帧数是否 = 步数 + 1（防“没执行却生成视频”）
      - 阶段一绝不允许出现 task_completion=success
    """
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
