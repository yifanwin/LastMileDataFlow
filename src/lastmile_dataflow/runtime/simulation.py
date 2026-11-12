"""MuJoCo stepping and snapshots shared by construction and execution.

Preparation may initialize or restore state. After begin(), motion uses step()
only; an independent trial requires a new Simulation. Snapshots retain clock
remainder and fixed head targets as well as the physical state.
"""

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from shutil import copyfile
import copy
import json

import mujoco
import numpy as np

from lastmile_dataflow.robots.registry import robot_config as parse_robot_config
from ..scenes.loading.mjcf import load_spec, save_spec
from ..scenes.loading.source import SceneSource, instance_catalog, resolve_target
from ..utils.io import canonical, read_json, write_json
from .control.interface import RobotCommand
from lastmile_dataflow.robots.registry import load_robot_spec, make_robot_adapter

NATIVE_PROFILE = "molmobot-native-v1"
_KEEP = object()


class InitializationError(ValueError):
    """初态不合法；runner 据此归类为 invalid_initialization 而不是基础设施错误。"""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details


@contextmanager
def closed_on_error(resource):
    """Close a freshly created resource when its setup fails."""
    try:
        yield resource
    except BaseException:
        resource.close()
        raise


class SnapshotCache:
    """Process-local frozen models; every operation still creates fresh MjData."""

    def __init__(self, capacity=2):
        self.capacity = capacity
        self.scenes = OrderedDict()

    def get(self, path, robot, target, scene_id=None):
        key = (str(path), target)
        if key not in self.scenes:
            while len(self.scenes) >= self.capacity:
                _, old = self.scenes.popitem(last=False)
                old.close()
            self.scenes[key] = Simulation.from_snapshot(path, robot, target=target, scene_id=scene_id)
        self.scenes.move_to_end(key)
        return self.scenes[key]

    def close(self):
        for scene in self.scenes.values():
            scene.close()
        self.scenes.clear()


class Simulation:
    def __init__(self, model, robot_config, catalog, target=None):
        self.model = model
        self.data = mujoco.MjData(model)
        self.robot = make_robot_adapter(model, self.data, robot_config)
        self.catalog = catalog
        self.target_id = resolve_target(model, catalog, target)
        self.handle_id = None  # open 任务的把手 body；由 executor 按 task 填入
        self.started = False  # 是否已 begin()（连续执行纪律的开关）
        self.closed = False
        self.renderer = None
        self.third_person_camera = None
        self.state_spec = mujoco.mjtState.mjSTATE_INTEGRATION
        self.clock_remainder = 0.0  # 控制周期与物理步长之间的余数，保证长期平均控制频率
        self.evidence_recorder = None
        self.planning_geometry = {}
        # Scene origin: where this model came from, copied to every derived simulation.
        self.source = None
        self.source_metadata = None
        self.spec = None
        self.frozen_dir = None
        self.local_model = None
        self.scene_id = None  # scene-library id, set when opened from a library snapshot
        self._frame_cache = None

    @classmethod
    def from_source(cls, source, robot_config, *, target=None, robot_scale=None):
        """从原始 MJCF 创建准备阶段仿真，并初始化机器人。"""
        spec = cls.prepare_spec(source, robot_config, robot_scale=robot_scale)
        model = spec.compile()
        metadata = read_json(source.metadata_path) if source.metadata_path else {}
        sim = cls(model, robot_config, instance_catalog(model, source, metadata=metadata), target)
        sim.spec, sim.source, sim.source_metadata = spec, source, metadata
        try:
            sim.robot.initialize()
        except ValueError as exc:
            sim.close()
            raise InitializationError(str(exc)) from exc
        return sim

    @classmethod
    def from_snapshot(cls, frozen_dir, robot_config, *, target=None, scene_id=None):
        frozen_dir = Path(frozen_dir)
        value = read_json(frozen_dir / "config.json")
        if _normalized(asdict(robot_config)) != _normalized(asdict(parse_robot_config(value["robot"]))):
            raise ValueError("snapshot robot configuration mismatch")
        if value["mujoco_version"] != mujoco.__version__:
            raise ValueError("use the saved MuJoCo version; cross-version XML reconstruction is not verified")
        model = mujoco.MjModel.from_binary_path(str(frozen_dir / "model.mjb"))
        sim = cls(model, robot_config, value["instances"], target)
        sim.frozen_dir = str(frozen_dir.resolve())
        sim.scene_id = scene_id
        sim.local_model = value.get("local_model")
        sim.source = SceneSource(**value["source"])
        sim.restore_snapshot(frozen_dir / "initial.npz")
        return sim

    @staticmethod
    def prepare_spec(source, robot_config, *, robot_scale=None):
        """Attach scene and robot in memory; leave source assets unchanged."""
        spec = load_spec(source.xml_path)
        spec.attach(load_robot_spec(robot_config, robot_scale), prefix="", frame=spec.worldbody.add_frame())
        spec.option.timestep = robot_config.physics_dt
        if robot_config.control_profile == NATIVE_PROFILE:
            spec.visual.global_.offwidth = max(spec.visual.global_.offwidth, 1280)
            spec.visual.global_.offheight = max(spec.visual.global_.offheight, 720)
        return spec

    def derive(self, *, model=None, catalog=None, robot_config=None, target=_KEEP, spec=_KEEP):
        """New MjData (and optionally a new model) carrying this scene's origin.

        The caller sets the new physical state; on failure inside a
        ``with closed_on_error(...)`` block the derived simulation is closed.
        """
        if target is _KEEP:
            target = self.model.body(self.target_id).name if self.target_id is not None else None
        derived = Simulation(
            model or self.model,
            robot_config or self.robot.config,
            copy.deepcopy(self.catalog if catalog is None else catalog),
            target,
        )
        derived.source, derived.source_metadata = self.source, self.source_metadata
        derived.frozen_dir, derived.local_model = self.frozen_dir, self.local_model
        derived.spec = self.spec if spec is _KEEP else spec
        return derived

    def source_spec(self):
        """Editable MJCF of this scene: in-memory spec, frozen XML, or rebuilt from source."""
        if self.spec is not None:
            return self.spec.copy()
        if self.frozen_dir:
            return load_spec(Path(self.frozen_dir) / "model.xml")
        return self.prepare_spec(self.source, self.robot.config)

    def freeze(self, path):
        """保存准备阶段场景。保留同版本二进制载荷，另导出 MJCF。"""
        if self.started:
            raise RuntimeError("cannot save initialization after execution begins")
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        mujoco.mj_saveModel(self.model, str(path / "model.mjb"), None)
        if self.spec is not None:
            save_spec(self.spec, path / "model.xml")
        elif self.frozen_dir and (Path(self.frozen_dir) / "model.xml").is_file():
            copyfile(Path(self.frozen_dir) / "model.xml", path / "model.xml")
        value = {
            "robot": asdict(self.robot.config),
            "source": self.source.to_dict(),
            "mujoco_version": mujoco.__version__,
            "instances": self.catalog,
        }
        if self.local_model is not None:
            value["local_model"] = self.local_model
        write_json(path / "config.json", value)
        self.save_snapshot(path / "initial.npz")
        return value

    def state_vector(self):
        """完整积分状态向量（qpos/qvel/act/插件状态等），用于快照与身份计算。"""
        state = np.zeros(mujoco.mj_stateSize(self.model, self.state_spec))
        mujoco.mj_getState(self.model, self.data, state, self.state_spec)
        return state

    def save_snapshot(self, path):
        with Path(path).open("xb") as stream:
            np.savez_compressed(
                stream,
                state=self.state_vector(),
                state_spec=int(self.state_spec),
                control_json=json.dumps(self.robot.snapshot_control()),
                clock_remainder=self.clock_remainder,
            )

    def restore_snapshot(self, path):
        if self.started or self.closed:
            raise RuntimeError("snapshot restore prohibited after execution begins")
        with np.load(path, allow_pickle=False) as saved:
            state = saved["state"]
            if state.shape != self.state_vector().shape or not np.isfinite(state).all():
                raise ValueError("snapshot state does not match this model")
            self.set_state(state, json.loads(str(saved["control_json"])), float(saved["clock_remainder"]))

    def set_state(self, integration, control, clock_remainder):
        """Install a captured integration state, robot control targets and clock remainder."""
        if self.started or self.closed:
            raise RuntimeError("state initialization requires an unstarted simulation")
        mujoco.mj_setState(self.model, self.data, integration, self.state_spec)
        self.robot.restore_control(control)
        self.clock_remainder = clock_remainder
        mujoco.mj_forward(self.model, self.data)

    def begin(self):
        """进入连续执行阶段。此调用不可逆——之后禁止一切重置操作。"""
        if self.started or self.closed:
            raise RuntimeError("simulation already started or closed")
        if self.evidence_recorder is not None:
            self.evidence_recorder.capture()
        self.started = True
        self.robot.execution_started = True

    def _require_running(self):
        if not self.started or self.closed:
            raise RuntimeError("step requires active continuous attempt")

    def step(self, action, *, fixed_base=False, substep_check=None, command_raw=None):
        """Advance one control interval, checking every physics substep.

        Clock remainder alternates 12/13 ticks at 20 Hz and 4 ms physics.
        ``substep_check`` returns a failure reason or None; a failure ends the
        interval early. Returns (command, failure_reason)."""
        self._require_running()
        command = self._submit(action, fixed_base=fixed_base, command_raw=command_raw)
        self.clock_remainder += 1 / self.robot.config.control_hz
        n = int((self.clock_remainder + 1e-12) / self.model.opt.timestep)
        self.clock_remainder -= n * self.model.opt.timestep
        return self._advance(command, [n], substep_check)

    def step_policy(self, action, *, substep_check=None, command_raw=None):
        """MolmoBot: set relative targets once, 5 control ticks × 5 physics ticks."""
        self._require_running()
        if self.robot.config.control_profile != NATIVE_PROFILE:
            raise ValueError("step_policy requires native MolmoBot profile")
        if not np.isclose(self.model.opt.timestep, 0.004) or self.robot.config.control_hz != 10:
            raise ValueError("MolmoBot timing requires 100/20/4 ms")
        command = self._submit(action, command_raw=command_raw)
        command["timing_ms"] = {"policy": 100, "control": 20, "physics": 4}
        return self._advance(command, [5] * 5, substep_check)

    def _submit(self, action, *, fixed_base=False, command_raw=None):
        if self.robot.config.control_profile == "robocasa-v1" and isinstance(action, RobotCommand):
            action = self.robot.execution_action(action)
            if command_raw is None:
                command_raw = action.copy()
        command = self.robot.apply(action, fixed_base=fixed_base)
        if self.evidence_recorder:
            raw = action if command_raw is None else command_raw
            if isinstance(raw, RobotCommand):
                raw = _requested_targets(self.robot, raw, command["submitted_action"])
            self.evidence_recorder.requested_action = self.robot.record_action(raw)
        return command

    def _advance(self, command, tick_groups, substep_check):
        before = float(self.data.time)
        actual_ticks = 0
        stop = None
        completed = False
        try:
            for ticks in tick_groups:
                self.data.ctrl[:] = command["ctrl"]
                for _ in range(ticks):
                    self.robot.update_control()
                    mujoco.mj_step(self.model, self.data)
                    actual_ticks += 1
                    stop = substep_check() if substep_check else None
                    if stop:
                        break
                if stop:
                    break
            completed = True
        finally:
            mujoco.mj_forward(self.model, self.data)
            command.update(
                time_before_s=before,
                time_after_s=float(self.data.time),
                requested_physics_steps=sum(tick_groups),
                actual_physics_steps=actual_ticks,
                completion="complete" if completed and not stop else "partial",
            )
            if actual_ticks:
                command["ctrl"] = self.data.ctrl.tolist()  # feedback controllers rewrite ctrl per substep
            if self.evidence_recorder and actual_ticks:
                self.evidence_recorder.executed(command)
        return command, stop

    def observe_state(self):
        """Measured state; nonfinite values become null and their field paths are listed."""
        bodies = {obj["instance_id"]: {"pose_world_xyz_wxyz": self._pose(obj["body_id"])} for obj in self.catalog}
        target = None
        if self.target_id is not None:
            target = {
                "mjcf_body": self.model.body(self.target_id).name,
                "pose_world_xyz_wxyz": self._pose(self.target_id),
            }
        nonfinite = []
        result = _null_nonfinite(
            {
                "time_s": float(self.data.time),
                "robot": self.robot.state(),
                "target": target,
                "instances": bodies,
                "qpos": self.data.qpos.tolist(),
                "qvel": self.data.qvel.tolist(),
                "ctrl": self.data.ctrl.tolist(),
            },
            "",
            nonfinite,
        )
        if nonfinite:
            result["nonfinite_fields"] = nonfinite
        return result

    def _pose(self, body):
        return np.r_[self.data.xpos[body], self.data.xquat[body]].tolist()

    def render(self, width, height):
        """RGB of the configured robot cameras (and the optional observer) at the current state.

        Frames are cached per (time, size), so recorder and analysis share one render."""
        key = (float(self.data.time), width, height)
        if self._frame_cache is None or self._frame_cache[0] != key:
            self._frame_cache = key, self._render_cameras(width, height)
        return {name: frame.copy() for name, frame in self._frame_cache[1].items()}

    def _render_cameras(self, width, height):
        if self.renderer is not None and (self.renderer.width, self.renderer.height) != (width, height):
            self.renderer.close()
            self.renderer = None
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=height, width=width)
        frames = {}
        for alias, name in self.robot.camera_names.items():
            self.renderer.update_scene(self.data, camera=name)
            frames[alias] = self.renderer.render().copy()
        if self.third_person_camera is not None:
            frames[self.third_person_camera.name] = self.third_person_camera.render(self, self.renderer, width, height)
        return frames

    def close(self):
        """释放渲染器并标记关闭；幂等。"""
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
        self.closed = True


def _requested_targets(robot, command, submitted):
    """Recorded requested action: fields left None hold the submitted absolute targets."""
    if not isinstance(submitted, dict):
        return robot.execution_action(command)
    return {
        key: submitted[key] if value is None else {**submitted[key], **value} if isinstance(value, dict) else value
        for key, value in asdict(command).items()
    }


def _null_nonfinite(value, path, found):
    if isinstance(value, float) and not np.isfinite(value):
        found.append(path)
        return None
    if isinstance(value, dict):
        return {k: _null_nonfinite(v, f"{path}.{k}", found) for k, v in value.items()}
    if isinstance(value, list):
        return [_null_nonfinite(v, f"{path}[{i}]", found) for i, v in enumerate(value)]
    return value


def _normalized(value):
    """Normalize tuple/list differences before comparing saved robot configs."""
    return json.loads(canonical(value))


class OperationFailure(RuntimeError):
    """An attempt-level physical or planning failure with an outcome reason."""

    def __init__(self, reason, diagnostic=None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic
