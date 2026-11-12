# planner 复用流程与目标几何错绑缺陷

- 状态：active
- 日期：2026-10-27
- 替代：无
- 关联：[成功率归因](01-low-success-analysis.md)、[全量统计](02-all-runs-trial-statistics.md)

## 1. planner 复用要解决的成本

`V2Planner.__init__` 每创建一个实例要完成：

1. 构造 `MotionPlannerCfg`，创建 cuRobo 的 IK / TrajOpt solver、rollout buffer 和碰撞世界（`curobo_v2.py:275-281`）；
2. `warmup(enable_graph=False, num_warmup_iterations=1)`（`curobo_v2.py:309-310`）；
3. 导出世界几何、做 FK 自检（`curobo_v2.py:189-206`、`290`）。

这些分配与编译是每个 trial 的固定开销。同一 GPU worker 会连续处理大量 trial，若每个 trial 都重建，GPU 侧初始化会吃掉可观时间。因此 `PlannerResources` 的设计目标是：

> “A GPU process retains at most two compatible solver families.”
> “Update measured locks, limits and world while retaining solver allocations.”

即：**保留 solver 分配，只热更新与本次 trial 相关的锁、限位和世界几何**。一个 GPU 进程最多留 2 个 planner（`curobo_v2.py:120-154`）。

## 2. 复用流程

### 2.1 进程与调度

- `PlanningPool` 每个 GPU 起一个常驻 worker 进程（`pool.py:185-208`）；每个进程只创建一个 `PlanningService`，其生命周期覆盖该 worker 处理的所有请求（`pool.py:159-182`）。
- `PlanningService` 持有两个长生命周期缓存：
  - `SnapshotCache`：最多 2 个冻结基线 sim，key = `(model_dir, target)`（`simulation.py:24-40`）；
  - `PlannerResources`：最多 2 个 planner（`pool.py:18-22`）。
- `dispatch()` 会**优先**把同一 `model_dir` 的请求发给上次处理该 model 的 worker，但如果没有该 worker 空闲，就发给任意空闲 worker（`pool.py:215-223`）。所以跨场景落到同一 worker 是常态，不是例外。

### 2.2 单次请求

`PlanningService.execute()`（`pool.py:28-107`）：

1. `baseline = scenes.get(model_dir, robot, task.target_body)` —— 取/建冻结基线；
2. `clone_sim(baseline, target=task.target_body, handle=...)` —— 新的 `MjData`，共享只读 model，绑定本次目标与把手（`context.py:28-38`）；
3. `request.state.initialize(sim)` —— 恢复执行侧的实测状态；
4. `planners.acquire(sim, options, side, directory, workspace=...)`；
5. 命中缓存则 `rebind_resources()`，未命中则 `V2Planner.__init__`；
6. 执行 `grasp_batch()`（preclose）或 `plan()`（closed/lift）。

### 2.3 复用的 key

```python
key = (sim.robot.config.model_path,      # 机器人模型
       sim.robot.config.control_profile, # 控制 profile
       side,                             # 左/右臂
       config.operation_base_mode,       # fixed / holonomic_joint_planning
       batch_size,                       # 批量大小
       config.num_ik_seeds,
       config.num_trajopt_seeds,
       config.max_candidates,
       ik_only)
```

（`curobo_v2.py:127-129`）

这组维度决定 solver 的**内存形状和采样规模**，形状相同才能复用分配。注意其中**没有** scene、target、handle、workspace。设计上这是有意的：这些属于“世界内容”，应由 `refresh_world()` 热更新，而不是触发 solver 重建。

## 3. 一个 planner 实例里哪些是场景绑定

`V2Planner.__init__` 在创建时绑定以下场景相关内容：

| 内容 | 初始化位置 | 复用时应否更新 |
|---|---|---|
| `self.sim` / `self.model` / `self.base` | `curobo_v2.py:174-185` | 是（rebind 已做） |
| `self.target_bodies = descendants(sim.model, sim.target_id)` | `curobo_v2.py:191` | **是（rebind 未做）** |
| `self.handle_bodies = descendants(sim.model, sim.handle_id)` | `curobo_v2.py:192` | **是（rebind 未做）** |
| 环境碰撞世界 `world` | `curobo_v2.py:193` | 是（rebind 通过 refresh_world 做） |
| target-only 碰撞世界 | `curobo_v2.py:194-195` | **依赖旧 `target_bodies`** |
| handle-only 碰撞世界 | `curobo_v2.py:196-198` | **依赖旧 `handle_bodies`** |
| `context['target_checker']` / `handle_checker` | `curobo_v2.py:199-202` | 对象可保留，但数据必须按新 target 重载 |
| `context['contact_links']` / `handle_contact_links` | `curobo_v2.py:203-204` | 对本机器人是常量 |
| solver / warmup / attachment | `curobo_v2.py:281-310` | 否（复用的目的） |

## 4. 缺陷是怎么产生的

### 4.1 rebind 的更新清单不完整

`rebind_resources()` 做的是（`curobo_v2.py:443-492`）：

- 更新 `sim/config/path/workspace`；
- 用新 sim 重建 `planning_model`，并把 kinematics 参数刷到所有 solver/rollout；
- 更新 `local_center/radius/movable_base`、`world_range`；
- 删除跨 model 的 `_carried_fit`；
- `refresh_world()`、`check_fk()`、`reset_motion_seed()`。

它**没有**重算 `self.target_bodies`、`self.handle_bodies`，也没有重建 checker 的目标身份。于是复用后：

```python
# curobo_v2.py:728-730
self.context['target_checker'].load_collision_model(collision_world(
    self.sim, path, self.world_range, reference_base=self.base,
    target_only=True, bodies=self.target_bodies - self.handle_bodies))
```

`bodies` 是**创建这个 planner 时那个 sim 的 body 数字 id**，被直接拿到当前 sim 上使用。注意不是“上一个任务的 sim 被搬了过来”——sim/model 每个请求都是新的，被沿用下来的只有 planner 实例里的这组整数 id（详见 4.2 节的任务边界与生命周期）。

### 4.2 任务边界与对象生命周期

这里“一次任务”= `scene_id + task_id`（某场景的某目标物体）。同一任务有多个站位、每站位 3 trial，它们共享同一份 baseline model，所以 body id 相同。

| 对象 | 生命周期 | 跨任务时 |
|---|---|---|
| `baseline` sim | `SnapshotCache` 按 `(model_dir, target)` 缓存，≤2 份 | 不同任务 `model_dir` 不同 → 新建，不会串用 |
| 请求用 sim | 每次 `clone_sim(baseline, target=...)` 新建 `MjData` | 每次都是当前任务的 model/target |
| `V2Planner` | `PlannerResources` 按不含 scene 的 key 缓存，≤2 个 | **跨任务复用同一个实例** |
| `planner.target_bodies` | `__init__` 时从当时 sim 计算一次 | **不更新，一直是创建时那个任务的 id** |

时间线示例（同一 GPU worker）：

```
任务 A（场景 S1，目标 vase）
  acquire → 未命中 → V2Planner.__init__(sim=A0)
      target_bodies = A 的 body id 集合
  release → planner.sim = None，planner 留在缓存

任务 A 的其他 trial
  acquire → 命中 → rebind(sim=A1)
      target_bodies 不变，仍等于 A 的 id → 正确

任务 B（场景 S2，目标 candle）
  acquire → 命中同一 key → rebind(sim=B0)
      self.sim = B0              ← sim 换成 B 的
      self.target_bodies         ← 仍是 A 的 id（没有更新）
      refresh_world()            ← 用 A 的 id 在 B0 的 model 里筛 geom

任务 C（场景 S3，目标 bowl）
  acquire → 命中 → rebind(sim=C0)
      target_bodies 仍是 A 的 id
```

因此准确说法是：**复用路径沿用“创建该 planner 时绑定任务”的 body id，而不是最近一次 rebind 任务的 id**。只有当某个请求恰好又回到创建时的那个任务，id 才重新变得有意义——这解释了全量中仍有约 1.7% 的 pick trial `target_world` 正确，而不是 100% 错误。

### 4.3 body id 在新场景里指向别的东西

`collision_world()` 的过滤逻辑是纯数字（`collision_world_v2.py:23-30`）：

```python
target = descendants(m, sim.target_id) if bodies is None else bodies
...
if ... or ((b not in target) if target_only else (b in target)) ...:
    continue
```

不同 scene 的 MJCF 编译顺序、body 数量、父子层级都不同。旧 target body id 在新 model 上只有两种结果：

1. **落在另一个合法 body 上** → target-only 世界导出成那个物体的几何；
   c005 中观察到 `pick-candle` 的 target world 是 `chestofdrawers`、`pick-vase` 的 target world 是 `window`。
2. **超出 `nbody`，或该 body 的 geom 全部被 robot / floor / contype / range 条件过滤** → 导出空世界；
   c005 中 340/704 个 pick trial 的 target world 为空。

handle checker 同理，只是它用旧 `handle_bodies`。

### 4.4 错误如何传导到各阶段

**preclose（goalset/grasp）**

- target checker 装错物体：`target_collision` 约束检查的是旁边的家具或别的物体。
  - 错物体恰好在目标附近 → 夹爪/手臂可能被误判碰撞。
  - 错物体不在附近 → 目标碰撞约束实际消失 → 抓取姿态可能穿入目标，但规划更容易通过，进入执行。
- 空 target world → 同样没有目标碰撞约束。
- **影响量级需要按数据修正**：全量 goalset 失败里，最大约束代价项 96%+ 是
  `constraints/scene_collision`，且 95%+ 落在 `omron_base`；`target_collision` 只在
  <0.2% 的失败里是最大代价项。因此错绑会失真 target 约束，但它不是 73% goalset
  失败的主因；该缺陷的主要可观测损失在后面的 lift 阶段。

**lift_actual**

- `attach_target_geometry()` 取：

```python
# curobo_v2.py:669
mesh = self.context['target_checker'].scene_model.mesh[0]
```

- target world 为空时，`SceneCfg()` 的 `mesh` 是空列表 → `IndexError: list index out of range`（全量 1,842 条）。
- target world 非空但是错物体时，用**当前目标的位姿**去变换**错误物体的顶点**（`curobo_v2.py:666-673`），再做 40 球拟合 → 携带几何完全错误 → 抬升判定碰撞 → `V2_POSE_FAIL`（全量 3,180 条）。

**open**

- handle checker 用旧 `handle_bodies` → `handle_contact` 约束检查错误把手；target checker 同样错。
- open 的目标位姿来自任务元数据（程序化 bbox pinch），**不依赖 checker**，所以错绑不会改变
  “位姿是否可达”本身；它影响的是接触/碰撞约束与抓取后的物理交互。当前数据里 open 的
  goalset 不可达主要由低把手（z<0.5 m）和底座碰撞解释，错绑是叠加因素而不是可达性来源。

### 4.5 为什么没有报错

整条链路只处理整数 id 和几何：

- `descendants()` 只做数字遍历（`geometry.py:8-12`）；
- `collision_world()` 只是 `b in target` 的集合判断，不校验“这个 body 是否属于本次 task”；
- 空世界返回 `SceneCfg()` 而不抛异常（`collision_world_v2.py:49-52`）；
- 只有 lift 阶段 `mesh[0]` 才第一次解引用这个空列表，所以表现为“抓取规划有时能过，抓完才崩”。

## 5. 根因归纳

> 复用本身没有问题；问题在于 **rebind 没有维护“每个 planner 实例的场景绑定”这个不变量**。

`__init__` 和 `rebind_resources` 是两套独立实现，前者建立了 target/handle 绑定，后者只复制了“由 sim 可推导的机器人参数”。于是复用路径和维护路径分叉，缺少的正是 target/handle 身份。由于 key 又刻意不含 scene/target，跨场景命中时这个缺口必然被触发。

数据上（2026-10-27 16:20 快照，按场景 XML 的 body 子树判定）：

- c005 的 10,914 个 planning query 中 10,882 个复用 solver（99.7%），只有 32 个新建。
- pick 的 `target_world` 正确仅 384/23,178（1.66%）：成功率 13.80%，已执行子集 39.85%；
  错误/空的 22,764 条成功率 0.53–0.77%，已执行子集 1.74–2.72%。
- open 必须看 `handle_world`：正确仅 117/35,136（0.33%），空 88.0%、错 11.7%。
- 单目标对照 run（复用不跨任务，60/60 绑定正确）成功率 61.67%；多目标 run（跨任务复用，
  39/59 空、17/59 错）成功率 3.39%，并出现 17 次 `IndexError`。
- 新建 planner 的 query 级 `target_world` 是正确的，但一个 trial 的 `preclose` 与 `pose`
  可能落到不同 planner，**不能再用 trial 级“是否出现过新建 planner”代表绑定正确**。

## 6. 修复方向

1. **首选**：把 `__init__` 中与场景绑定的一段抽成 `_bind_scene(sim)`，`rebind_resources()` 调用同一个函数，重算 `target_bodies` / `handle_bodies` 并重建（或重载）target/handle checker。
2. **兜底**：在 planner key 中加入 `(model_dir, sim.target_id, sim.handle_id)`，跨场景不复用；代价是牺牲跨场景的 solver 分配复用。
3. **防御**：`attach_target_geometry()` 在 checker 为空时显式抛出归类明确的错误，而不是让 `IndexError` 逃逸成 episode_error。
4. **可观测**：把 target world 的 geom 前缀与 task target 的一致性写入 planning 证据，便于批量审计。

推荐 1 + 3：既保留复用收益，又让复用和新建走同一条绑定逻辑。
