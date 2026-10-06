"""独立 MuJoCo 生命周期：准备时可恢复，连续执行开始后永久禁止重置。

【本文件的地位】它是**唯一允许发生真实物理运动**的地方（对应 architecture.md 的
“runtime 是唯一真实运动入口”）。所有阶段——阶段一短动作、阶段二构建静置、阶段三抓取——
最终都通过这里的方法推进物理。

【必须理解的两种阶段】
  准备阶段（started=False）：可以加载模型、restore_snapshot、freeze、robot.initialize。
                            机器人位姿只能在这个阶段被设置。
  执行阶段（started=True） ：begin() 之后永久禁止上述所有操作，只能 step()。
                            目的：杜绝“偷偷把机器人传送回起点再假装成功”。

【为什么快照要存那么多东西】
  快照不只存 qpos/qvel，还存了控制时钟余数（20Hz 控制 vs 0.004s 物理的余数）和固定头目标，
  否则恢复后控制器的相位/头部锁定会对不上，两次运行的轨迹就无法逐字节复现。
"""
from dataclasses import asdict
from pathlib import Path

import mujoco
import numpy as np

from ..io import file_digest, read_json, write_json
from ..robots.rby1 import RBY1Adapter, prepare_robot_spec
from ..scenes.source import instance_catalog, resolve_target, scene_version


class InitializationError(ValueError):
    """初态不合法时使用；runner 会据此归类为 invalid_initialization 而不是基础设施错误。"""
    pass


class Simulation:
    def __init__(self, model, robot_config, catalog, target=None):
        self.model = model
        self.data = mujoco.MjData(model)
        self.robot = RBY1Adapter(model, self.data, robot_config)
        self.catalog = catalog
        self.target_id = resolve_target(model, catalog, target)  # 目标 body 的数字 id
        self.started = False       # 是否已 begin()（连续执行纪律的开关）
        self.closed = False
        self.renderer = None
        self.state_spec = mujoco.mjtState.mjSTATE_INTEGRATION  # 快照包含的完整积分状态
        self.model_hash = None
        self._clock_remainder = 0.0  # 控制周期与物理步长之间的余数，保证长期平均控制频率

    @classmethod
    def from_source(cls, source, robot_config, *, target=None, restoration=None):
        """从原始 MJCF（或旧样例恢复信息）构建一个全新的准备阶段仿真。

        步骤：准备 spec（含机器人挂接）→ 编译 → 建实例映射 →
              写入机器人初态 → 可选恢复旧样例的物体位姿。
        """
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
        """把房子 MJCF 和机器人 MJCF 拼成一份内存 spec（不落盘、不改源文件）。

        `spec.attach(...)` 把机器人整体挂到世界坐标系下的一个新 frame 上；
        之后所有机器人 body 名都会带 `robot_0/` 前缀（对应 config.namespace）。
        """
        spec = mujoco.MjSpec.from_file(str(source.xml_path))
        robot_spec = mujoco.MjSpec.from_file(robot_config.model_path)
        prepare_robot_spec(robot_spec, robot_config)
        spec.attach(robot_spec, prefix="", frame=spec.worldbody.add_frame())
        spec.option.timestep = robot_config.physics_dt
        return spec

    def freeze(self, path):
        """把当前场景冻结成可独立恢复的目录（仅准备阶段允许）。

        产物：model.mjb（编译模型）+ initial.npz（初态快照）+ version.json（版本摘要）
              + instances.json（实例映射）+ checksums.json（互校摘要）。
        目录已存在时直接报错，绝不覆盖——这是“attempt 唯一且不覆盖”约束的落点。
        """
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
        """取一份完整积分状态向量（qpos/qvel/act/插件状态等），用于快照与身份计算。"""
        state = np.zeros(mujoco.mj_stateSize(self.model, self.state_spec))
        mujoco.mj_getState(self.model, self.data, state, self.state_spec)
        return state

    def save_snapshot(self, path):
        """保存快照；包含状态、模型摘要、MuJoCo 版本、固定头与时钟余数，保证可精确恢复。"""
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
        """恢复快照（仅准备阶段允许）。所有校验失败都抛 ValueError，不做“尽力恢复”。"""
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
        """从冻结目录构建准备阶段仿真：先校验全部摘要，再恢复初态。

        校验链（任一步失败都拒绝，不会“凑合加载”）：
          checksums.json 覆盖完整 → 每个文件摘要一致 → version.json 自洽 →
          model.mjb 摘要等于 version 中记录 → 机器人配置一致 → （v2）模型/检查点/映射身份一致。
        这样能保证“独立恢复”拿到的是当初冻结的那一份，而不是被改过的现场。
        """
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
            # v2 额外绑定“编译模型身份 + 检查点身份 + 实例映射身份”
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
        """进入连续执行阶段。此调用不可逆——之后禁止一切重置操作。"""
        if self.started or self.closed:
            raise RuntimeError("simulation already started or closed")
        self.started = True
        self.robot.execution_started = True

    def step(self, action, *, fixed_base=False, substep_check=None):
        """推进一个控制周期：下发动作，再按物理步长细分执行。

        【为什么不是一步一个物理步】
          控制频率 20Hz、物理步长 0.004s，一个控制周期 ≈ 12.5 个物理步。
          用 `_clock_remainder` 在 12/13 之间交替，保证长期平均正好 20Hz，
          而不是每周期固定取整导致系统性时间漂移。

        substep_check 会在**每个物理子步**后调用（不是只在周期末尾），
        这样“某个瞬间的严重穿透”也会被捕捉到；一旦其 valid=False 就立即停。

        返回值：(command 下发记录, stop 停止原因或 None)
        """
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
        """采集一份完整“现场”：时间、机器人状态、目标位姿、所有实例位姿、qpos/qvel/ctrl。

        末尾的 diagnostic() 是重要细节：终止现场可能含 NaN/Inf，直接 JSON 化会炸。
        这里的处理是**把非有限值写成 null，同时记录字段路径**，
        目的是“保留诊断证据”而不掩盖问题——动作本身在 apply() 阶段就已拒绝非有限值。
        """
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
        """用机器人配置里的三台相机各渲染一帧 RGB（头相机 + 左右腕相机）。"""
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=height, width=width)
        frames = {}
        for alias, camera in self.robot.camera_names.items():
            self.renderer.update_scene(self.data, camera=camera)
            frames[alias] = self.renderer.render().copy()
        return frames

    def close(self):
        """释放渲染器并标记关闭；幂等。"""
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
        self.closed = True


def read_json_value(value):
    """标准化 tuple/list，用于冻结配置比较。

    因为配置里 tuple 被 JSON 化后变成 list，直接比较会不等；这里统一走一遍 canonical。
    """
    import json
    from ..io import canonical
    return json.loads(canonical(value))
