

## 问题梳理与重构计划

### 一、代码问题梳理

#### 1. P1 功能缺陷（阻塞标定）

**1.1 目标 body 集合未重新绑定**

**位置：** `curobo_v2.py:191–198`（init）、`484–486`（rebind）、`728–734`（refresh_world）

**问题：**
- `__init__()` 计算 `self.target_bodies` 和 `self.handle_bodies`（从 sim.target_id/handle_id）
- `rebind_resources()` 复用 solver 时**未重新计算**这两个集合
- 导致跨任务复用时使用首次任务的目标 body id
- 不同任务或同 model 不同目标的 descendants 不同，必须重新计算

**影响：** 标定实验方案 P1.1，Cup_30 队列末尾运行时使用错误的 target bodies

**1.2 Pick/Open checker 结构兼容性**

**位置：** `pool.py` 不相关，实际在 `curobo_v2.py:120–155` 的 `PlannerResources`

**问题：**
- `PlannerResources.acquire()` 的 key（127–129行）包含 9 个维度，**不包含 has_handle**
- Pick（无 handle_id）和 Open（有 handle_id）被视为同一兼容家族
- `curobo_v2_costs.py:40–43` 的 `ContactManager.initialize_from_config()` 根据 `context['handle_checker']` 创建 `handle_cost`
- Pick 首次创建时 `handle_cost=None`，Open 复用时仍为 None
- 反之，Open → Pick 会持有无效的 handle_checker 引用

**影响：** 标定实验方案 P1.2，Open 的 handle 碰撞检查失效

**1.3 携带物拟合缓存失效不完整**

**位置：** `curobo_v2.py:667`（attach）、`484–486`（rebind）

**问题：**
- `attach_target_geometry()` 检查缓存：`hasattr(self, '_carried_fit') and self._fit_model is sim.model`
- 只检查 model 对象身份，**不检查 target_id**
- 同 model 换目标时（如 Open 不同柜门）仍复用首次 fit
- `rebind_resources()` 部分处理了 model 切换（484行），但未覆盖同 model 不同目标

**影响：** 标定实验方案 P1.3，同 scene 不同目标使用错误的 mesh fit

---

#### 2. P0 代码规范问题（严重）

**2.1 函数内定义复杂类**

| 位置                        | 类名                 | 闭包变量                          | 行数 |
| --------------------------- | -------------------- | --------------------------------- | ---: |
| `curobo_v2.py:224–261`      | `NativeGraspPlanner` | owner, context, phases, query_log | 38   |
| `curobo_v2_costs.py:24–106` | `ContactManager`     | context（全局 dict）              | 83   |

**问题：**
- 无法在其他地方实例化或测试
- 调试困难，依赖隐式，违反"禁止在函数内部定义复杂类"规范

**2.2 核心逻辑依赖闭包**

- `NativeGraspPlanner.plan_pose`：通过闭包访问 `owner.phases`、`owner.query_log`、`owner.context`
- traced solver（318–366行）：通过多层闭包访问 `self` (owner) 状态
- `ContactManager.compute_costs`：通过闭包访问全局 `context` 字典

**2.3 单函数职责过多**

| 函数                 | 行数 | 职责数 |
| -------------------- | ---: | -----: |
| `V2Planner.__init__` | 220  | 10+    |
| `collision_world`    | 50   | 7      |

`V2Planner.__init__()` 包含：依赖检查、基本初始化、碰撞世界、FOV、NativeGraspPlanner 定义、Motion planner 创建、FK 验证、warmup、solver 追踪、日志写入。

---

#### 3. P1 维护性问题

**3.1 重复逻辑未抽取**

- 目标/把手 body 计算：分散在 init (191–198)、refresh_world (728–734)，标定方案要求合为一处
- trial 状态重置：`bind_trial()` (419–428) 和 `rebind_resources()` (479–482) 重复 5 行
- 碰撞世界构建：init 和 refresh_world 重复调用 `collision_world(...)`
- FOV target 计算：init (218)、bind_trial (433)、refresh_world (737) 重复
- ToolPoseCriteria 重置：grasp (570)、diagnostic_ik、grasp_batch (646) 重复

**3.2 目录组织平铺**

13 个文件全部在 `planning/`，缺少领域分组：
- cuRobo 适配：curobo_v2、curobo_v2_costs、head_fov
- 机器人模型：panda_omron、rby1m_mobile_torso（后者包含共享工具函数）
- 碰撞导出：collision_world_v2
- 多进程服务：pool
- 导航与工具：navigation、astar、waypoints、evidence、remote

缺少 README 说明职责（现有 README 只有 2 行注释）

---

#### 4. P2 轻微问题

**4.1 过度防御 try-finally**

位置：`grasp()` (566)、`diagnostic_ik()` (601)、`grasp_batch()` (640)

**判断：** 这些 finally 块实际**不是过度防御**，而是必要的：
- planner 会被复用跨 trial
- finally 确保异常时也执行 `detach()`、`ToolPoseCriteria` 重置
- 移除会导致异常后 planner 状态残留

**结论：** 保留现有 try-finally，不属于代码问题

**4.2 配置构建耦合**

`panda_omron.py:configuration()` 38 行混合了 spheres、碰撞忽略、锁定关节、dict 组装

---

### 二、重构计划

#### 阶段 0：准备（0.5 天）

| 任务                    | 交付                      | 时间   |
| ----------------------- | ------------------------- | -----: |
| worktree 建立 `.venv`   | `scripts/install.sh` 完成 | 0.2 天 |
| 运行现有测试基准        | 记录通过数                | 0.1 天 |
| 扩展 planning/README.md | 模块职责说明              | 0.2 天 |

#### 阶段 1：P1 功能缺陷修复（1 天）

**1.1 提取 body 计算函数（0.3 天）**

```python
# curobo_v2.py 新增
def _compute_target_bodies(sim):
    """从当前 sim 的 target_id 计算 descendants"""
    from ..scenes.geometry import descendants
    return descendants(sim.model, sim.target_id)

def _compute_handle_bodies(sim):
    """从当前 sim 的 handle_id 计算 descendants"""
    from ..scenes.geometry import descendants
    handle_id = getattr(sim, 'handle_id', None)
    return descendants(sim.model, handle_id) if handle_id else set()
```

**修改三处：**
1. `__init__()` 191行：`self.target_bodies = _compute_target_bodies(sim); self.handle_bodies = _compute_handle_bodies(sim)`
2. `rebind_resources()` **新增**（在 447 行 detach 后）：重新计算当前任务 bodies
3. `refresh_world()` 728行：使用 `self.target_bodies` 和 `self.handle_bodies`（已经是当前的）

**1.2 修复 PlannerResources key（0.3 天）**

```python
# curobo_v2.py:127
key = (sim.robot.config.model_path, sim.robot.config.control_profile, side,
       config.operation_base_mode, batch_size, config.num_ik_seeds,
       config.num_trajopt_seeds, config.max_candidates, ik_only,
       bool(getattr(sim, 'handle_id', None)))  # 新增：区分 Pick/Open
```

**1.3 修复携带物缓存（0.2 天）**

```python
# curobo_v2.py:attach_target_geometry
def attach_target_geometry(self, state=None):
    ...
    cache_key = (id(self.sim.model), self.sim.target_id)  # 新增
    reused = (hasattr(self, '_carried_fit_key') and 
              self._carried_fit_key == cache_key)  # 修改
    if not reused:
        ...
        self._carried_fit = fit
        self._carried_fit_key = cache_key  # 新增
        self._fit_model = self.sim.model
    ...
    if fit.num_spheres == 0:
        raise ValueError(
            f'empty carried-object fit for target_id={self.sim.target_id} '
            f'in {self.sim.model.ptr}')  # 修改：包含 target_id
```

**1.4 测试验证（0.2 天）**

扩展 `tests/planning/test_planner_reuse.py`：

```python
def test_different_model_same_body_id():
    """不同 model 中复用相同 body id，目标必须是当前目标"""
    ...

def test_same_model_different_target():
    """同 model 换目标，target world 和 fit 均更新"""
    ...

def test_pick_open_family_switch():
    """Pick/Open 家族切换，cost manager 不串用"""
    ...

def test_empty_target_error():
    """Pick attach 空目标明确报错"""
    ...
```

---

#### 阶段 2：PlannerContext 与类提取（1.5 天）

**2.1 创建 PlannerContext dataclass（0.3 天）**

```python
# curobo_v2.py 顶部
@dataclass
class PlannerContext:
    """V2 planner 运行时上下文，替代 dict"""
    target_checker: 'SceneCollision'
    handle_checker: 'SceneCollision | None'
    contact_links: list[str]
    handle_contact_links: list[str]
    contact_allowed: bool
    attached: bool
    local_center: Any  # torch.Tensor
    radius: float
    movable_base: bool
    fov_robot: Any = None
    fov_cost: Any = None
    fov_target: Any = None
    fov_tcp_frame: str | None = None
    fov_attached_offset: Any = None
```

**修改：**
- `V2Planner.__init__()` 200行：`self.context = PlannerContext(...)`
- `head_fov.py:target_camera_torch()` 88行：`context.fov_target`（保持 dict-like 访问语法兼容，或改为属性）
- tests：更新 `context['key']` 为 `context.key`

**2.2 ContactManager 提取（0.4 天）**

```python
# curobo_v2_costs.py 顶部
class ContactManager(RobotCostManager):
    def __init__(self, device_cfg, context: PlannerContext):  # 接收 device_cfg 和 context
        super().__init__(device_cfg)
        self.context = context
        self.target_cost = None
        self.handle_cost = None
        ...

# manager_type 返回工厂
def manager_type(context):
    def contact_manager_cfg_with_context(data_dict, **kwargs):
        original = RobotCostManagerCfg.create(data_dict, **kwargs)
        cfg = ContactManagerCfg(**vars(original), 
                                head_fov_cost_enabled=data_dict.get('head_fov_cost_enabled', False),
                                head_fov_endpoint_check=data_dict.get('head_fov_endpoint_check', False))
        # 注入 context：class_type 需要接收 device_cfg
        cfg.class_type = lambda device_cfg: ContactManager(device_cfg, context)
        return cfg
    
    @dataclass
    class ContactManagerCfg(RobotCostManagerCfg):
        head_fov_cost_enabled: bool = False
        head_fov_endpoint_check: bool = False
    
    return contact_manager_cfg_with_context
```

**2.3 提取 SolverTracker（0.4 天）**

```python
# curobo_v2.py 新增类
class SolverTracker:
    """追踪查询阶段与 solver 调用"""
    def __init__(self, names, batch_size, max_attempts):
        self.names = names
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.phases = None
        self.single_phase = 'pose'
        self.query_log = []
        self.solver_log = []
        self.query_index = 0
        self.progress_phase = 'pose'
    
    def next_phase(self):
        if self.phases:
            self.progress_phase = next(self.phases)
        else:
            self.progress_phase = self.single_phase
        self.query_index = len(self.query_log) + 1
        return self.progress_phase
    
    def wrap_solver(self, solver, kind: str):
        """包装 IK/TrajOpt solver.solve_pose"""
        original = solver.solve_pose
        def traced(*args, **kwargs):
            if kind == 'ik' and kwargs.get('current_state'):
                kwargs['current_state'] = static_ik_state(kwargs['current_state'])
            started = time.monotonic()
            result = original(*args, **kwargs)
            elapsed = time.monotonic() - started
            row = solver_evidence(result, kind, self.progress_phase, self.query_index,
                                  len(self.solver_log) + 1, self.batch_size)
            # 添加 position_error/rotation_error/feasible/metrics 等字段（从 318–366 行迁移）
            ...
            self.solver_log.append(row)
            return result
        solver.solve_pose = traced
```

**2.4 提取 NativeGraspPlanner 包装（0.4 天）**

```python
# curobo_v2.py NativeGraspPlanner 定义移至模块顶层
def create_native_grasp_planner(native_type, settings, context: PlannerContext, tracker: SolverTracker, owner):
    """工厂函数：创建注入追踪与上下文的 NativeGraspPlanner"""
    class NativeGraspPlanner(native_type):
        def plan_pose(self, goal_tool_poses, current_state, *args, **kwargs):
            phase = tracker.next_phase()
            tracker.progress_phase = phase
            tracker.query_index = len(tracker.query_log) + 1
            if owner.on_query:
                owner.on_query(phase)
            
            kwargs.setdefault('max_attempts', tracker.max_attempts)
            context.contact_allowed = phase in ('goalset', 'grasp', 'approach_contact')
            
            if phase == 'lift':
                owner.attach_target_geometry(current_state)
            
            started = time.monotonic()
            if owner.progress_ready:
                trial_progress('Planning phase=%s', phase)
            result = super().plan_pose(goal_tool_poses, current_state, *args, **kwargs)
            
            # FOV 验证、chain_state、查询记录（从 226–261 行迁移）
            ...
            
            tracker.query_log.append({...})
            return result
    
    return NativeGraspPlanner(settings)
```

**说明：** 由于 `MotionPlanner`/`BatchMotionPlanner` 需要动态选择，且 plan_pose 需要访问 owner 的 `attach_target_geometry`、`on_query`、`progress_ready`、`fov_validator`，采用**工厂函数**模式，在 `V2Planner.__init__()` 中调用 `create_native_grasp_planner(...)`。

---

#### 阶段 3：V2Planner.__init__ 拆分（1 天）

**目标：** 将 220 行拆分为 8 个私有方法，每个 < 50 行。

```python
def __init__(self, sim, config, side, path, *, workspace, batch_size=1, ik_only=False):
    self._verify_dependencies()
    self._init_basic_state(sim, config, side, path, workspace, batch_size)
    self._build_context()
    if config.head_fov_enabled:
        self._setup_fov()
    self._create_motion_planner(batch_size, ik_only)
    self._wrap_solvers()
    self._validate_fk()
    self._write_identity()

def _verify_dependencies(self):
    """检查 CUDA、cuRobo 版本"""
    ...

def _init_basic_state(self, sim, config, side, path, workspace, batch_size):
    """初始化 sim、config、base、model、tracker"""
    ...
    self.tracker = SolverTracker(self.model.names, batch_size, config.max_attempts)

def _build_context(self):
    """构建碰撞世界、目标/把手 checker"""
    self.target_bodies = _compute_target_bodies(self.sim)
    self.handle_bodies = _compute_handle_bodies(self.sim)
    world = collision_world(...)
    target = collision_world(..., bodies=self.target_bodies - self.handle_bodies)
    handle = collision_world(..., bodies=self.handle_bodies) if self.handle_bodies else None
    self.context = PlannerContext(...)

def _setup_fov(self):
    """FOV 配置"""
    ...

def _create_motion_planner(self, batch_size, ik_only):
    """创建 NativeGraspPlanner"""
    ...
    self.motion = create_native_grasp_planner(native_type, settings, self.context, self.tracker, owner=self)
    self.attachment = AttachmentManager(...)
    if not ik_only:
        self.motion.warmup(...)

def _wrap_solvers(self):
    """包装 IK/TrajOpt solver 追踪"""
    for kind, solver in [('ik', self.motion.ik_solver), ('trajopt', self.motion.trajopt_solver)]:
        self.tracker.wrap_solver(solver, kind)

def _validate_fk(self):
    """验证 FK 对齐"""
    ...

def _write_identity(self):
    """写入 planner_identity.json"""
    ...
```

**同时抽取公共逻辑：**

```python
def _reset_trial_state(self):
    """重置 trial 状态，供 bind_trial 和 rebind_resources 调用"""
    self.phases, self.single_phase, self.on_query = None, 'pose', None
    self.context.contact_allowed = False
    self.query_log.clear()
    self.solver_log.clear()
    self.fov_log.clear()
```

---

#### 阶段 4：目录重组（0.5 天）

**目标结构：**

```
src/lastmile_dataflow/planning/
├── __init__.py              # 保持现有导出
├── README.md                # 扩展：职责、子目录说明
├── curobo_v2.py             # V2Planner 主类（保持原位）
├── curobo_v2_costs.py       # ContactManager（保持原位）
├── collision_world_v2.py    # collision_world（保持原位）
├── panda_omron.py           # PandaOmron 模型（保持原位）
├── rby1m_mobile_torso.py    # RBY1 模型 + 共享工具（保持原位）
├── head_fov.py              # FOV（保持原位）
├── pool.py                  # 多进程服务（保持原位）
├── waypoints.py             # 规划结果（保持原位）
├── evidence.py              # 证据记录（保持原位）
├── navigation.py            # 导航（保持原位）
├── astar.py                 # A*（保持原位）
├── remote.py                # 远程客户端（保持原位）
└── assets/                  # 资产（保持原位）
```

**理由：**

1. **子目录移动成本高**：
   - 13 个外部文件导入 planning 模块（workflows、runtime、data、tests、scripts）
   - 更新所有导入路径需要验证 multiprocessing pickle、patch 路径、pyproject package-data
   - 移动 assets 会改变 ASSETS 常量、urdf_path 和冻结输入引用

2. **当前结构可接受**：
   - 13 个文件规模合理（不是 50+ 文件平铺）
   - 文件名已经表明职责（curobo_v2、panda_omron、astar）
   - planning/ 作为一个 cuRobo 适配 + 导航工具的集合包，不需要进一步细分

3. **重点在代码质量**：
   - 阶段 1–3 已经解决了核心问题（闭包、长函数、重复逻辑）
   - 目录重组收益小，风险高（破坏现有导入）

**本阶段任务：** 扩展 `planning/README.md`

```markdown
# planning

cuRobo V2 规划适配、机器人模型、碰撞导出和导航工具。

## 主要模块

- **curobo_v2.py**：V2Planner 主类，cuRobo 0.8.0 适配
- **curobo_v2_costs.py**：ContactManager，目标/把手接触掩码
- **collision_world_v2.py**：MuJoCo → cuRobo 碰撞世界导出
- **panda_omron.py**：PandaOmron V2 模型（URDF、spheres、FK）
- **rby1m_mobile_torso.py**：RBY-1 V2 模型 + 轨迹工具（ordered_points、retime_points 等）
- **head_fov.py**：head camera FOV 约束与验证
- **pool.py**：多进程规划服务（PlanningPool、PlanningService）
- **navigation.py**：几何导航（GeometryNavigation）
- **astar.py**：A* 搜索、占据栅格、距离场
- **waypoints.py**：PlanResult、GRASP_APPROACH_DISTANCE_M 等规划结果定义
- **evidence.py**：solver 证据记录（solver_evidence、phase_evidence、ik_probe_evidence）
- **remote.py**：远程规划客户端（PlanningClient、RemotePlanner）

## 使用示例

```python
from lastmile_dataflow.planning.curobo_v2 import V2Planner
from lastmile_dataflow.planning.pool import PlanningPool

# 单 planner
planner = V2Planner(sim, config, side='right', path=output_dir, workspace=(center, radius))
success, index, stages, diagnostics = planner.grasp(matrices, on_query=lambda phase: ..., lift=True)

# 多进程服务
pool = PlanningPool(gpu_ids=[0, 1], workers_per_gpu=1)
pool.submit(request)
result = pool.poll(timeout=1.0)
```

## 测试

```bash
.venv/bin/python -m unittest discover -s tests/planning -t . -v
```

## 依赖

- cuRobo 0.8.0（可选，V2Planner 需要）
- MuJoCo
- PyTorch（cuRobo 后端）

## 设计原则

- V2 pinned to 0.8.0：不维护多版本 cuRobo 兼容
- 机器人模型提供 URDF/spheres/bounds，planner 统一调用
- 碰撞导出 per-geom，不使用家具级 AABB
- 规划无解/执行失败分开处理，不混淆
```

---

#### 阶段 5：collision_world 拆分（可选，0.5 天）

**优先级：** P2，若时间不足可跳过。

**方案：** 提取 `_convert_geom()`、`_should_include()`、`_write_report()` 独立函数，精简主函数至 30 行。

---

#### 阶段 6：文档与验收（0.5 天）

**6.1 代码规范检查（0.2 天）**

| 检查项 | 标准 | 当前 | 目标 |
|---|---|---|---|
| 函数内复杂类 | 0 个 | 2 个 | 0 个 |
| 闭包核心逻辑 | 0 个 | 3 处 | 0 个 |
| 最长函数 | < 80 行 | 220 行 | < 50 行 |
| 重复逻辑 | 0 处 | 5 处 | 0 处 |

**6.2 验收报告（0.3 天）**

包含：
1. 修复的 P1 功能缺陷（3 个）
2. 解决的代码规范问题（闭包、长函数、重复逻辑）
3. 重构前后对比（curobo_v2.py 行数、类结构）
4. 测试覆盖（新增 4 个用例、所有测试通过）
5. 剩余技术债（planner 缓存扩展、checker 统一、碰撞球拟合）

---

### 三、总体时间线

| 阶段 | 工作量（天） | 里程碑 |
|---|---:|---|
| 0. 准备 | 0.5 | 测试基准、README 框架 |
| 1. P1 修复 | 1.0 | **任务绑定修复，测试通过** |
| 2. 类提取 | 1.5 | **无闭包依赖，4 个改造** |
| 3. V2Planner 拆分 | 1.0 | **__init__ < 15 行** |
| 4. 目录与文档 | 0.5 | **README 扩展** |
| 5. collision 拆分（可选） | 0.5 | collision_world < 40 行 |
| 6. 验收 | 0.5 | **交付报告** |
| **总计** | **4.5–5.5 天** | |

**关键路径：** 阶段 1 → 2 → 3 → 4 → 6

**可选路径：** 阶段 5（若时间不足跳过）

---

### 四、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| P1 修复破坏已有功能 | 中 | 高 | 小步提交，每步测试 |
| 类提取改变行为 | 中 | 中 | 保持接口一致，单元测试覆盖 |
| cuRobo class_type 构造不兼容 | 低 | 高 | 先验证 0.8.0 代码路径再改 |
| 时间不足 | 中 | 低 | 阶段 5 可跳过 |

---

### 五、后续技术债（不在本轮）

1. planner 缓存扩容或优化调度
2. checker 结构统一（空 handle checker 常驻）
3. 通用几何缓存（处理目标子树关节形变）
4. 碰撞球优化（底盘/柱体拟合，属于标定 P2/P3）
5. 参数化配置（buffer、activation 外部化）
6. panda_omron.configuration() 拆分

---

## 总结

**重点：**
1. **阶段 1 最高优先级**：修复任务绑定三个缺陷，恢复标定能力
2. **阶段 2–3 核心重构**：消除闭包依赖、提取类、拆分长函数
3. **目录不重组**：保持现有结构，降低破坏风险，聚焦代码质量

**交付物：**
- P1 修复 commit + 测试
- 类提取 commit（PlannerContext、ContactManager、SolverTracker、NativeGraspPlanner）
- V2Planner 拆分 commit
- 扩展 planning/README.md
- 验收报告

估算 **4.5–5.5 天**，关键路径 4.5 天。