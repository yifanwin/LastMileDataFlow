"""独立 MuJoCo 生命周期：准备时可恢复，连续执行开始后永久禁止重置。"""
from dataclasses import asdict
from pathlib import Path

import mujoco
import numpy as np

from ..io import file_digest, read_json, write_json
from ..robots.rby1 import RBY1Adapter, prepare_robot_spec
from ..scenes.source import instance_catalog, resolve_target, scene_version


class InitializationError(ValueError):
    pass


class Simulation:
    def __init__(self, model, robot_config, catalog, target=None):
        self.model = model
        self.data = mujoco.MjData(model)
        self.robot = RBY1Adapter(model, self.data, robot_config)
        self.catalog = catalog
        self.target_id = resolve_target(model, catalog, target)
        self.started = False
        self.closed = False
        self.renderer = None
        self.state_spec = mujoco.mjtState.mjSTATE_INTEGRATION
        self.model_hash = None
        self._clock_remainder = 0.0

    @classmethod
    def from_source(cls, source, robot_config, *, target=None, restoration=None):
        spec = cls.prepare_spec(source, robot_config)
        if restoration:
            from ..integrations.legacy import apply_restoration_spec
            apply_restoration_spec(spec, restoration)
        spec.option.timestep = robot_config.physics_dt
        model = spec.compile()
        catalog = instance_catalog(model, source)
        sim = cls(model, robot_config, catalog, target)
        try:
            sim.robot.initialize(restoration["robot_initial"] if restoration else None)
        except ValueError as exc:
            sim.close()
            raise InitializationError(str(exc)) from exc
        if restoration:
            from ..integrations.legacy import apply_restoration_state
            apply_restoration_state(sim, restoration)
        sim.spec = spec
        sim.source = source
        sim.restoration = restoration
        return sim

    @staticmethod
    def prepare_spec(source, robot_config):
        spec = mujoco.MjSpec.from_file(str(source.xml_path))
        robot_spec = mujoco.MjSpec.from_file(robot_config.model_path)
        prepare_robot_spec(robot_spec, robot_config)
        spec.attach(robot_spec, prefix="", frame=spec.worldbody.add_frame())
        spec.option.timestep = robot_config.physics_dt
        return spec

    def freeze(self, path):
        if self.started:
            raise RuntimeError("freeze initialization only before continuous execution")
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        model_path = path / "model.mjb"
        if model_path.exists():
            raise FileExistsError(model_path)
        mujoco.mj_saveModel(self.model, str(model_path), None)
        self.model_hash = file_digest(model_path)
        version = scene_version(self.source, asdict(self.robot.config), self.model_hash,
                                self.restoration, provenance=getattr(self,"frozen_provenance",None))
        write_json(path / "version.json", version)
        write_json(path / "instances.json", self.catalog)
        self.save_snapshot(path / "initial.npz")
        write_json(path / "checksums.json", {name: file_digest(path / name) for name in
                    ("model.mjb", "initial.npz", "version.json", "instances.json")})
        return version

    def state_vector(self):
        state = np.zeros(mujoco.mj_stateSize(self.model, self.state_spec))
        mujoco.mj_getState(self.model, self.data, state, self.state_spec)
        return state

    def save_snapshot(self, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        if self.model_hash is None:
            raise RuntimeError("save compiled model before snapshot")
        with path.open("xb") as stream:
            np.savez_compressed(stream, state=self.state_vector(), model_sha256=self.model_hash,
                                state_spec=int(self.state_spec),
                                mujoco_version=mujoco.__version__,
                                fixed_head=self.robot.fixed_head,
                                clock_remainder=self._clock_remainder)

    def restore_snapshot(self, path):
        if self.started or self.closed:
            raise RuntimeError("snapshot restore prohibited after continuous execution starts")
        with np.load(path, allow_pickle=False) as saved:
            if str(saved["model_sha256"]) != self.model_hash:
                raise ValueError("snapshot model digest mismatch")
            if str(saved["mujoco_version"]) != mujoco.__version__:
                raise ValueError("snapshot MuJoCo version mismatch")
            if int(saved["state_spec"]) != int(self.state_spec):
                raise ValueError("snapshot state specification mismatch")
            state = saved["state"]
            if state.shape != self.state_vector().shape or not np.isfinite(state).all():
                raise ValueError("invalid snapshot state")
            head = saved["fixed_head"]
            if head.shape != (2,) or not np.isfinite(head).all():
                raise ValueError("invalid snapshot head")
            remainder = float(saved["clock_remainder"])
            if not np.isfinite(remainder) or not 0 <= remainder < self.model.opt.timestep + 1e-10:
                raise ValueError("invalid snapshot clock")
            mujoco.mj_setState(self.model, self.data, state, self.state_spec)
            self.robot.fixed_head = head.copy()
            self._clock_remainder = remainder
        mujoco.mj_forward(self.model, self.data)

    @classmethod
    def from_snapshot(cls, frozen_dir, robot_config, *, target=None):
        frozen_dir = Path(frozen_dir)
        checksums = read_json(frozen_dir / "checksums.json")
        required = {"model.mjb", "initial.npz", "version.json", "instances.json"}
        if set(checksums) != required:
            raise ValueError("incomplete frozen snapshot manifest")
        for name, checksum in checksums.items():
            if file_digest(frozen_dir / name) != checksum:
                raise ValueError(f"frozen artifact modified: {name}")
        version = read_json(frozen_dir / "version.json")
        if version.get("schema_version") not in (None, "1.0", "2.0"):
            raise ValueError("unknown frozen scene schema")
        from ..io import digest
        if digest({k: v for k, v in version.items() if k != "version_id"}) != version["version_id"]:
            raise ValueError("scene version digest mismatch")
        model_hash = file_digest(frozen_dir / "model.mjb")
        if model_hash != version["compiled_model_sha256"]:
            raise ValueError("frozen model modified")
        if version["robot"] != read_json_value(asdict(robot_config)):
            raise ValueError("robot configuration mismatch")
        model = mujoco.MjModel.from_binary_path(str(frozen_dir / "model.mjb"))
        sim = cls(model, robot_config, read_json(frozen_dir / "instances.json"), target)
        sim.model_hash = model_hash
        sim.restore_snapshot(frozen_dir / "initial.npz")
        if version.get("schema_version") == "2.0":
            from .build_session import model_identity, checkpoint_identity
            if model_identity(sim) != version["model_id"] or checkpoint_identity(sim, version["model_id"]) != version["checkpoint_id"]:
                sim.close()
                raise ValueError("frozen model/checkpoint identity mismatch")
            if digest(sim.catalog) != version["mapping_id"]:
                sim.close()
                raise ValueError("frozen instance mapping mismatch")
        from ..scenes.source import SceneSource
        source = version["source"]
        sim.source = SceneSource(**{k: source[k] for k in
                                ("scene_id", "xml_path", "metadata_path", "dataset", "split")})
        sim.restoration = version["restoration"]
        sim.frozen_provenance = version["source"]
        return sim

    def begin(self):
        if self.started or self.closed:
            raise RuntimeError("simulation already started or closed")
        self.started = True
        self.robot.execution_started = True

    def step(self, action, *, fixed_base=False, substep_check=None):
        if not self.started or self.closed:
            raise RuntimeError("step requires active continuous attempt")
        command = self.robot.apply(action, fixed_base=fixed_base)
        before = float(self.data.time)
        self._clock_remainder += 1 / self.robot.config.control_hz
        n = int((self._clock_remainder + 1e-12) / self.model.opt.timestep)
        self._clock_remainder -= n * self.model.opt.timestep
        stop = None
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
            if substep_check:
                check = substep_check()
                if not check["valid"]:
                    stop = check
                    break
        # mj_step 的 xpos 可能是最后一步积分前状态；先 forward 再记录现场。
        mujoco.mj_forward(self.model, self.data)
        command.update(time_before_s=before, time_after_s=float(self.data.time))
        return command, stop

    def observe_state(self):
        bodies = {}
        for obj in self.catalog:
            i = obj["body_id"]
            bodies[obj["instance_id"]] = {
                "pose_world_xyz_wxyz": np.r_[self.data.xpos[i], self.data.xquat[i]].tolist()}
        target = None
        if self.target_id is not None:
            i = self.target_id
            target = {"mjcf_body": self.model.body(i).name,
                      "pose_world_xyz_wxyz": np.r_[self.data.xpos[i], self.data.xquat[i]].tolist()}
        result = {"time_s": float(self.data.time), "robot": self.robot.state(),
                "target": target, "instances": bodies,
                "qpos": self.data.qpos.tolist(), "qvel": self.data.qvel.tolist(),
                "ctrl": self.data.ctrl.tolist()}
        # 终止现场的 NaN/Inf 不能破坏 JSON；以 null + 明确字段诊断保留，动作不会这样放行。
        nonfinite = []

        def diagnostic(value, path=""):
            if isinstance(value, float) and not np.isfinite(value):
                nonfinite.append(path)
                return None
            if isinstance(value, dict):
                return {k: diagnostic(v, f"{path}.{k}") for k, v in value.items()}
            if isinstance(value, list):
                return [diagnostic(v, f"{path}[{i}]") for i, v in enumerate(value)]
            return value

        result = diagnostic(result)
        if nonfinite:
            result["nonfinite_fields"] = nonfinite
        return result

    def render(self, width, height):
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=height, width=width)
        frames = {}
        for alias, camera in self.robot.camera_names.items():
            self.renderer.update_scene(self.data, camera=camera)
            frames[alias] = self.renderer.render().copy()
        return frames

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
        self.closed = True


def read_json_value(value):
    """标准化 tuple/list，用于冻结配置比较。"""
    import json
    from ..io import canonical
    return json.loads(canonical(value))
