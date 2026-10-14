"""MuJoCo stepping and snapshots shared by construction and execution.

Preparation may initialize or restore state. After begin(), motion uses step()
only; an independent trial requires a new Simulation. Snapshots retain clock
remainder and fixed head targets as well as the physical state.
"""
from dataclasses import asdict
from pathlib import Path

import mujoco
import numpy as np

from ..config import read_config
from ..io import write_json
from .robot import RBY1Adapter, prepare_robot_spec
from ..scenes.source import instance_catalog, resolve_target
from ..scenes.mjcf import load_spec


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
        self.third_person_camera = None
        self.state_spec = mujoco.mjtState.mjSTATE_INTEGRATION  # 快照包含的完整积分状态
        self._clock_remainder = 0.0  # 控制周期与物理步长之间的余数，保证长期平均控制频率
        self.evidence_recorder = None
        self._raw_frame_cache = None
        self._sensor_result = None
        self._capture_depth_requested = False

    @classmethod
    def from_source(cls, source, robot_config, *, target=None):
        """从原始 MJCF 创建准备阶段仿真，并初始化机器人。"""
        spec = cls.prepare_spec(source, robot_config)
        spec.option.timestep = robot_config.physics_dt
        model = spec.compile()
        catalog = instance_catalog(model, source)
        sim = cls(model, robot_config, catalog, target)
        try:
            sim.robot.initialize()
        except ValueError as exc:
            sim.close()
            raise InitializationError(str(exc)) from exc
        sim.spec = spec
        sim.source = source
        sim.restoration = None
        return sim

    @staticmethod
    def prepare_spec(source, robot_config):
        """把房子 MJCF 和机器人 MJCF 拼成一份内存 spec（不落盘、不改源文件）。

        `spec.attach(...)` 把机器人整体挂到世界坐标系下的一个新 frame 上；
        之后所有机器人 body 名都会带 `robot_0/` 前缀（对应 config.namespace）。
        """
        spec = load_spec(source.xml_path)
        robot_spec = load_spec(robot_config.model_path)
        prepare_robot_spec(robot_spec, robot_config)
        spec.attach(robot_spec, prefix="", frame=spec.worldbody.add_frame())
        spec.option.timestep = robot_config.physics_dt
        if robot_config.control_profile == 'molmobot-native-v1':
            spec.visual.global_.offwidth = max(spec.visual.global_.offwidth, 1280)
            spec.visual.global_.offheight = max(spec.visual.global_.offheight, 720)
        return spec

    def freeze(self, path):
        """保存准备阶段场景。保留同版本二进制载荷，另导出 MJCF。"""
        if self.started:
            raise RuntimeError('cannot save initialization after execution begins')
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        mujoco.mj_saveModel(self.model, str(path / 'model.mjb'), None)
        # Binary remains available until exported XML has been tested with the actual assets.
        spec = getattr(self, 'spec', None)
        if spec is not None:
            import xml.etree.ElementTree as ET
            xml = ET.fromstring(spec.to_xml())
            defaults = xml.find('default')
            # Attached main defaults have no class name; XML requires them at root.
            if defaults is not None:
                for child in list(defaults):
                    if child.tag == 'default' and not child.get('class'):
                        defaults.remove(child)
                        defaults.extend(list(child))
            (path / 'model.xml').write_text(ET.tostring(xml, encoding='unicode'))
        value = {'robot': asdict(self.robot.config), 'source': self.source.to_dict(),
                 'mujoco_version': mujoco.__version__, 'instances': self.catalog}
        write_json(path / 'config.json', value)
        self.save_snapshot(path / 'initial.npz')
        return value

    def state_vector(self):
        """取一份完整积分状态向量（qpos/qvel/act/插件状态等），用于快照与身份计算。"""
        state = np.zeros(mujoco.mj_stateSize(self.model, self.state_spec))
        mujoco.mj_getState(self.model, self.data, state, self.state_spec)
        return state

    def save_snapshot(self, path):
        with Path(path).open('xb') as stream:
            np.savez_compressed(stream, state=self.state_vector(), state_spec=int(self.state_spec),
                                fixed_head=self.robot.fixed_head, clock_remainder=self._clock_remainder)

    def restore_snapshot(self, path):
        if self.started or self.closed:
            raise RuntimeError('snapshot restore prohibited after execution begins')
        with np.load(path, allow_pickle=False) as saved:
            state = saved['state']
            if state.shape != self.state_vector().shape or not np.isfinite(state).all():
                raise ValueError('snapshot state does not match this model')
            mujoco.mj_setState(self.model, self.data, state, self.state_spec)
            self.robot.fixed_head = saved['fixed_head'].copy()
            self._clock_remainder = float(saved['clock_remainder'])
        mujoco.mj_forward(self.model, self.data)

    @classmethod
    def from_snapshot(cls, frozen_dir, robot_config, *, target=None):
        frozen_dir = Path(frozen_dir)
        value = read_config(frozen_dir / 'config.json')
        if read_json_value(asdict(robot_config)) != value['robot']:
            raise ValueError('snapshot robot configuration mismatch')
        if value['mujoco_version'] != mujoco.__version__:
            raise ValueError('use the saved MuJoCo version; cross-version XML reconstruction is not verified')
        model = mujoco.MjModel.from_binary_path(str(frozen_dir / 'model.mjb'))
        sim = cls(model, robot_config, value['instances'], target)
        sim.restore_snapshot(frozen_dir / 'initial.npz')
        from ..scenes.source import SceneSource
        sim.source = SceneSource(**value['source'])
        sim.restoration = None
        sim.spec = None
        return sim

    def begin(self):
        """进入连续执行阶段。此调用不可逆——之后禁止一切重置操作。"""
        if self.started or self.closed:
            raise RuntimeError("simulation already started or closed")
        if self.evidence_recorder is not None:
            self.evidence_recorder.capture()
        self.started = True
        self.robot.execution_started = True

    def step(self, action, *, fixed_base=False, substep_check=None, command_raw=None):
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
        command, request_id, submitted_id = self._submit(action, fixed_base=fixed_base, command_raw=command_raw)
        self._clock_remainder += 1 / self.robot.config.control_hz
        n = int((self._clock_remainder + 1e-12) / self.model.opt.timestep)
        self._clock_remainder -= n * self.model.opt.timestep
        return self._advance(command, request_id, submitted_id, [n], substep_check)

    def step_policy(self, action, *, substep_check=None, command_raw=None):
        """MolmoBot: set relative targets once, 5 control ticks × 5 physics ticks.

        No repeated relative accumulation, teleport, or simulation reset.
        """
        if not self.started or self.closed:
            raise RuntimeError('step requires active continuous attempt')
        if self.robot.config.control_profile != 'molmobot-native-v1':
            raise ValueError('step_policy requires native MolmoBot profile')
        if not np.isclose(self.model.opt.timestep, .004) or self.robot.config.control_hz != 10:
            raise ValueError('MolmoBot timing requires 100/20/4 ms')
        command, request_id, submitted_id = self._submit(action, command_raw=command_raw)
        command['timing_ms'] = {'policy': 100, 'control': 20, 'physics': 4}
        return self._advance(command, request_id, submitted_id, [5] * 5, substep_check)

    def _submit(self, action, *, fixed_base=False, command_raw=None):
        command = self.robot.apply(action, fixed_base=fixed_base)
        if self.evidence_recorder:
            self.evidence_recorder.requested_action = np.asarray(action if command_raw is None else command_raw).copy()
        return command, None, None

    def _advance(self, command, request_id, submitted_id, tick_groups, substep_check):
        before = float(self.data.time)
        actual_ticks = 0
        stop = None
        error = None
        try:
            for ticks in tick_groups:
                self.data.ctrl[:] = command['ctrl']
                for _ in range(ticks):
                    mujoco.mj_step(self.model, self.data)
                    actual_ticks += 1
                    if substep_check:
                        stop = substep_check()
                        if not stop['valid']:
                            break
                        stop = None
                if stop:
                    break
        except BaseException as exc:
            error = exc
            raise
        finally:
            mujoco.mj_forward(self.model, self.data)
            command.update(time_before_s=before, time_after_s=float(self.data.time),
                           requested_physics_steps=sum(tick_groups), actual_physics_steps=actual_ticks,
                           completion='partial' if stop or error else 'complete')
            if self.evidence_recorder and actual_ticks:
                self.evidence_recorder.executed(command)
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
        if self._raw_frame_cache is not None:
            time_s, w, h, frames = self._raw_frame_cache
            if time_s == float(self.data.time) and (w, h) == (width, height):
                return {k: v.copy() for k, v in frames.items()}
        from ..data.camera_calibration import capture_sensors
        samples = capture_sensors(self, width, height, depth=self._capture_depth_requested)
        self._sensor_result = (float(self.data.time), width, height, samples)
        return {k: sample['rgb'] for k, sample in samples.items()}

    def capture_sensors(self, width, height, *, depth=True):
        self._capture_depth_requested = depth
        try:
            self.render(width, height)
        finally:
            self._capture_depth_requested = False
        if self._sensor_result is None or self._sensor_result[:3] != (float(self.data.time), width, height):
            raise RuntimeError('raw calibration unavailable from overridden renderer')
        return self._sensor_result[3]

    def enable_third_person(self,config=None):
        """Reusable optional observer, independent of the robot's fixed MJCF cameras."""
        from ..data.third_person import ThirdPersonCamera
        self.third_person_camera=ThirdPersonCamera(config)
        return self.third_person_camera

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
