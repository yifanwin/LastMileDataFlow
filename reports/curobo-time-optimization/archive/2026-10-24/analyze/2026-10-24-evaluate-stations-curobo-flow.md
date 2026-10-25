# evaluate-stations 与 cuRobo 流程讲解

- 状态：archived
- 日期：2026-10-24
- 替代：[当前文档](../../../info/01-current-status.md)
- 关联：[耗时分析](2026-10-20-scene-datasets-and-calibration-cost.md)、[时间优化方案](../plan/2026-10-22-time-optimization-plan.md)、[E03 结果](../delivery/E03-telemetry-parallel-planner-reuse.md)、[架构说明](../../../../../docs/architecture.md)

**2026-10-24 归档：保留当时的设置、结论与未验证边界；当前实验以 PandaOmron、每站位 3 次为准。本文中的旧预算与待办不作为当前方案。**

这份文档只回答一个问题：**输入一条 `evaluate-stations` 命令后，程序到底依次做了什么，cuRobo 在其中扮演什么角色。** 不要求读者懂 cuRobo。文中所有行号对应当前 worktree 的源码。

---

## 0. 先建立三个基本认识

1. **一次 trial = 一次真实的物理抓取。** 一个场景有约 20 个站位，每个站位跑 `trials_per_station` 次（默认 5），每次 trial 都从冻结快照重新克隆一个仿真，绝不重置正在跑的仿真。所以单场景是 20×5 = 100 次独立试验。
2. **cuRobo 只负责"算"，MuJoCo 负责"动"。** cuRobo 输出的是未来若干控制周期内底盘/手臂/躯干的目标关节序列；真正让机器人动起来、产生接触、判定成败的，是 MuJoCo 的物理步进。
3. **规划成功 ≠ 任务成功。** 抓取规划成功只说明"存在一条运动学上可行、无碰撞的轨迹"；物体有没有真的被抓起、抬起、保持住，要由逐 tick 的物理证据判定。

---

## 1. 术语表

| 术语 | 含义 | 在本仓库的位置 |
|---|---|---|
| station（站位） | 机器人底盘的一个冻结初始位姿 + 对应可见性检查通过的格点 | `scenes/stations/` |
| trial / attempt / rollout | 同义词：一次独立的物理试验，输出一个 `rollouts/<id>/` 目录 | `runtime/execution/curobo.py:23` |
| baseline 仿真 | 每个场景创建一次的只读源仿真，所有 trial 从它克隆 | `workflows/collection.py:450` |
| clone_sim | 用同一个 MuJoCo model 新建一份 `MjData` + 状态，得到可独立步进的新仿真 | `runtime/execution/context.py:28` |
| frozen station | 场景生成时固定的机器人初始关节/底盘状态，trial 开始时摆回去 | `scenes/stations/sampling.py:81` |
| cuRobo / MotionPlanner | NVIDIA 的运动规划库；这里固定 0.8.0，用 `MotionPlanner` 做 IK + 轨迹优化 | `planning/curobo_v2.py` |
| V2Planner | 本仓库的封装层：把场景/机器人导出成 cuRobo 能吃的格式，构造 `MotionPlanner`，再把结果转回 MuJoCo 关节序列 | `planning/curobo_v2.py:79` |
| IK | 逆运动学：给定末端抓取位姿，求各关节角 | cuRobo 内部 |
| TrajOpt | 轨迹优化：在 IK 解之间算一条满足速度/加速度/碰撞/自碰撞约束的平滑轨迹 | cuRobo 内部 |
| goalset | 一次给出多个候选抓取位姿，让 cuRobo 挑一个整体上可达的 | `manipulation.py:75` |
| tick / control tick | 一个控制周期，20 Hz（50 ms）；内部再细分 12–13 个 4 ms 物理子步 | `runtime/simulation.py:155` |
| 执行段 A / B | 闭爪之前的接近运动 / 闭爪之后的抬起与保持 | `manipulation.py:112-136` |
| planner_setup | 构造一个 `V2Planner`（含世界导出、Warp 编译、solver 初始化、自检）的计时项 | `planning/curobo_v2.py:81` |
| FOV | 头部相机视野约束；开启后规划还要保证目标在相机画面内 | `planning/head_fov.py` |

---

## 2. 调用链：从命令到物理步

```text
lastmile-dataflow evaluate-stations
└─ workflows/collection.py:486  evaluate_stations
   └─ :389  _run_dataset                     ← 准备、遍历场景、收尾
      ├─ :353  _prepare_run                   ← 读数据集配置、校验 profile、建 run 目录
      ├─ :450  Simulation.from_snapshot       ← 每个场景一个 baseline 仿真
      └─ :334  _evaluate_scene
         ├─ :186  _station_trials             ← 站位 → trial 循环，写 trials.jsonl
         │  └─ :68   _station_records
         │     └─ :41   _execute
         │        └─ runtime/execution/curobo.py:23  run_curobo_attempt
         │           ├─ context.py:28   clone_sim + sampling.py:81 place_frozen_station
         │           ├─ data/recorder.py:19  AttemptRecorder（每个 trial 一个目录）
         │           ├─ execution/navigation.py  navigate（仅 collect-lastmile）
         │           └─ execution/manipulation.py:35  manipulate_v2
         │              ├─ :13   candidate_pool（seed 决定候选与手臂顺序）
         │              ├─ :72   V2Planner 构造（或从 StationPlannerCache 复用）
         │              ├─ :84   planner.grasp  ← cuRobo 的 goalset→approach→grasp→lift
         │              ├─ :112  ctx.begin() + follow_points（执行段 A）
         │              ├─ :117  闭爪 20 tick
         │              ├─ :126  闭爪后重建 V2Planner
         │              └─ :133  抬起 + 保持（执行段 B）
         └─ :312  _station_summary            ← 统计、热图、S0/S1 配对
```

`_station_trials` 每拿到一条记录立刻 `append_row(root/'trials.jsonl')`（`collection.py:208`），所以中途崩溃也能看到已完成的 trial。全部结束在 `finally` 里写 `summary.json`（`collection.py:472`）。

---

## 3. 启动阶段（还没开始跑物理）

### 3.1 `_prepare_run`

`collection.py:353` 做四件事：

1. 读数据集 `config.json` 的 `geometry`，覆盖执行配置里的 `radius_m / spacing_m / width / height / min_target_pixels / map_resolution_m / navigation_*`。**目的是让标定用的几何和采样时的几何完全一致**，否则站位坐标对不上。
2. `load_scenes()` 只加载 accepted 场景；没有则直接报错。
3. 每个场景读它自己的 `robot` 配置，`check_profile()` 校验控制模式（position / native）、相机、策略与执行配置匹配，然后生成 `execution_profile`。若传入 `--calibration` 复用旧标定，profile 不匹配会直接拒绝，而不是静默混用。
4. `create_run()` 新建输出目录，把实际使用的 config、collection、profiles、GPU、seed 写进 `config.json`。

### 3.2 每个场景一个 baseline

`collection.py:450`：

```python
sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
```

这个仿真从 `.mjb` 快照加载整个房间和机器人，**只做两件事**：提供 `model`（只读，所有 trial 共享）和 `state_vector()`（初始状态，被复制到每个 clone）。它本身不 step、不渲染。一个场景的 100 个 trial 都从它克隆，这是"begin 之后不重置"语义的实现方式。

### 3.3 seed 与编号

```python
seed = config.seed + station_index * config.trials_per_station + trial   # collection.py:76
```

seed 决定三件互不相同的随机选择：

- 抓取候选的抽样顺序（`candidate_pool` 里的 `rng.permutation`）；
- 先试哪只手臂（`seed % 2`，偶数先左后右，奇数反过来）；
- 规划器 `random_seed`（影响 IK/TrajOpt 的 seed manager）。

`attempt_id` 由父进程 `count()` 统一分配（`r000000`、`r000001`…），并行模式下也由父进程发放，保证唯一。

### 3.4 并行模式（`--station-workers > 1`）

`collection.py:119` 的 `_parallel_station_records`：父进程把 `(站位索引, 一组 attempt_id)` 作为任务放进队列，每个 worker 进程**自己建一个 baseline 仿真**，跑完一个站位再领下一个。只有父进程写 `trials.jsonl`。worker 崩溃或报共享错误会 `stop.set()`，其余 worker 在下一个 trial 边界停下。默认仍是 1 worker。

---

## 4. 一个 trial 的完整生命周期（核心）

入口 `run_curobo_attempt`（`runtime/execution/curobo.py:23`），依次：

### 4.1 准备：克隆 + 摆位（`curobo.py:24`）

```python
sim = clone_sim(baseline, target=task['target_body'])
place_frozen_station(sim, station)
```

- `clone_sim` 复用 baseline 的 `model`（只读），新建 `MjData`，复制状态向量、控制量、时钟余数，然后 `mj_forward`。**不重新编译 XML/MJB**，所以这一步相对便宜，但它含 `deepcopy(catalog)` 和全状态复制，在 100 trial 规模上仍属于"未单列"的固定开销。
- `place_frozen_station` 把机器人 `initialize` 到该站位的冻结状态，然后恢复非机器人对象的 `qvel`，并断言"物体位姿没被改动"。此时 `sim.started == False`，所以允许设置机器人位姿；一旦 `begin()` 之后任何位姿设置都会报错。

### 4.2 记录器与执行上下文（`curobo.py:27-45`）

- `AttemptRecorder` 创建 `rollouts/<attempt_id>/`，写入 packet（配置、任务、站位、协议）。
- `ContinuousContext` 打开 `physics.jsonl.gz` trace，准备 `ContactSampler`、接触统计、目标 body 集合。
- `initial_state.json` 记录开始前的完整测量状态（机器人、目标、所有物体、qpos/qvel/ctrl）。

### 4.3 导航或静置（`curobo.py:52-58`）

- `collect-lastmile` 会传 `path`，先做 A* 导航到达 S1；
- `evaluate-stations` 不传 path，只按 `initial_settle_s` 空跑若干 tick 让物理稳定。

这也解释了一个细节：`curobo.py:60` 的重试次数是 `(operation_retries if path else 0) + 1`。**所以 evaluate-stations 里每个 trial 只执行一次操作尝试**，即使配置写了 `operation_retries: 1`。重试只在连续采集（有导航）时启用。

### 4.4 抓取规划：`manipulate_v2` 前半（`manipulation.py:35-96`）

**第一步，挑抓取候选**（`candidate_pool`，`manipulation.py:13`）：

- 从场景发现阶段得到的全部候选里，按"朝向评分"排序取前 `max(4×8, 16)` 个，再用 seed 打乱，取前 `max_grasp_candidates`（默认 4）个。
- 每个候选是一个 4×4 位姿：物体 body 位姿 × 局部抓取偏移。
- 这一步是纯 CPU 几何运算，耗时 < 0.1 s。

**第二步，决定先试哪只手**（`manipulation.py:38`）：`seed % 2` 决定手臂顺序；一只手规划失败就换另一只。

**第三步，构造规划器**（`manipulation.py:72-74`）：

```python
factory = cache.acquire if cache else V2Planner
planner = factory(sim, options, side, directory, workspace=(anchor_xy, radius_m))
```

默认 `reuse_planners=True`，走 `StationPlannerCache.acquire`（`planning/curobo_v2.py:51`）：

- 只有在 `sim.started == False`（还在冻结初态）且模型对象、状态向量与缓存时完全一致时，才复用上一个 trial 的 planner；
- 复用时 `bind_trial` 只更新 sim 引用、seed、FOV 校验器，保留世界几何与 solver；
- 不复用时新建 `V2Planner`。这就是历史耗时表里 `planner_setup` 11.19 s/次的主要来源（见 §5.1）。

**第四步，给候选加接近偏移**（`manipulation.py:75-79`）：沿工具 z 轴退回 `approach_offset_m`（默认 1 cm），再按 `grasp_roll_rad` 绕 z 轴转。这样 cuRobo 拿到的目标位姿是"抓取前的悬停位"，不是直接插进物体。

**第五步，`planner.grasp(...)`**（`manipulation.py:82-89`），内部调 cuRobo 原生 `plan_grasp`，四个阶段见 §5.4。返回 bool、选中的候选索引、各阶段关节轨迹、诊断。这段包在 `timed(ctx, 'grasp_planning')` 里。

若两只手臂都失败，抛 `OperationFailure(failure_reason)`。因为此时 `recorder.steps == 0`，最终 status 会被判为 `planning_no_solution`（`curobo.py:69`）——注意这是"没进入执行就放弃"，不是"试过但没抓起"。

### 4.5 执行段 A：接近与抓取前（`manipulation.py:97-116`）

规划成功后：

1. 取出选中的候选 `c` 和目标位姿 `goal`；
2. `pre = goal - 8 cm`（沿工具 z），作为预抓取点；
3. 建 `PickMonitor(side)`，记录初始事实（目标高度、允许的手指、支撑接触等），写 `task_evidence_initial.json`；
4. `ctx.begin()`（`context.py:78`）——这是不可逆的一步，之后禁止任何 reset；
5. `follow_points(planner, stages['pregrasp'], ...)`：**把 cuRobo 的关节轨迹逐点变成控制命令**。每个轨迹点内部是一个闭环：最多 120 次 `mobile_tick`，直到末端误差 ≤ 1.0（归一化比例）才进下一个点；最后 100 次迭代收敛到 1 cm / 0.06 rad 以内，否则抛 `tcp_tracking_timeout`。
6. `follow_points(... stages['approach'] ...)`：从预抓取点走到抓取点。

也就是说，cuRobo 给的是**参考轨迹**，执行时底盘、手臂、躯干每 tick 都在做"按比例逼近下一个参考点"的增量控制，而不是开环回放。

### 4.6 闭爪与闭爪后重建（`manipulation.py:117-129`）

```python
for _ in range(20):
    ctx.arm_tick(side, q, 0., h)      # 夹爪宽度 0，保持当前手臂角与躯干目标
```

固定闭合 20 tick（1 s）。随后：

```python
release()                              # 归还/关闭闭爪前的 planner
planner = V2Planner(sim, closed_options, side, directory, ...)
```

**闭爪后必须重建规划器**，原因有三：

- 手指关节此时被实际锁定，规划模型的自由度和指尖球体位置都变了；
- 抬起要从"实测闭合状态"起步，而不是规划时的理想抓取姿态；
- 抬起的轨迹要在"手上带着物体"的几何下重新做碰撞检查。

这一步的目录名是 `v080-closed-<plan_serial>-<side>`，与闭爪前的 `v080-<plan_serial>-<side>` 分开保存诊断。历史样本中闭爪后构造 24 次 = 24 个真正进入执行的 trial 各一次（其余 76 个在规划阶段就失败了）。

### 4.7 执行段 B：抬起与保持（`manipulation.py:130-136`）

- `attach_target_geometry()`：把目标物体的网格用 40 个球体拟合，挂到 `attached_object_<side>` 链节上，仅用于规划期的碰撞近似（**不是物理焊接**），写 `attached_object.json`；
- `lift = 当前 TCP + 10 cm`，`ctx.follow(...)` 先规划一次（计入 `motion_planning`）再跟踪执行；
- `hold_pick()`（`checks.py:60`）：最多 50 tick，每 tick 先做一次廉价必要条件检查，满足才跑完整判定；完整判定成功立即返回，不等满 50 tick。

`open` 操作没有 lift 段：闭爪后沿门把手轴向逐步推进，**每一步都 `refresh_world` 重建世界碰撞网格**（因为门在动），所以 open 场景的规划开销结构与 pick 不同（`manipulation.py:137-165`）。

### 4.8 结束、超时与失败分类（`curobo.py:60-109`）

一层层兜底，但每类结果语义不同：

| 触发 | status | 说明 |
|---|---|---|
| `hold_pick` 或执行中 `PickEvidenceComplete` | `success` | 物理证据完整通过 |
| `OperationFailure` 且没执行过任何 tick | `planning_no_solution` | 规划阶段放弃 |
| `OperationFailure` 已执行过 | `failure` | 执行中约束/跟踪失败 |
| 仿真发散/穿模等 `SIMULATION_FAILURES` | `simulation_failure` | 物理无效，不能算任务失败 |
| 墙钟超过 `attempt_timeout_s`（300 s） | `timeout` | 终止原因，不改变已有证据；会额外跑一次判定并标注 `recorded_trace_at_termination` |
| `InvalidAction` | `failure` / `control_limit:...` | 非法指令，本 trial 结束，不影响其他站位 |
| 其他异常 | 按阶段分类（初始化/执行/记录/共享资源） | 共享故障（如 GPU/服务）会中止整批 |

`deadline()`（`context.py:74`）在每个 tick 开头和每次规划前后检查墙钟，所以 300 s 是"软预算"：一次卡在 GPU 上的 `plan_pose` 可以越过它，下一个检查点才抛 `TimeoutError`。

### 4.9 finally：写盘（`curobo.py:110-148`）

- `ctx.finish()` 关闭 trace，写 `planning.json`；
- 写 `execution_diagnostics.json`：包含 `timings`（各计时项）、墙钟、仿真时间；
- `recorder.finish(status, reason)`：把逐 tick 样本压成 `trajectory.npz`，检查 `len(samples) == steps + 1` 判定记录是否完整；
- 再把 `recorder_finalize_wall_s` 补写进 diagnostics（所以这两项不计入 `timings`）。

---

## 5. cuRobo 内部到底在算什么

### 5.1 `V2Planner.__init__` 为什么贵（`planning/curobo_v2.py:79-280`）

构造一个规划器，实际做了：

1. **版本与设备检查**：`nvidia-curobo == 0.8.0`，必须有 CUDA。
2. **机器人模型**：`robot.planning_model(...)` 给出关节顺序、移动底盘变换、关节边界与耦合、工作空间半径。
3. **世界几何导出**（`collision_world_v2.py:12`）：遍历 MuJoCo 所有 geom，把 MESH 原样取出、BOX 转 8 顶点盒、球/椭球/圆柱/胶囊按曲面细分（球 320 面），排除地面和机器人自身，只保留距底盘 `world_range ≈ 2.59 m` 内的，合并成一棵 BVH。目标物体**另外**导出一份 `target_only` 的世界，给"允许接触目标"的检查器用。
4. **`MotionPlannerCfg.create(...)`**：把世界、机器人配置、`num_ik_seeds`、`num_trajopt_seeds`、`max_goalset`、自碰撞开关、`random_seed` 交给 cuRobo。这一步内部会做 Warp kernel 编译/缓存查找（进程内首次可能几百秒）、SDF/BVH 上传 GPU。
5. **FK 自检**：用 cuRobo 正运动学算一次 TCP 位姿，与 MuJoCo 的实测 TCP 比较，误差超限直接抛错（`fk_check.json`）。开 FOV 时还会核对头部相机的光学坐标系（`head_camera_fk_check.json`）。
6. **`warmup()`**：跑一次空规划，把 kernel 和内存池预热。
7. **给两个 solver 挂 tracing 包装**：记录每次 IK/TrajOpt 的成功、位置/旋转误差、约束项，写 `solver_trace.json`。这是纯观测，不额外增加搜索。

所以"11 秒/次"不是某一个慢函数，而是**世界几何 + solver 初始化 + 预热的一整套固定成本**。历史数据也显示 `planner_setup` 与场景顶点数几乎不相关（`corr ≈ 0.01`），说明它是固定开销而非规模相关。

### 5.2 机器人怎么表示

| 对象 | 表示 | 说明 |
|---|---|---|
| 世界 | 三角网格合并成单个 BVH | 运行时做 mesh SDF 查询 |
| 机器人 | 24 links / 86 spheres | `rby1m_holobase_spheres.yml` |
| 被抓取物 | 40 个 voxel 拟合球 | 只在抬起阶段挂载 |
| 目标接触检查 | 独立 `SceneCollision` | `target_only=True`，配允许手指白名单 |

一次 IK 种子集约 2 752 次距离查询（86 球 × 32 seeds），这是规划段 CPU/GPU 开销的根源。

### 5.3 `plan_pose`：IK + TrajOpt

一次普通位姿规划（`V2Planner.plan`，`curobo_v2.py:475`）：

1. **IK**：对多个随机种子并行求逆解，每个种子给出一组关节角候选；成功者通过位置/旋转误差与碰撞筛选。
2. **TrajOpt**：以当前状态为起点、IK 解为终点，优化一条 B 样条轨迹，代价含碰撞、自碰撞、关节限位、速度/加速度、工具位姿；开启 FOV 时再加"目标必须在头部相机视野内"的代价。
3. **输出**：插值后的离散关节位置 + 时间步长 `dt`。

`max_attempts=5`（`plans` 元数据里记为 `native_max_attempts`）表示 cuRobo 内部最多重试 5 轮，不是本仓库的重试。

### 5.4 `plan_grasp`：四个 phase

`planner.grasp`（`curobo_v2.py:370`）调用原生 `motion.plan_grasp`，阶段序列是 `goalset → pregrasp → grasp → lift`，每次进入某个 phase 时会回调 `on_query(phase)`，本仓库用它做三件事：

- `ctx.deadline()` + 规划次数预算（`max_operation_plans`，默认 12）检查，超了抛 `planning_budget_no_solution`；
- `plan_serial` 自增，保证诊断目录不覆盖；
- 写 `planning.json`。

各阶段含义：

| phase | 做什么 | 本仓库对应的执行 |
|---|---|---|
| `goalset` | 对全部候选抓取位姿求 IK，选出一个整体可达的 `goalset_index` | 无（只规划） |
| `pregrasp` | 从当前状态到抓取点前方 8 cm 的悬停位 | `follow_points(stages['pregrasp'])` |
| `grasp` | 从悬停位直线接近到抓取位（此阶段允许手指与目标接触） | `follow_points(stages['approach'])` |
| `lift` | 闭爪后带物体上抬 10 cm | 闭爪后由新 planner 重新规划 |

注意命名错位：cuRobo 的 `grasp` 阶段在本仓库里叫 `stages['approach']`，而 `stages['pregrasp']` 对应 cuRobo 的 `approach`。读代码时按 `curobo_v2.py:380` 的映射表理解。

`contact_allowed` 只在 `goalset / grasp / approach_contact` 三个阶段为真，其余阶段指尖碰撞目标就算失败。

### 5.5 从规划结果到控制点

1. `points()`（`curobo_v2.py:353`）把 cuRobo 的关节顺序重排成本仓库顺序（`ordered_points`），按 `interpolated_last_tstep` 截断；
2. `retime_points`（`rby1m_mobile_torso.py:99`）把原生 `dt` 轨迹按 `time_dilation`（默认 0.5，即放慢一半）线性插值到 20 Hz 控制时钟；
3. 校验轨迹不超出圆形工作空间，否则报错；
4. `follow_points`（`context.py:241`）逐点闭环跟踪：每 tick 计算底盘增量、手臂增量、躯干高度增量的比例，取最大者归一化，再发 `RobotCommand`。

### 5.6 几个为 cuRobo 0.8.0 行为打的补丁

这些不是业务逻辑，但会影响你看日志时的判断：

| 补丁 | 位置 | 原因 |
|---|---|---|
| `static_ik_state` | `rby1m_mobile_torso.py:112` | 原生 grasp 把上一段轨迹的 `dt` 传给静态 IK，导致 25 ms 内走 8 cm 被误判不可行；静态 IK 端点查询不该带时长 |
| `chain_state` | `:119` | 0.8.0 会把 ACTIVE-only 的 B 样条 knots 装到 FULL 关节名下，链式阶段再用会触发 CUDA 断言；knots 是优化控制量，不是测量状态 |
| `pad_target_query_range` | `curobo_v2.py:442` | 小物体的 no-hit 距离可能小于机器人球半径，造成恒定假碰撞且梯度为 0；只放大查询边界，不改几何 |
| `refresh_world` | `:458` | 开门的每一步都要重建世界网格；因为没用 CUDA graph，可以直接换 BVH |
| `detach` | `:399` | 每次规划结束必须清掉挂载几何与 FOV 偏移，否则污染下一次 trial |

---

## 6. 每个控制 tick 里发生了什么

`ContinuousContext.tick`（`context.py:96`）——耗时表里 34.3% 的那一项：

```text
tick(action)
├─ recorder.phase = 当前阶段
├─ 动作合法化（躯干限位、增量裁剪；越界写 adjustments.jsonl）
├─ deadline()                       ← 墙钟超 300 s 抛 TimeoutError
├─ begin()                          ← 首次调用启动仿真（不可逆）
└─ timed('simulation_and_physics_evidence')
   └─ sim.step()
      ├─ submit：动作 → ctrl
      ├─ 时钟余数决定本次 12 或 13 个 4 ms 物理子步
      └─ 每个子步：
         ├─ robot.update_control()
         ├─ mujoco.mj_step()
         ├─ substep_check：
         │   ├─ simulation_failure()（NaN/发散/穿模）
         │   ├─ mj_forward()                ← 为证据刷新派生量
         │   ├─ ContactSampler.capture()    ← 遍历全部 contact
         │   ├─ ContactStatistics.update_frame()
         │   ├─ PickMonitor.sample()        ← 高度、手指力、支撑接触、躯干…（91 次完整判定里的一部分）
         │   └─ 写 physics.jsonl.gz（--record-physics 时）
         └─ finally：
             ├─ mj_forward()
             ├─ recorder.executed() → capture()：
             │   ├─ 采样机器人状态（写 trajectory.npz 的样本）
             │   └─ sim.render() + observation() → 4 路相机 PNG（compress_level=1）
             └─ command 元数据（实际物理步数、完成度）
├─ physics.flush()
└─ checkpoint_pick_success()        ← 在 lift/hold 阶段检查能否提前判定成功
```

几个容易忽略的点：

- **物理子步检查是每子步做一次的**，不是每 tick：`mj_forward + contact 扫描 + monitor` 大约占带检查时间的一半（微基准 480 ms vs 226 ms/tick）。
- **图像写入发生在 `sim.step` 的 finally 里**，所以被算进 `simulation_and_physics_evidence`，而不是单独的 `rendering`。`--record-video` 才走 `rendering`/`recording` 两项。
- `record_images` 默认开（`config.py:115`），`--record-video` 默认关；`record_physics` 默认关，但关掉只是不写文件，行数据仍会构造。
- 一个 trial 的真实物理步数 = 实际执行的 tick 数 × 12/13，超时或失败会让实际步数远小于规划轨迹长度。

---

## 7. 结果判定：三个不同的"成功"

| 层级 | 字段 | 由谁决定 | 含义 |
|---|---|---|---|
| 执行终止 | `execution_termination.status` | `run_curobo_attempt` | 这次 rollout 是跑完、超时还是出错 |
| 任务结果 | `task_outcome.status` | `PickMonitor` + 完整判定器 | 物体是否真的被抓起并保持 |
| 记录完整 | `recording_complete` | `AttemptRecorder.finish` | 样本数与步数是否吻合 |

`pick` 的完整判定（`outcomes/evaluation.py`）检查：手指集合、抬升高度 ≥ `PROTOCOL['lift_m']`、无支撑接触、保持 ≥ `hold_s`。`checkpoint_pick_success` 与 `hold_pick` 只是"提前停止"的优化，判定本身仍走完整检查，不会因为便宜条件满足就判成功。

三种结果要分开报告：`planning_no_solution`（没进执行）、`failure`（执行了但没成功）、`timeout`（预算耗尽，证据按终止现场评价）。**退出码 0、文件存在、`execution_complete` 都不等于任务成功。**

---

## 8. 产物怎么读

```text
outputs/station_evaluations/<run-id>/<calibration-id>/
├─ config.json                    # 本次实际使用的配置、profiles、GPU、seed
├─ trials.jsonl                   # 每行一个 trial：status/reason/task_outcome/rollout_id…
├─ pair_index.jsonl               # 仅 collect-lastmile：S0→S1 计划与执行结果
├─ train_index.jsonl              # 成功 rollout 的训练索引
├─ summary.json                   # calibration_complete、eligible_pairs、elapsed_s
├─ figures/<scene-id>/success_heatmap_stations.svg
└─ rollouts/<attempt_id>/
   ├─ initial_state.json          # begin 前的完整测量状态
   ├─ v080-<serial>-<side>/       # 闭爪前规划证据
   │  ├─ planner.yml              # 机器人规划配置
   │  ├─ collision_world.json     # 导出的世界几何统计
   │  ├─ fk_check.json            # cuRobo vs MuJoCo FK 误差
   │  ├─ solver_trace.json        # 每次 IK/TrajOpt 的结果与约束项
   │  └─ grasp_result.json        # 四阶段结果、选中候选、失败原因
   ├─ v080-closed-<serial>-<side>/# 闭爪后重建的规划器（同上 + carried_geometry.json）
   ├─ attached_object.json        # 抬起时挂载的 40 球拟合
   ├─ task_evidence_initial.json  # 判定用的初始事实
   ├─ planning.json               # 规划查询序列
   ├─ execution_diagnostics.json  # timings、墙钟、仿真时间
   ├─ trajectory.npz              # 逐 tick 状态/动作/阶段
   ├─ events.jsonl                # 超时、异常、物理成功等事件
   ├─ physics.jsonl.gz            # 仅 --record-physics
   └─ images/<camera>/<step>.png  # 默认逐 tick 4 路相机
```

读耗时先看 `execution_diagnostics.json` 的 `timings`，再看 `grasp_result.json` 的 `queries`（每个 phase 的真实耗时）与 `status`。`solver_trace.json` 能回答"是 IK 没解还是轨迹优化失败"。

---

## 9. 把耗时数字对应回代码

| 耗时表中的项 | 对应代码 | 为什么是这个量级 |
|---|---|---|
| `planner_setup` 11.19 s × 198 | `manipulation.py:72`、`:126` → `curobo_v2.py:81` | 世界导出 + solver 初始化 + warmup 的固定成本；闭爪前可复用，闭爪后不可 |
| `grasp_planning` 8.60 s × 174 | `manipulation.py:84` → `curobo_v2.py:370` | 4 个候选 × 4 阶段，每阶段最多 5 次尝试；IK 种子集 × 86 球 × 网格 SDF |
| `motion_planning` 5.33 s × 24 | `context.py:222` → `curobo_v2.py:475` | 每次 `follow` 的单点位姿规划（抬起、开门每一步） |
| `simulation_and_physics_evidence` 2 840.2 s | `context.py:164` 包住的 `sim.step` | 8 838 tick × 每 tick 12–13 子步 × (物理 + mj_forward + 接触扫描 + monitor)；含同步 PNG |
| `task_validation` 0.42 s × 91 | `checks.py:32` → `evaluate_mobile_pick` | 完整判定只在满足廉价条件时触发 |
| 未计 1 552.6 s | `clone_sim`、`place_frozen_station`、`AttemptRecorder` 初始化、`context_finalize`、`recorder.finish` 写 npz | 每 trial 约 15.5 s 的固定开销 |

历史样本中的 174 / 24 是**未启用 planner 复用**时的构造次数。当前默认 `reuse_planners=True` 会把同站位、闭爪前、同侧、同冻结初态的构造压缩到每个站位每侧最多 1 次；闭爪后仍每个进入执行的 trial 重建 1 次。真实 GPU 上的复用收益尚未验收。

---

## 10. 常见误解与读日志要点

1. **"174 次 planner 构造 = 174 个 trial"**：不是。一个 trial 可能因为失败换臂而构造 2 次，也可能因为复用而不构造。
2. **`plan_count` 和 `plan_serial`**：`plan_count` 每个 attempt 归零，是本次尝试的规划预算；`plan_serial` 跨重试单调递增，用来给证据目录命名，避免覆盖。看到目录编号跳号是正常的。
3. **`status=timeout` 不代表失败**：超时是终止原因，`task_outcome` 会按终止现场的证据单独判定，可能是 `success`。
4. **`planning_no_solution` 与 `failure` 的区别**：前者一个 tick 都没执行；后者已经动过机器人。把它们都当"抓取失败"统计会高估真实执行失败率。
5. **evaluate-stations 不会重试操作**：重试次数取决于是否存在导航路径（`curobo.py:60`），标定场景没有 path。
6. **热图只用于展示**：困难配对始终读原始 trial（`collection.py:318` 注释），不要从热图反推起点。
7. **记录不完整会被改判**：`recorder.finish` 发现样本数不匹配时会把 status 改成 `recording_error`，即使任务本身成功。
8. **每个 trial 的仿真都是新的**：`clone_sim` 共享只读 model，但 `MjData` 是新的；不存在"上一个 trial 的物体还在手里"这种情况。

---

## 11. 自己跑一次最小可见的流程

```bash
# 先确认数据集和资产路径
.venv/bin/lastmile-dataflow evaluate-stations --help

# 单场景、单 worker、只跑少量 trial，便于逐目录对照本文
MUJOCO_GL=egl .venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/<run-id> \
  --assets-dir <assets_dir> \
  --config configs/execution/curobo_v2.json \
  --run-id inspect-flow \
  --samples <one-scene-id> \
  --max-trials 5 \
  --gpu 0 \
  --record-physics \
  --record-video
```

跑完按这个顺序看：

1. `trials.jsonl`：5 行结果的 status 分布；
2. `rollouts/r000000/execution_diagnostics.json`：`timings` 与总墙钟的差额；
3. `rollouts/r000000/v080-*-left/grasp_result.json`：四个 phase 的 `queries` 耗时和失败原因；
4. `rollouts/r000000/images/`：确认图像确实是逐 tick 四路；
5. 如果成功，`v080-closed-*/carried_geometry.json` 与 `task_evidence_initial.json` 能对上。

`--no-planner-reuse` 可以对照同一次运行中 `planner_setup` 的次数变化。

---

## 12. 证据边界

- 本文的代码路径与行号来自当前 worktree（`curobo-time-optimization`），不是主仓库现状。
- 174 / 24 次构造、8.60 s、11.19 s、2 840.2 s 等数字来自历史样本（small3 / pick 场景），当时未启用 planner 复用；当前默认配置下的计数与耗时尚未重新实测。
- 本文只解释流程，不构成性能结论；优化目标与验收状态以[时间优化方案](../plan/2026-10-22-time-optimization-plan.md)和 [E03 结果](../delivery/E03-telemetry-parallel-planner-reuse.md)为准。
