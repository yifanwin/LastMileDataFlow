# 第 2 路：runtime/ 精简审查

> 2026-11-12 更新：用户改了决定，『RL 文件为达到目标，可以做修改』。本文中“RL 文件只改 import 行、不搬、不动”的限制已作废，以方案 02 第四节第 7 条为准。

范围：`pro5000:/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify/src/lastmile_dataflow/runtime/`，不含 `runtime/rl/`。RL 文件只记依赖，见第 5 节。
方式：只读，没有改动代码，也没有运行任何程序。行号以 2026-11-11 读取时的工作树为准。

> **注意：工作树正在 rebase。** 读取过程中发现 `refactor/simplify` 正处在 interactive rebase 中（onto 89c7851，pick 0f8648c 已做、bfd707d 待做），`runtime/execution/workers.py`、`planning/pool.py`、`workflows/scene_datasets.py`、`01-handover.md` 都有冲突（UU），`workflows/collection.py` 是 DU。读 `workers.py` 时文件里还没有冲突标记，之后 grep 出了 2 处 `<<<<<<<`，说明有人（Codex 或用户）正在这个工作树里解冲突。本报告里 `workers.py` 的内容以先读到的版本为准。动手前要先等 rebase 完成。

---

## 1. 总体判断

runtime 去掉 RL 以后约 5400 行。主要问题不在某个文件写得长，而在**同一件事有好几份实现**：

| 重复的东西 | 出现处 | 份数 |
|---|---|---|
| 一次试验的外壳：packet、recorder、异常分类、终止/结论/指标汇总 | `execution/curobo.py`、`execution/vla.py` | 2 |
| 派生新 Simulation 并手工抄来源字段（source/restoration/spec/frozen_dir/local_model） | `simulation.from_snapshot`、`context.clone_sim`、`session.clone_preparation`、`session.candidate`、`benchmark_scene.native_scene`（`scenes/loading/local.py` 里还有一处） | 6 |
| substep 检查的返回格式 | `{'valid','issues':[{'severity','code'}]}`（context）、`{'valid','issues':[str]}`（vla、preparation）、`{'valid':True}`（semantic_tick） | 3 种 |
| 非有限/警告检查和标签 | `contacts.simulation_failure`（nonfinite_physics）、`pick.PickMonitor.sample`（nonfinite_state）、`health.inspect_state`（nonfinite_physics_state） | 3 |
| yaw 差 | `planning/navigation/geometry.yaw_gap`、`vla._PolicyNavigation.yaw_gap`、`context.ContinuousContext.yaw_gap`（不回绕）、`context.follow_arm_points` 里的 lambda | 4 |
| “到达容差内”判定 | `navigation.navigate`（2 处）、`vla._navigate`、`context.follow_points`（2 处） | 5 |
| 底盘每步限幅（speed/control_hz 与 max_base_delta 取小） | `navigation.navigate`、`context.mobile_tick` | 2 |
| 躯干联动期望 `[0,h,-2h,h,0,0]` | `action.torso_joints`、`evaluation.torso_tracking_error`、`pick.constraint_failure`、`health.inspect_state` | 4 |
| `try: … except BaseException: sim.close(); raise` | `simulation.from_source`、`preparation.prepare_scene`、`session.clone_preparation`、`session.candidate`、`session.matched_control` | 5 |
| 关节/执行器映射与 `group()` | `RBY1Adapter.__init__`、`PandaOmronAdapter.__init__` | 2 |
| MolmoBot 原生相机 fovy（139/58/58） | `robot._prepare_rby1_spec`、`benchmark_scene.native_scene` | 2 |
| 底盘/手臂 delta 上限检查 | `action.validate_action`、`native_actions.apply_native` | 2 |
| “plan 预算 + 计数”检查 | `ContinuousContext.plan`、`_Manipulation._query` | 2 |

此外有几类风格问题散布在各处：

- 函数内局部 import 约 35 处，多数并不是为了避开循环依赖；
- 实例属性在 `__init__` 之外临时挂上去，再用 `getattr/hasattr` 兜底（`frozen_dir`、`local_model`、`restoration`、`source`、`_hold_targets`、`planner_factory`）；
- 裸 dict 和裸 tuple 当结构用（`ContactFrame.contacts` 的 6 元组、`RoboCasa.control`、`execution_worker` 的 6 元组 job、outcome dict）；
- 三处鸭子类型的 ctx，其中一处用 `SimpleNamespace` 加 lambda 闭包拼出来；
- 格式风格不统一：vla/context/manipulation 是 black 风格的竖排，curobo/navigation/state/pick 是分号压成一行的密排。

---

## 2. 跨文件的重构建议（收益最大，建议先做）

### R1. 两个执行后端共用一个试验外壳（curobo.py + vla.py）

**现状**：`run_curobo_attempt`（curobo.py:24–171，一个 150 行的函数）和 `_VLARollout`（vla.py:81–593）各自做了一遍下面这些事：

1. clone_sim、放站位、可选第三人称相机；
2. 拼 packet 裸 dict、建 AttemptRecorder、`bind_simulation`；
3. 建 AnalysisVideo，写 analysis_video.json；
4. 写 initial_state.json，`mark_episode_phase('execution')`；
5. `try` 里依次捕获 OperationFailure、InvalidAction、TimeoutError、Exception，每个分支各写一遍 status/reason/navigation 映射；
6. `finally` 里拼 execution_termination、task_outcome、trajectory_metrics、contact_policy、navigation 汇总；
7. `recorder.finish`，再拼返回 dict（station_id/attempt/rollout_id/executed_steps/recording_complete/observation_complete）。

**问题**：

- 同一条规则写了两遍：`'simulation_failure' if reason in SIMULATION_FAILURES else …` 出现 4 次（curobo:80、curobo:95、vla:444、vla:552）；
- `trajectory_metrics` 的三元嵌套写了 2 次（curobo:154、vla:515）；
- `navigation` 在 finally 里的收尾写了 2 次（curobo:123、vla:473）；
- vla 的 `attribution` 用 4 层嵌套三元表达式拼出来（vla:526）；
- curobo 主函数 150 行、十几个局部变量，靠分号挤在一起。

**改法**（组合，不用继承）：新建 `runtime/execution/attempt.py`，放共享部分。

```python
@dataclass
class AttemptOutcome:
    status: str = "infrastructure_error"
    reason: str = "unfinished"
    attribution: dict | None = None
    exception_detail: dict | None = None
    execution_termination: dict | None = None
    task_outcome: dict | None = None
    control: dict | None = None

def failure_status(reason, *, executed=True):
    if reason in SIMULATION_FAILURES:
        return "simulation_failure"
    return "failure" if executed else "planning_no_solution"

def finish_navigation(navigation, contacts, reason): ...
def trajectory_metrics(stats, status, started): ...
def classify(exc, outcome, recorder, phase):  # 统一处理 InvalidAction/TimeoutError/Exception 三个分支
    ...

class AttemptSession:            # 两个后端都用：sim + recorder + 接触统计 + analysis
    sim; recorder; contact_stats; contact_sampler; navigation_contacts; analysis
    @classmethod
    def open(cls, baseline, task, station, config, collection, packet, *, root, attempt_id): ...
    def result(self, outcome, extra) -> dict: ...   # 拼最终 dict（同一份字段）
```

改完以后：

- `run_curobo_attempt` 拆成 `_settle()`、`_manipulate_with_retry()`、`_diagnostics()` 三个小函数，主流程大约 30 行；
- `_VLARollout` 只留 `_navigate/_prepare_policy/_run_policy`。

**预计删减**：curobo 和 vla 合计约 −180 行，新增 attempt.py 约 +90 行，净减约 90 行。复杂度下降更明显。

**风险**：中。输出 JSON 的字段必须逐个对齐（curobo 有 `planning_ref/seed/replay_complete/retry_history/operation_base_mode/planner_backend/manipulation_protocol`，vla 有 `gripper_mode/control.policy`），用现有 `tests/workflows/test_collection.py` 和 `test_lifecycle.py` 兜住。

### R2. substep 检查只返回一个字符串

**现状**：`Simulation._advance`（simulation.py:249–253）要求 `substep_check()` 返回 `{'valid': bool, 'issues': [...]}`。可是：

- `ContinuousContext._check_tick` 的 issues 是 `{'severity','code'}`；
- `vla._operation_check/_navigation_check` 和 `preparation.check` 的 issues 是裸字符串；
- `_check_semantic_tick` 有时只返回 `{'valid': True}`；
- 调用方再用 `stop['issues'][0]` 或 `stop['issues'][0]['code']` 去取。vla.tick（vla:222）把整个 issue 塞进 `OperationFailure`，那里是字符串，碰巧能用。

**改法**：协议改成 `Callable[[], str | None]`，返回第一个失败原因，没有失败就返回 None。

```python
# simulation._advance
reason = substep_check() if substep_check else None
if reason:
    stop = reason
    break
...
return command, stop          # stop: str | None
```

- `context._check_tick` 直接 `return failures[0] if failures else None`；
- `preparation.prepare_station` 里的闭包 `check()`（preparation.py:36）换成模块级的 `contacts.simulation_failure` 直接传进去，**同时去掉一个闭包**；
- `_check_semantic_tick` 和 vla 的两个 check 也照此改。

**预计删减**：约 −25 行。**风险**：低。只是内部协议，不进输出文件。

### R3. Simulation 的来源字段显式化，派生统一走 `derive()`

**现状**：

- `Simulation.__init__` 没有声明 `source/source_metadata/restoration/frozen_dir/local_model`，它们由 6 处调用方事后挂上去，读的时候用 `getattr(sim,'frozen_dir',None)`、`hasattr(sim,'local_model')`；
- `restoration` 只有 `from_source` 写 `None`、`from_snapshot` 写 `None`、`local.py:69` 写 `None`，其余地方都是复制它。**全仓库没有任何地方给它赋非 None 值**。

**改法**：

```python
@dataclass(frozen=True)
class SceneOrigin:
    source: SceneSource
    frozen_dir: str | None = None
    local_model: dict | None = None
    metadata: dict = field(default_factory=dict)

class Simulation:
    def __init__(self, model, robot_config, catalog, target=None, *, origin, spec=None): ...
    def derive(self, *, model=None, catalog=None, target=..., spec=..., state="copy"|"migrate"|None):
        """共享（或换用）model、复制来源，建一个新的 MjData。"""
```

- `clone_sim`、`clone_preparation`、`candidate`、`native_scene`、`local.py` 都改成调 `sim.derive(...)`；
- 删除 `restoration` 字段（7 处引用，外加 tests/data/test_panda_recording.py:26）。

**预计删减**：约 −40 行，同时删掉约 10 处 getattr/hasattr。**风险**：低到中。`freeze()` 写 config.json 时会用到 `local_model`，字段名保持不变即可。

### R4. 去掉鸭子类型的 ctx 和 SimpleNamespace 闭包

**现状**：

- `navigate(ctx, path, goal)` 依赖 ctx 上的 `sim/config/phase/monitor/fingers/tick/navigation_contacts` 七个属性。为了满足它，vla 专门造了一个 `_PolicyNavigation` dataclass（vla:63–78），其中 `hold_torso_target` 和 `yaw_gap` 两个方法 navigate 都没用到；
- `context.follow_arm_points`（context.py:409–451）用 `SimpleNamespace` 和 lambda 拼出假的 ctx 和假的 planner，再去调 `follow_points`，正是规范里点名不要的“闭包变量传依赖”；
- **`follow_arm_points` 在 src 和 tests 里都没有调用方**，是死代码。

**改法**：

- 删除 `follow_arm_points`（−43 行）；
- `navigate` 改成显式参数 `navigate(sim, config, tick, path, goal, contacts) -> NavigationResult`，phase/monitor 由调用方在调用前设置。这样 `_PolicyNavigation` 就可以删掉（−16 行）；
- 模块级的 `follow_points(ctx, …)` 只给 robocasa 用，并入 `ContinuousContext` 成为 `_follow_points_robocasa()`。

**预计删减**：约 −70 行。**风险**：低。

### R5. 底盘几何：yaw 差、到达判定、限幅各留一份

新增 `planning/navigation/geometry.py`（已有 `yaw_gap`）或 `runtime/control/base.py`：

```python
def base_error(target, current) -> np.ndarray      # [dx, dy, wrapped dyaw]
def arrived(error, config) -> bool                 # xy 和 yaw 都在容差内
def base_step_limits(robot_config, config) -> tuple[float, float]
```

- 替换 navigate:17/27/33、vla:287、context:343、context:473 这几处判定，以及 navigate:21 和 context:273 两处限幅；
- 删除 `vla._PolicyNavigation.yaw_gap` 和 `follow_arm_points` 里的 lambda。

**要先确认的一点**：`ContinuousContext.yaw_gap`（context:250）**不回绕**，因为 RBY-1 的 base_theta 是有界关节（action.py:124 的注释），跨 ±π 回绕会给出非法的跳变。所以它和导航用的 `yaw_gap` 不能直接合并。建议改名为 `joint_yaw_delta`，或者直接写成 `target[2]-base[2]`，不再做成方法。

**预计删减**：约 −30 行。**风险**：中，涉及数值行为，要用 test_geometry_navigation 和 test_lifecycle 对照。

### R6. 机器人适配器：组合一个关节表，消掉魔数和原生分支

**现状**：

- `RBY1Adapter` 和 `PandaOmronAdapter` 各自建 groups/actuators/joints/addresses/act_ids/camera 检查，各写一份 `group()`、`initialize` 骨架；
- `PandaRoboCasaAdapter` **继承** `PandaOmronAdapter`，只为了复用这些映射，却覆盖了 initialize/hold/apply/execution_action/snapshot/restore/copy/record_* 共 10 个方法；
- RBY1 的动作下标 `{"left":3,"right":11}`、`{"left":10,"right":18}` 在 robot.py 里硬编码 4 次（276、278、282、336、341），`action.ACTION_LAYOUT` 定义了布局却**没有人用**；
- `molmobot-native-v1` 的分支散在 `_prepare_rby1_spec`、`apply`、`_semantic_action`、`simulation.prepare_spec`、`simulation.step_policy` 五处；
- `apply_native`（native_actions.py）是模块函数，却直接读写 `robot._native_stationary_targets`、`robot._native_stationary`、`robot._controls`、`robot._check_joint` 这些私有成员。

**改法**：

```python
@dataclass(frozen=True)
class JointMap:                       # control/joints.py
    groups: dict[str, tuple[str, ...]]
    actuators: dict[str, tuple[str, ...]]
    joint_ids: dict[str, int]; qpos_adr: dict[str, int]; act_ids: dict[str, int]
    @classmethod
    def build(cls, model, namespace, groups, actuators): ...
    def read(self, data, group) -> np.ndarray: ...
    def write_qpos(self, data, values: dict) -> None: ...
    def check_joint(self, model, name, value) -> None: ...
    def controls(self, model, data, targets) -> np.ndarray: ...

ARM_SLICE = {"left": slice(3, 10), "right": slice(11, 18)}
GRIPPER_INDEX = {"left": 10, "right": 18}
TORSO_INDEX = 19
```

- 三个适配器都持有 `self.joints = JointMap.build(...)`；
- `PandaRoboCasaAdapter` 不再继承 PandaOmronAdapter。共享的只有 `factory 名称表`、`finger_bodies`、`tcp_site`，做成模块函数即可；
- 原生控制做成一个持有自身状态的小类 `NativeControl(stationary_targets, stationary)`，RBY1Adapter 按 profile 组合 `PositionControl` 或 `NativeControl`，`apply()` 只写一行 `return self.control.apply(self, value, fixed_base)`；
- 适配器选择从两张字典（`adapter_class` 和 `prepare_robot_spec` 各一份分派，都有 robocasa 特判）合并成一张：

```python
ROBOTS = {("rby1m", None): (RBY1Adapter, prepare_rby1_spec),
          ("panda_omron", "robocasa-v1"): (PandaRoboCasaAdapter, panda_robocasa.prepare_spec),
          ("panda_omron", None): (PandaOmronAdapter, panda_omron.prepare_spec)}
```

**预计删减**：约 −120 行。**风险**：中。test_robot_interface、test_native_control、test_actions、test_torso、test_robocasa_tracking、test_panda_omron 都要过；`record_*` 的输出布局不能变。

### R7. 健康检查、非有限标签、躯干联动各留一份

- `PickMonitor.sample` 的 illegal 检查（pick.py:59–60）直接用 `contacts.simulation_failure(sim)`；
- `health.inspect_state` 的第一道检查也改用它；
- 躯干期望统一用 `action.torso_joints(h)`，`evaluation.torso_tracking_error`、`pick.constraint_failure`、`health.inspect_state` 三处都引用它；
- `pick.py:150` 的 `fail in ('nonfinite_state','mujoco_warning')` 改成 `fail in SIMULATION_FAILURES`。

**要用户定**：三个非有限标签 `nonfinite_physics / nonfinite_state / nonfinite_physics_state` 会写进结果文件。合成一个是**输出变化**，旧数据里的标签照样被 `SIMULATION_FAILURES` 识别。建议合并，但这一步要用户点头。

**预计删减**：约 −20 行。

### R8. 用一个上下文管理器替代 5 处 `except BaseException: close; raise`

```python
@contextmanager
def closed_on_error(resource):
    try:
        yield resource
    except BaseException:
        resource.close()
        raise
```

`from_source`、`prepare_scene`、`clone_preparation`、`candidate`、`matched_control` 各写一行 `with closed_on_error(sim):`。如果做了 R3，`derive()` 内部负责清理，这里大半都会消失。**预计删减**约 −20 行，**风险**低。

### R9. 局部 import 提到顶层

约 35 处，例如：

- simulation.py：94、113、116、158、169、213、221、223、310、327、341、342；
- robot.py：159、163、207、253、268、293、300、319、347、349、359、360、380、382；
- context.py：51、94、411；
- curobo.py：45、46、65、87（87 还在循环体里）；
- vla.py：360、411；
- session.py：69、70、150（150 处的 `replace` 在文件顶部已经导入过）；
- pick.py：194。

真正有循环依赖的只有 `pick ↔ evaluation`（下一节合并文件后就没了）和 `scenes.geometry → runtime.control.interface`（见第 4 节）。其余全部提到顶层。**预计删减**约 −25 行。

### R10. 统一格式

vla、context、manipulation、workers 是 black 风格，一个调用拆成 8 行；curobo、navigation、state、pick、robot.aim_station 用分号挤成一行。建议对 runtime/ 跑一次 `ruff format`（行宽 100～110），并开启禁用分号多语句的 E702/E703。只是格式变化，单独成一个提交，方便审阅。行数大约持平：vla/context 会减少约 150 行，curobo/state/pick 会增加约 100 行。

---

## 3. 逐文件清单

格式：位置｜问题｜改法｜预计行数变化｜风险

### simulation.py（343 行）

- **22–24**｜`InitializationError` 里有多余的 `pass`；preparation.py:186 往异常上挂 `error.details`｜给它加一个 `details: dict | None = None` 字段｜−2｜低
- **27–46**｜`SnapshotCache` 本身没问题｜保留｜0｜—
- **50–68、85–87、165–172**｜实例属性在 `__init__` 外挂载｜见 R3｜见 R3｜—
- **73–75 和 97**｜`spec.option.timestep` 设置了两次｜删掉 74 行｜−1｜低
- **103–126 `freeze`**｜`frozen_dir` 分支在函数内 import copyfile、save_spec｜见 R3/R9，`origin.frozen_dir` 变成显式字段｜−4｜低
- **147–150**｜旧快照兼容分支（`'control_json' not in saved` 时读 `fixed_head`），AGENTS.md 明确不要内部旧路径兼容壳｜删除，只读 `control_json`｜−3｜低；要确认已冻结的场景库都是新格式，可以 grep 一下 npz 的键
- **154–173 `from_snapshot`**｜`read_json_value` 放在文件末尾，函数内还要 `import json` 和 `canonical`｜机器人配置比较改成 `asdict(robot_config) == asdict(parse_robot_config(...))`；tuple 和 list 的差异交给 `robot_config()` 解析时统一｜−6｜低
- **184–210 `step`/`step_policy`**｜开头的守卫重复｜抽 `_require_running()`｜−3｜低
- **212–233 `_submit`**｜RobotCommand 记录逻辑绕：同一函数 import 了两次 RobotCommand，再 import 一次 asdict，还有一行嵌套 dict 推导｜“把 command 还原成记录用的原始动作”交给适配器的 `record_action(command, submitted)`，`_submit` 只剩“执行 → 记录”两行｜−14｜中，要和 test_recording、test_panda_recording 对照
- **235–268 `_advance`**｜`except BaseException as exc: error = exc; raise` 只是为了在 finally 里知道是否异常｜改成 `completed = False … completed = True` 的标志，删掉 except；配合 R2｜−4｜低
- **244、264 两处 `getattr(self.robot,'update_control',None)`**｜接口不统一｜所有适配器都有 `update_control()`，RBY1 和 PandaOmron 里是空实现；`_advance` 直接调用｜−3｜低
- **289–299**｜`observe_state` 里定义了递归闭包 `diagnostic`，靠闭包往 `nonfinite` 列表里追加｜改成模块级 `_null_nonfinite(value, path, found: list)`｜0｜低
- **304–323 `render`/`capture_sensors`**｜状态很绕：`_raw_frame_cache` 由 **recorder.py:80 从外部写入**私有字段；`_capture_depth_requested` 通过 try/finally 临时置位再间接传参；`_sensor_result` 又是一份缓存｜改成一个显式的帧缓存 `FrameCache(time_s, size, samples)`，`capture_sensors(width, height, depth)` 直接调 camera_calibration；`render()` 取 rgb，命中缓存就直接返回；recorder 改调公开方法 `sim.render(...)`｜−10｜中，test_recording.py:45 会覆盖 render
- **325–329 `enable_third_person`**｜一行包装，属于无价值封装｜`sim.third_person_camera = ThirdPersonCamera(config)` 直接写在两个调用处（vla:119、curobo:30）｜−4｜低

### execution_config.py（209 行）

- **97–100、115–116、93–94**｜`operation_retries≤1`、`max_attempts==5`、`torso_error_policy=='record_only'`、`planner_backend=='curobo_v2_v080'` 都只允许一个取值（或一个上限），实际上是常量伪装成配置｜改成模块常量 `PLANNER_BACKEND`、`NATIVE_MAX_ATTEMPTS`、`TORSO_ERROR_POLICY`，从 dataclass 里删掉，结果文件照旧写这些值。`manipulation._query` 里硬编码的 `"curobo_v2_v080"` 和 `5` 也改用这两个常量（manipulation.py:115–116）｜−12｜中，configs/*.json 里如果写了这些键会报错，要同步删
- **101–109**｜`operation_base_mode` 可以由 backend 推出，只剩 curobo 还能选 `fixed`｜保留一个 `fixed_base: bool`，或者保留现状。可选｜−5｜低
- **117–174 `__post_init__`**｜校验 60 行，按类型分了 4 组循环｜改用字段约束表：`_POSITIVE_INT = (...)`、`_POSITIVE = (...)`、`_NONNEG = (...)`、`_UNIT = (...)` 四个元组，加一个 `_check(name, ok)` 函数，结构更平｜−15｜低
- **59**｜frozen dataclass 里用可变 list｜改成 `tuple[str, ...] = ("pick","open")`｜0｜低（JSON 读进来是 list，需要 `tuple()` 转换）
- **64–66**｜格式化残留，一个常量拆成 3 行｜1 行写完｜−2｜低
- **181–190 `execution_profile`**｜只在 workflows 里用，返回裸 dict｜移到 `workflows/collect.py`｜0｜低
- **193–209 `calibration_execution`**｜过滤的键（mode_config、torso_heights、lastmile_nearby_m、start_count…）**都不是 ExecutionConfig 的字段**，处理的是 workflows 的标定配置 dict，放错了模块｜移到 `workflows/collect.py` 或 station_map，留给第 1 路处理｜0｜低

### control/interface.py（51 行）

- **16–43 `RobotInterface` Protocol**｜src 里没有人引用，只有 test_robot_interface.py:49 用到；而且已经和实际接口对不上：`holding_state`、`execution_action`、`record_layout`、`planning_model`、`update_control` 都不在里面｜二选一：删掉；或者做 R6 时把它补成真正的统一接口，三个适配器显式标注。建议补全，因为规范要求“不同实现用统一接口”｜±0～−28｜低
- **46–51 `tcp_matrix`**｜只有 `scenes/geometry.tcp_pose` 调用它，而且是函数内 import，形成了 scenes → runtime 的反向依赖｜把函数体直接搬进 `scenes/geometry.tcp_pose`（它只读 `robot.tcp_site` 和 data），interface.py 里删掉｜−6｜低

### control/robot.py（383 行）

- **55–75、85–87**｜关节表重复｜见 R6｜—｜—
- **149、255**｜`getattr(self,"_hold_targets",None)`｜在 `__init__` 里初始化为 None｜−1｜低
- **206–242 `aim_station`**｜一个函数做了 yaw 对准和 pitch 对准两件事，还有分号密排（235–241）｜拆成 `_aim_yaw(initial, anchor, cid)` 和 `_aim_pitch(...)`，各约 12 行；“相机前向角”抽成 `_camera_heading(cid)`。panda_omron.py:135 也有同样的 yaw 迭代，可以共用｜−8｜中，标定结果对数值敏感
- **244–250、303**｜类属性散在方法中间｜集中放到类顶部｜0｜低
- **271–272 `execution_action`**｜只是 `_semantic_action` 的别名｜保留一个名字，统一叫 `execution_action`｜−3｜低
- **274–286 `manipulation_command`**｜和 `_semantic_action` 一样在填 delta｜改成构造 `RobotCommand(base, {side:q}, ...)` 后调 `_semantic_action`，闲置臂的 hold 目标作为参数传入｜−8｜中
- **292–294 `record_state`**｜runtime 依赖 `data.export.molmobot_dataset.state22`，方向反了｜把 `state22` 移到 control（它是机器人状态布局，不是数据导出逻辑），由 export 来 import｜0｜低
- **299–301 `planning_model`**｜runtime 通过函数内 import 依赖 planning，方向不对：规划模型应该由 planning 按机器人名字自己选｜挪到 `planning/robots/__init__.py:planning_model(robot_name, ...)`，适配器里删除｜−6｜低
- **345–354、378–383**｜两套分派｜见 R6｜−8｜—
- **357–374 `load_robot_spec`**｜从 `planning.robots.panda_omron` 导入 `SCALE`，runtime 又依赖 planning｜把 scale 放进机器人配置（`PandaOmronConfig.model_scale`），或者移到 control/panda_omron.py，让 planning 来 import 它｜−2｜低

### control/action.py（167 行）

- **7–12 `ACTION_LAYOUT`**｜没有人用｜做 R6 时改成真正使用的 `ARM_SLICE`/`GRIPPER_INDEX` 常量，否则就删｜−6｜低
- **52–53、146–147**｜`ANGULAR_TOLERANCE_RAD`、`COMMAND_TOLERANCE_RAD`、`FEEDBACK_TOLERANCE_RAD` 三个常量都是 .003｜如果语义相同，合成一个；FEEDBACK 只在 tests 里用（test_torso）｜−2｜低
- **35–49、native_actions 16–19**｜delta 上限检查重复｜抽 `_check_delta_bounds(a, config)`｜−4｜低
- **136**｜`sanitize_action` 调了适配器的私有方法 `robot._check_joint`｜做 R6 后改成 `robot.joints.check_joint`｜0｜低
- **141–143 `local_yaw_bounds`**｜被 planning/robots/rby1m 使用｜保留｜0｜—

### control/native_actions.py（77 行）

- **全文件**｜模块函数改写适配器私有状态｜做成 `NativeControl` 类，持有 stationary 状态，见 R6｜−5｜中
- **23–38**｜delta → 目标 → 投影的循环，和 `action._project_delta_commands` 很像，只差 stationary 锁存，而且这里不报超界、直接 clip｜两者行为不同，不建议强行合并。抽出 `joint_command_interval` 的批量版 `group_intervals(robot, group)` 共用即可｜−3｜低

### control/panda_omron.py（302 行）

- **18–61 `factory_spec`/`export_factory`**｜资产导出工具，只有 tests/smoke/panda_omron.py 用；`sys.path.insert` 加 try/finally 去 import robosuite｜移到 `scripts/` 下作为一次性资产工具（或 scenes/loading），runtime/control 只留运行时代码｜−45（搬走）｜低
- **124–126**｜`addresses` 又查了一遍 `model.joint(...)`，`joints` 已经有这个 id｜见 R6｜—｜—
- **146–158**｜property 和类属性交错排列｜集中排列｜0｜低
- **209–226**｜`arm_positions(arm)` 被拿来当参数校验，`gripper_width`、`tcp_site`、`finger_bodies` 都先调一次、丢掉返回值｜改成显式的 `_check_arm(arm)`；或者因为 `arms=("right",)`，直接删掉这些校验，内部调用方从来不传 left｜−4｜低
- **271–281 `_controls`**｜每个关节又查一次 `model.joint(...)`｜用 `self.joints[name]`｜−1｜低

### control/panda_robocasa.py（240 行）

- **72**｜继承 PandaOmronAdapter 后覆盖了 10 个方法｜见 R6，改成组合｜—｜中
- **98–102、111–119**｜`self.control` 是 8 个键的裸 dict，snapshot/restore 时逐键判断是不是 ndarray｜改成 `@dataclass class OscState(position, rotation, initial_joint, initial_yaw, base_velocity, height, grip, action)`，`to_json()`/`from_json()` 各写一行｜−2，结构更清晰｜低（snapshot 的 json 键要保持一致）
- **142–157、169–177、182–223**｜魔数 .05、.5、1.5、150、2000、10、.02、.2｜改成命名常量（robosuite OSC 的默认 kp、输出缩放），放在文件顶部，并注明来源｜+8｜低
- **182–223 `update_control`**｜42 行，计算了 OSC 臂力矩、升降力矩、夹爪、写 ctrl 四件事｜拆成 `_arm_torque()`、`_lift_force()`、`_write_ctrl(values)`｜0｜低
- **28–50 `rotation_vector`/`rotation_matrix`/`xyzw`**｜通用旋转工具；`rotation_vector`、`rotation_matrix` 还被 scenes/rl_observation、data/rendering/head_views、tests 引用（data → runtime.control 的依赖方向不对）｜移到 `scenes/geometry.py`，那里已有 `pose7`、`quat_angle`｜0（搬家）｜低；rl_observation 是 RL 文件，只需改 import 行

### control/dls.py（68 行）

- **全文件**｜只有 `runtime/rl/environment.py:19` 用，是 RL 专用的 mjlab 动作项｜移到 `runtime/rl/dls.py`，RL 侧只改一行 import｜0（搬家）｜低。如果用户要求 RL 零改动，就原地不动

### execution/protocol.py（83 行）

- **17–19、29**｜读写 `sim._clock_remainder` 私有字段｜Simulation 加公开属性 `clock_remainder`｜0｜低
- **21–30 `SimulationState.initialize`**｜和 `Simulation.restore_snapshot` 是同一件事：一个从内存恢复，一个从 npz 恢复｜让 `restore_snapshot` 先读 npz，构造出 `SimulationState`，再调 `.initialize(sim)`；`save_snapshot` 同样调 `SimulationState.capture(sim)` 再写盘。这样只有一份“状态 ↔ 仿真”的逻辑｜−10｜低
- **61–83**｜`PlanningRequest.inputs`、`PlanningResponse.plan/diagnostics` 是裸 dict｜跨进程的 RPC 载荷，留给第 3 路（planning/remote、pool）一起定｜—｜—

### execution/context.py（491 行）

- **42–59 `clone_sim`**｜见 R3｜−10｜—
- **62–90 `ContinuousContext.__init__`**｜25 个属性。其中 `collisions` 是 `contact_stats.examples` 的别名；`plan_serial` 和 `plan_count` 两个计数器只在预算模式下才不同｜`collisions` 改用 `contact_stats.examples`；计数合成一个 `PlanCounter` dataclass（count、serial、limit、`reserve()`），R1/`_query` 共用｜−8｜低
- **92–101 `deadline`**｜函数内 import `PlannerUnavailable`｜提到顶层；如果有循环依赖，就把这个异常定义挪到 protocol.py｜−2｜低
- **127–165 `_check_tick`**｜做了健康、接触、导航穿透、躯干反馈、monitor 采样、约束六件事｜按 R2 改返回值后，拆成 `_sample_monitor(contacts, failures)`｜0｜低
- **167–230 `tick`**｜64 行；两处 `open('ab')` 逐条追加 jsonl，每个事件开关一次文件｜抽 `_sanitize(action, fixed_base) -> (action, raw)` 和 `_append_jsonl(name, event)`（recorder 可能已有类似功能）；`semantic` 判断提成 `robot.semantic_control`，这个属性已存在，却没被用上（robot.py:248、panda_omron.py:156）｜−15｜低
- **250–251 `yaw_gap`**｜见 R5｜—｜中
- **258–297 `mobile_tick`**｜限幅和 navigate 重复｜见 R5｜−6｜—
- **299–322 `plan`**｜与 `_Manipulation._query` 都在做预算检查｜见上面的 PlanCounter｜−4｜低
- **328–367 `follow_points` 方法**和 **454–491 同名模块函数**｜一个方法和一个模块函数同名，方法里又调模块函数｜见 R4，并成 `_follow_rby1()` 和 `_follow_robocasa()` 两个私有方法｜−5｜低
- **374–382 `planner_options`**｜`SimpleNamespace(**asdict(config), ...)` 是一个无类型的 options 包｜由第 3 路定：planner 应该直接接收 `ExecutionConfig` 加显式参数（seed、max_candidates、robot_planner_dir）。另外 `robot_planner_dir` 写死成 rby1m，Panda 也走这条路径，需要核实｜—｜中
- **385–451**｜`_check_semantic_tick`、`semantic_tick`、`follow_arm_points`｜`follow_arm_points` 删除（R4）；`semantic_tick` 只有 smoke 脚本用，移到 `tests/smoke` 的共用 helper 里｜−50｜低

### execution/vla.py（623 行）

- **43–60 `first_point`**｜1024、576 写死，和 `__init__` 里的分辨率重复｜改用 `self.width/self.height` 或 `MOLMOBOT_RESOLUTION` 常量｜−1｜低
- **63–78 `_PolicyNavigation`**｜见 R4｜−16｜低
- **114 起，`self.pi05` 分支共 9 处**（114、116、121、302、325、335、359、410、539）｜两个 VLA 后端靠布尔值分叉｜做一个 `VLABackend` 小 dataclass：`name/schema_version/resolution/camera_order/fisheye/step/observation(sim, frames, task)/to_execution(robot, action)`，MolmoBot 和 pi05 各一个实例，放在 policies 侧，对应 `policies/molmobot.py` 和 `policies/pi05/`；同时消掉 360、411 两处函数内 import｜−30｜中
- **120–134、135**｜packet 裸 dict，和 curobo 重复｜见 R1｜—｜—
- **178–179 `_state`**｜一行包装｜删除｜−3｜低
- **209–210、404–405、431–433**｜时间预算检查写了 3 份｜抽 `_check_budget()`｜−5｜低
- **212–213、308–309、416–417**｜`if not self.sim.started: self.sim.begin()` 写了 3 份｜抽 `_ensure_started()`；或者在 `_prepare_policy` 里 begin 一次，后面不再判断｜−4｜低
- **224–270**｜两个 check 每个 substep 都调 `contact_observations()`，**而它每次都新建一个 ContactSampler**（contacts.py:112–114），`self.contact_sampler` 的缓存完全没起作用｜改成 `self.contact_sampler.capture(...)` 加 `contact_stats.update_frame(frame)`，和 context 保持一致。之后 `contact_observations` 和 `ContactStatistics.update` 都可以删｜−8，性能也会提升｜低
- **451–456、485–490**｜同一个 `evaluate(...)` 调用写了 2 次｜抽 `_evaluate()`｜−5｜低
- **468–470**｜循环结束后又把 `infrastructure_error/unfinished` 改写成 failure，和 466 行重复｜循环里给 status 设好初值，删掉这段｜−3｜低
- **472–542 `_finalize`**｜四层嵌套三元｜见 R1｜−25｜中
- **544–583 `run`**｜五个 except 分支，其中 `except Exception` 和外层的 `@isolated_episode` 重复｜见下面 lifecycle 一节｜—｜—
- **596–623 `run_vla_attempt`**｜参数照抄了一遍 `_VLARollout.__init__` 的全部参数｜改成 `VLAAttempt` dataclass，或者让 `_VLARollout` 自己带一个 `run` 类方法｜−12｜低

### execution/manipulation.py（383 行）

- **76–95 和后续**｜状态散落：`for self.side in self.sides`（124 行把循环变量写成实例属性）；`self.c`、`self.j`、`self.h` 这种单字母属性；diagnostic、operation_state、directory、goals、index、stages、goal、pre、verdict 等十几个属性都在 `__init__` 外创建｜`_plan()` 返回一个 `GraspPlan(side, index, stages, candidate, goal, pregrasp, directory)` dataclass，后续步骤以参数接收；`j/h` 改成局部变量 `joint_id/torso_h`｜0，但可读性大幅提升｜低
- **95**｜`getattr(self.ctx,"planner_factory",V2Planner)`：靠 ctx 上可能被挂上的属性来注入（planning/remote.py:27 会设置它）｜给 ContinuousContext 加一个显式字段 `planner_factory = V2Planner`｜−0｜低
- **103–119 `_query`**｜见 PlanCounter；`"curobo_v2_v080"` 和 `5` 硬编码｜−5｜低
- **126–137、257–274**｜建 planner 的代码块写了 2 次｜抽 `_make_planner(options, tag)`｜−10｜低
- **154–165**｜`operation_state` 根据 `ctx.budget is not None` 去读 `recorder.config["preparation"]["path"]`，是隐式耦合｜ContinuousContext 显式持有 `prepared_state_path: Path | None`，由 curobo.py 传入｜−3｜低
- **166–179**｜pick 和 open 两个分支只差 `lift` 参数｜`lift = op == "pick" and robot.plan_lift_before_close`，调用一次｜−6｜低
- **211–217、366–372**｜`active_control` dict 构造了 2 次｜`_control_record()` 方法，或 `ActiveControl` dataclass｜−6｜低
- **61–68、296–305**｜手写 z 轴旋转矩阵和 Rodrigues 公式｜换成 `scenes/geometry` 里的工具（或 `scipy.spatial.transform.Rotation.from_rotvec`）｜−10｜低
- **382–383 `manipulate_v2`**｜只有一个版本，后缀 v2 没有意义；curobo.py:19 还 `as manipulate` 改了一次名｜改名为 `manipulate`｜0｜低

### execution/lifecycle.py（197 行）

- **13、81–101 ContextVar 全局作用域**｜`track_resource`、`mark_episode_phase`、`episode_phase` 通过隐式全局状态在调用栈深处登记资源（clone_sim、recorder）。功能本身有用（批量实验的失败隔离），但依赖不可见｜中期把 `EpisodeScope` 显式传进 runner（`run_*_attempt(…, scope)`），clone_sim 由调用方登记。短期保留，这是 AGENTS 里“保留资源关闭与批量失败隔离”的那部分｜0（短期）｜中
- **122–168 `execute_isolated_episode`**｜try 里套 try/finally，外面再一层 finally；结果 dict 经过 4 次 `{**result, ...}` 改写｜拆成三个函数：`_run(function, scope) -> (result, traceback)`、`_merge_cleanup(result, errors)`、`_normalize_scope(result)`，主函数只剩 ContextVar 的 set/reset｜−10｜低
- **171–181 `isolated_episode`**｜wrapper 里用 lambda 闭包｜改成 `functools.partial(function, baseline, task, station, *args, root=root, attempt_id=attempt_id, **kwargs)`｜−2｜低
- **重复捕获**｜`run_curobo_attempt` 和 `_VLARollout.run` 内部已经 `except Exception` 并调用 `classify_episode_exception`，外层装饰器又捕获一遍。外层只在 recorder 建好之前的异常时生效｜保留外层（它能兜住构造期异常）；内层的 `except Exception` 也要保留，因为它要调 `recorder.finish` 写出结果。两层的状态映射都放到 R1 的 `classify()`｜—｜—

### execution/curobo.py（172 行）

- **24–171**｜整个文件只有一个 150 行的函数｜见 R1｜−60｜中
- **58**｜`ctx.max_plans` 在构造之后才按 operation 改｜在 ContinuousContext 构造时传入 `max_plans`｜−1｜低
- **65、87**｜函数内 import RobotCommand，87 行还在循环体里｜提到顶层｜−2｜低
- **83**｜重试时**从磁盘重新读** initial_state.json 去拿目标初始位姿｜开头记一次 `initial_target_xyz = sim.data.xpos[target_id].copy()`｜−1｜低
- **69–70、90**｜`ctx.plan_count=0` 写了两次｜PlanCounter 提供 `reset()`｜0｜低
- **106–109 `PickEvidenceComplete`**｜用异常传递“成功”信号：tick → checkpoint → raise → 外层捕获｜可以接受（要立刻跳出多层 follow 循环），但应在 pick.py 注释里说明用途。另一种做法是 tick 返回 `stop` 状态，那样要一路往回传，更啰嗦。**建议保留**｜0｜—
- **142–143**｜`os.environ.get('CUDA_VISIBLE_DEVICES')`；workers.py:202 用的是 `os.environ[...]`，变量不存在时会 KeyError｜两处统一用 `.get`，或抽一个 `worker_info()`｜−3｜低

### execution/navigation.py（41 行）

- **9–40**｜分号密排；ctx 是鸭子类型｜见 R4、R5，返回 `NavigationResult` dataclass（输出时 `asdict`）｜−5｜低

### execution/preparation.py（59 行）

- **36–38**｜函数内闭包 `check()`｜见 R2，直接传 `partial(simulation_failure, sim)`｜−3｜低
- **55**｜`getattr(baseline,'frozen_dir',None)`｜见 R3｜0｜低

### execution/workers.py（204 行，正在 rebase，有冲突）

- **19–25**｜模块全局 `_scenes`，由 `initialize_preparation_worker` 设置。multiprocessing initializer 的惯用写法，可以接受｜保留，但改名为 `_PREPARATION_SCENES` 并加类型注释｜0｜—
- **47–49、108–122**｜`_record_planning_failure` 有 9 个位置参数，`record_unexecuted` 用 partial 一个个透传｜改成 `UnexecutedTrial` dataclass（scene、index、prepared、response、budget、config、collection、root），配一个方法 `record(scenes)`｜−12｜低
- **151**｜job 是 6 元组｜`ExecutionJob` dataclass。pool 那边也要改，和第 3 路一起｜−2｜低
- **153–160**｜`inputs` 是裸 dict，交给 PlanningClient｜和 protocol.PlanningRequest.inputs 一起定｜—｜—
- **35–44、161–199**｜`except Exception` 归类成结果，属于批量隔离，保留｜0｜—

### construction/preparation.py（194 行）

- **32–41 SettleConfig 校验**｜可以接受｜保留｜0｜—
- **54–130 `settle_scene`**｜77 行，做了五件事：选 body、按祖先过滤、步进采样、窗口指标、穿透和警告检查；返回 13 个键的裸 dict｜拆成 `_dynamic_bodies(sim)`、`_bodies_under(sim, names, selected)`（67–74 的祖先回溯）、`_window_metrics(history, names)`、`_severe_penetrations(sim, limit)`；返回 `SettleReport` dataclass，`to_dict()` 时保持原键名｜+5，主函数缩到约 30 行｜低
- **186–187**｜往异常上挂 `error.details`｜见 simulation 一节｜−1｜低
- **192–194**｜`except BaseException`｜见 R8｜−3｜低
- **133–163 `PreparedScene`**｜`refresh()` 只有 tests/scenes/test_graph 调用｜确认 workflows 不用后删除，测试改为直接 `build_scene_graph`｜−5｜低

### construction/session.py（180 行）

- **13–27 `clone_preparation`**｜见 R3、R8｜−10｜低
- **57–100 `_model`**｜函数内 import load_spec 和 Path；`baseline_spec` 的懒加载有三层回退：spec → frozen_dir/model.xml → prepare_spec｜改成 `Simulation.source_spec()` 方法，放在 simulation 里｜−6｜低
- **121–136**｜给自由关节写位姿的 6 行代码写了 2 次（placements 和 target_moves）｜抽 `_set_free_pose(model, data, body_name, pose)`｜−8｜低
- **144–156 `settle`**｜函数内又 `from dataclasses import replace` 一次，文件顶部已有｜删掉｜−1｜低
- **172–180 `matched_control`**｜见 R8｜−3｜低

### construction/state.py（54 行）

- **15–38**｜三处 `try: k = m0.joint(name).id / except KeyError: continue`｜改用 `mujoco.mj_name2id(m, mjOBJ_JOINT, name)`，返回 −1 就跳过，不再拿异常做控制流｜−3｜低
- **21**｜关节宽度表在循环里每次新建｜提成模块常量 `_JOINT_WIDTH = {mjJNT_FREE:(7,6), mjJNT_BALL:(4,3), ...}`，用枚举名，不用 0/1/2/3｜0｜低
- **全文件**｜分号密排｜按 R10 统一格式｜+10｜低
- **52**｜读写 `_clock_remainder` 私有字段｜见 protocol 一节｜0｜低

### construction/benchmark_scene.py（45 行）

- **31–32**｜原生 fovy 和 robot.py:37 重复｜提成常量 `NATIVE_CAMERA_FOVY`，放在 robot.py｜−2｜低
- **33–34 与 simulation.py:98–100**｜原生 offscreen 尺寸一处写 1024×576，一处写 1280×720｜核对后只留一处｜−2｜低，渲染尺寸要确认
- **35–40**｜手工派生 Simulation｜见 R3｜−4｜低

### outcomes/contacts.py（142 行）

- **55–71、97–109**｜`ContactFrame.contacts` 里是 6 元组 `(index, pair, force, bearing, intended, forbidden)`，pick.py:63、contacts.py:61 和 :134 都靠位置解包｜改成 `@dataclass(frozen=True) class ContactSample(index, pair, force, bearing, intended, forbidden)`｜0｜低
- **85**｜`namespace = ... if self.sim.robot else 'robot_0/'`｜Simulation 一定有 robot，删掉这个兜底｜−0｜低
- **112–114 `contact_observations`**、**124–128 `ContactStatistics.update`**｜见 vla 一节；改完后，剩下的调用方 `health.inspect_state` 和 `scenes/stations/sampling.py:50` 都改成 `ContactSampler(sim).capture().observations()`｜−8｜低
- **47**｜地面穿透阈值 `.005` 是魔数｜命名常量 `FLOOR_PENETRATION_M`。health.py:30 也用了 .005｜0｜低

### outcomes/evaluation.py（118 行）

- **8–12**｜`torso_tracking_error` 和 pick.py:109 重复｜见 R7｜−2｜低
- **15–19 和 97–100**｜`mobile_constraint_failure` 和 `constraint` 只差 `check_idle`｜合成 `workspace_constraint(row, initial, *, check_idle=True)`｜−4｜低
- **22–27 `evaluate_mobile_pick`**｜只被 pick.py:194 在函数内 import 调用，原因是 pick ↔ evaluation 循环依赖｜**合并 pick.py 和 evaluation.py**（共 360 行，都是任务判定），内部循环依赖随之消失。`timed` 和 `EXECUTION_SEMANTICS` 不属于判定，移走（见 pick 一节）｜−6｜低
- **30–62 `attribute`**｜一长串 if/elif 字符串匹配。规则本身是领域知识，可以接受｜可选：把前 5 条改成 `(predicate, category)` 表；MissedGrasp 一段抽成 `_grip_attribution()`｜−5｜低
- **94**｜`VLA_PROTOCOL` 夹在文件中间｜移到文件顶部｜0｜低
- **103–118 `evaluate`**｜嵌套三元｜改成显式 if 链｜0｜低

### outcomes/pick.py（240 行）

- **58–60**｜自己又检查了一遍非有限值和警告｜见 R7｜−2｜—
- **77–82**｜“底盘/轮子碰地板”的豁免逻辑和 `ContactPair.floor_base` 重复｜直接用 `pair.floor_base`｜−3｜低
- **104**｜yaw 回绕又手写了一份｜用 `geometry.yaw_gap`｜0｜低
- **113–117**｜“Legacy contact labels”循环：`row['illegal']` 里只可能是 nonfinite/警告，而这些在 100 行已经返回过了，所以这段**永远不会返回**｜删除｜−5｜低
- **139–162**｜`try/except (TypeError,ValueError,KeyError)` 包住整个评估循环，把任何内部错误都变成 `malformed_physics_evidence`，会掩盖 bug。前面 131 行已经做了必需字段检查｜删掉 try/except。如果担心外部输入，只对 `r['finger_forces_n']` 的转换做边界检查｜−3｜中，test_outcomes 里可能有构造畸形输入的用例要更新
- **150**｜见 R7｜0｜—
- **165–166 `EXECUTION_SEMANTICS`**｜只有 curobo.py 用｜移到 curobo.py 或 R1 的 attempt.py｜0｜低
- **175–186 `timed`**｜通用计时器放在了判定模块里，而且特判 `category == 'simulation_and_physics_evidence'` 时累加 budget｜做成 `ContinuousContext.timed(category)` 方法（context、manipulation、curobo 都在用），budget 累加由调用 tick 的地方显式处理｜−2｜低
- **189–231 `pick_verdict`/`could_have_success`/`checkpoint_pick_success`/`hold_pick`**｜是“执行期的早停逻辑”，不是判定本身｜移到 `execution/context.py`，或者新建 `execution/pick_checkpoint.py`，outcomes 只留纯判定函数｜0（搬家）｜低

### outcomes/pick_state.py（37 行）

- RL 依赖它（`RL_PICK_THRESHOLDS`、`update_hold_window`），只能保留原样，不改。`PICK_THRESHOLDS` 是裸 dict，按理应改成 frozen dataclass，但 RL 的 torch 代码用 `thresholds['lift_m']` 和 `.get('enforce_relative_pose')` 读它，改了就要动 RL 文件本体，**这次不改**。

### outcomes/health.py（60 行）

- **49–60 `independent_results`**｜**没有调用方**，文档里说的“阶段一/二/三”也是旧流程｜删除｜−12｜低
- **6–46 `inspect_state`**｜只被 `scenes/stations/initialization.py` 用，属于“初态检查”｜见 R7。删掉 independent_results 后，health.py 只剩这一个函数。建议移到 `scenes/stations/initialization.py`（它的唯一调用方），删除 health.py｜−5（删一个文件）｜低

---

## 4. 目录与依赖建议（runtime 部分）

1. **outcomes/ 合并**：`pick.py` 和 `evaluation.py` 合成 `outcomes/task.py`（判定）；执行期早停逻辑和 `timed` 搬到 execution；`health.py` 并入 scenes/stations/initialization；`pick_state.py`（RL 共用）和 `contacts.py` 保留。outcomes 从 5 个文件减到 3 个。
2. **control/ 去掉不属于运行时的东西**：
   - `panda_omron.factory_spec/export_factory` 移到 scripts（资产导出工具）；
   - `dls.py` 移到 `runtime/rl/`（只改 RL 的一行 import）；
   - 旋转工具移到 scenes/geometry；
   - 新增 `joints.py`（JointMap）。
3. **修正几处依赖方向**：
   - `runtime.control.robot → data.export.molmobot_dataset.state22`：state22 搬进 control；
   - `runtime.control.robot → planning.robots.*`（`planning_model`、`SCALE`）：由 planning 按名字分派，SCALE 放进机器人配置；
   - `scenes.geometry.tcp_pose → runtime.control.interface.tcp_matrix`：函数体并入 geometry；
   - `data.rendering.head_views → runtime.control.panda_robocasa.rotation_matrix`：随旋转工具一起搬到 geometry；
   - `data.recorder` 直接写 `sim._raw_frame_cache`：改用公开接口。
4. **execution/**：新增 `attempt.py`（R1）；`navigation.py` 保留；`protocol.py` 吸收 Simulation 的状态存取（见 protocol 一节）。
5. `execution_config.py` 里的 `execution_profile/calibration_execution` 交给 workflows。

---

## 5. RL 依赖（这次不动，只改 import 行）

| RL 文件 | 依赖的 runtime 模块 | 本报告的建议对它的影响 |
|---|---|---|
| `runtime/rl/environment.py` | `runtime.control.dls.BaseFrameDLSCfg`、`runtime.outcomes.pick_state.RL_PICK_THRESHOLDS` | dls 搬家时改 1 行 import；pick_state 不动 |
| `runtime/rl/terms.py` | `runtime.outcomes.pick_state`（RL_PICK_THRESHOLDS、update_hold_window） | 不动 |
| `scenes/loading/rl_scene.py` | `runtime.simulation.Simulation`、`runtime.control.robot.load_robot_spec`，并读写 `sim.local_model` | 做 R3 后 `local_model` 变成 origin 字段。**rl_scene.py:98 直接写 `sim.local_model`**，那里除了 import 还要改一行赋值；如果严格只许改 import，R3 就要保留 `local_model` 这个可写属性（property setter） |
| `scenes/stations/rl.py` | `runtime.simulation.Simulation` | 构造函数签名变化（R3 加 `origin=`）会影响它。需要先看它怎么构造 Simulation，必要时给 `origin` 一个默认值 |
| `scenes/rl_observation.py` | `runtime.control.panda_robocasa.rotation_vector` | 旋转工具搬家时改 1 行 import |

结论：只有 R3 会碰到 RL 文件的非 import 行。处理办法是让 Simulation 继续保留 `local_model` 属性，`origin` 给默认值。其余建议都不影响 RL。

---

## 6. 建议的落地顺序与规模

| 顺序 | 内容 | 净减（估） | 风险 |
|---|---|---|---|
| 1 | 删死代码：follow_arm_points、independent_results、ACTION_LAYOUT、pick 的 legacy 循环、旧快照兼容、enable_third_person、`_state`、restoration 字段 | −110 | 低 |
| 2 | R2 substep 协议、R8 closed_on_error、R9 顶层 import、state.py 改用 mj_name2id | −75 | 低 |
| 3 | R3 Simulation 来源显式化和 derive | −60 | 低到中 |
| 4 | R4 和 R5（ctx 显式化、底盘几何统一） | −100 | 中 |
| 5 | outcomes 合并和 R7 | −50 | 中（标签合并要用户定） |
| 6 | R1 试验外壳 + VLABackend | −150 | 中 |
| 7 | R6 适配器（JointMap + NativeControl + 去继承） | −120 | 中 |
| 8 | R10 统一格式（单独提交） | ±0 | 低 |

合计预计 runtime（不含 RL）从约 5400 行减到约 4700 行（−650 到 −750）。函数长度和裸 dict 的数量下降更明显：最长的函数从 150 行（run_curobo_attempt）降到 40 行以内。

**需要用户拍板的点**：

- (a) 三个非有限标签合并成一个，会改输出；
- (b) 是否接受 R3 时 rl_scene.py 那一行赋值也跟着改；
- (c) `execution_config` 里锁死的四个字段改成常量后，configs/*.json 要同步删键；
- (d) 当前工作树的 rebase 必须先完成，才能开始改。
