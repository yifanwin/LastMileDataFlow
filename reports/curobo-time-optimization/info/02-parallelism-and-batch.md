# cuRobo 当前并行方式与 batch 使用情况

- 状态：active
- 日期：2026-10-25
- 替代：无（新增汇总）
- 关联：[当前状况](01-current-status.md)、[并行专项](../plan/02-parallel-execution.md)、[主方案](../plan/01-panda-omron-time-optimization.md)、[最终批次交付](../delivery/2026-10-24-val100-experiment.md)

## 结论速览

| 问题 | 当前答案 |
|---|---|
| 用到了 cuRobo 的 batch 模式吗？ | **用了，但只用于同站位闭爪前的抓取规划**：`BatchMotionPlanner`，`max_batch_size = trials_per_station`（Panda 配方为 3） |
| batch 之外怎么并行？ | 每张参与的 GPU 一个常驻规划进程；CPU 侧 60 个独立试验进程 + 12 个准备/记录进程，与 GPU 规划流水重叠 |
| 同一张 GPU 上并发几个 native 查询？ | **1 个**。不启用同卡多进程、多 stream 或 `multi_env` |
| 闭爪后规划是 batch 吗？ | **不是**。每个 trial 按自己的实测状态发一次 `B=1` 请求，使用 `MotionPlanner` |
| 跨站位拼 batch 吗？ | **不拼**。不同站位的初态、参考底盘和碰撞世界不同，共享世界 batch 不适用 |
| CUDA Graph 用了吗？ | **没有**，`use_cuda_graph=False` |

一句话：当前是“**多 GPU 进程 × CPU 试验进程池 × 单次 native batch（B=3）**”三层并行。batch 解决同一站位三次规划的并行，进程池解决不同 trial 的物理执行并行，两者同时存在但边界明确。

## 1. 并行分了几层

| 层次 | 并行单位 | 并发上限 | 主要实现 |
|---|---|---|---|
| GPU 进程层 | 一个 GPU 一个规划进程，一次一个 native 查询 | 等于 `--gpu` 数量 | `PlanningPool`、`_planning_worker`（`planning/pool.py:103,135`） |
| GPU 查询内 batch | 同一站位、闭爪前的全部 trial（通常 3 个） | `trials_per_station` | `V2Planner.grasp_batch`（`planning/curobo_v2.py:545`） |
| GPU 查询内 seed/goalset | 每 lane 的 IK / TrajOpt seeds 与候选目标集 | 32 IK seeds、4 TrajOpt seeds、最多 16 goalset | `MotionPlannerCfg.create`（`planning/curobo_v2.py:256`） |
| CPU 执行层 | 一个独立 MuJoCo `Simulation` + 一个 trial | 最多 60 | `execution_worker`（`runtime/execution/workers.py:72`） |
| CPU 准备/记录层 | 站位公共准备、未执行 trial 的数值记录 | 最多 12 | `prepare_job`、`record_unexecuted`（`runtime/execution/workers.py:27,39`） |
| 场景构建层 | 构建工作单元 | 最多 12 | `build_scene_dataset`（`workflows/scene_datasets.py:590`） |

前四层在同一次 `evaluate-stations` 标定中同时存在；构建层属于 `build-scenes` 的另一条链，不占用标定时的 cuRobo 规划资源。

## 2. cuRobo batch 模式：具体怎么用

### 2.1 什么时候进入 batch

`pool.py` 为每个站位的闭爪前请求计算：

```python
arguments = dict(workspace=(task['anchor_world'], config.radius_m),
                 batch_size=len(seeds))
```

`seeds` 是该站位的 trial 身份列表，长度等于 `min(trials_per_station, 剩余预算)`，Panda 配方下通常为 3。随后在 `V2Planner.__init__` 中：

```python
native_type = BatchMotionPlanner if batch_size > 1 else MotionPlanner
...
max_batch_size=batch_size, multi_env=False, use_cuda_graph=False
```

因此：

- `trials_per_station > 1` → `curobo._src.motion.motion_planner_batch.BatchMotionPlanner`；
- `batch_size == 1` → 普通 `MotionPlanner`；
- 两种 family 可在同一个 GPU 进程中按 LRU 各缓存一个（`PlannerResources`，`planning/curobo_v2.py:119`），key 包含模型、控制 profile、手臂、底盘模式、batch size、seeds 与候选数。

当前机器与配方下，每个 GPU 进程实际主要驻留两个 family：闭爪前 `B=3` 和闭爪后 `B=1`。不同批量大小会形成不同 key，不会把 2 个 trial 的请求塞进 3 槽 batch。

### 2.2 batch 内三个 lane 共享什么

一次 `plan_grasp` 的 batch 是**同一个站位的同一规划问题，重复 3 个随机解**，不是三个不同场景：

| 项 | 是否共享 | 说明 |
|---|---|---|
| 场景、目标、碰撞世界 | 共享 | `multi_env=False`，一个 `scene_model` |
| 参考底盘坐标 | 共享 | 同一 `reference_world` |
| 初始关节状态 | 共享 | `self.state(len(seeds))` 把同一状态复制到 batch 维 |
| 候选目标集 | 通常共享 | 当前 Panda 配方 `grasp_candidate_order=tcp_distance`，候选排序确定，3 个 lane 得到同一组最多 16 个目标 |
| 随机流 / seed | **不共享** | `bind_batch_seeds` 为每个 lane 建立独立的 core 随机流和 LM-IK 采样流（`planning/curobo_v2.py:48`） |
| 失败判定 | 不共享 | 每个 slot 单独 `success`，失败 slot 不进入物理执行 |

如果改用 `grasp_candidate_order=facing`，`candidate_pool` 会按 seed 做随机排列，此时不同 lane 的 16 个候选子集也可能不同；但**共享世界、共享初态**这一点不变。

### 2.3 batch 内实际求解的维度

`grasp_batch` 发起一次：

```python
result = self.motion.plan_grasp(
    self.batch_goals(matrix_batches), self.state(len(seeds)),
    grasp_approach_offset=-.08,
    plan_approach_to_grasp=True,
    plan_grasp_to_lift=False,
    disable_collision_links=[])
```

`batch_goals` 生成 `GoalToolPose`，形状约为 `[B, 1, 1, G, 3/4]`，`G = max_grasp_candidates`（Panda 配方为 16）。一次调用按阶段求解：

| 阶段 | batch 内行为 | 输出 |
|---|---|---|
| `goalset` | 3 个 lane × 最多 16 个候选目标做 IK/TrajOpt | 每个 lane 选中的 `goalset_index` |
| `pregrasp` | 3 个 lane 同时规划到接近位姿 | `approach` 轨迹 |
| `grasp` | 3 个 lane 同时规划到抓取位姿 | `grasp` 轨迹 |
| `lift` | **本次不规划** | 闭爪后按每个 trial 的实测状态单独请求 |

每个阶段内部使用 `num_ik_seeds=32`、`num_trajopt_seeds=4`；`NativeGraspPlanner.plan_pose` 把 `max_attempts` 默认设为 `config.max_attempts=5`。这些 seed、goalset 和 batch 维度都在同一份 GPU 张量里计算，属于**单次调用内部的并行**，不是 Python 线程并行。

`BatchMotionPlanner.plan_grasp` 的语义是：每个阶段对全部 B 个问题都求解；某个问题失败后，后续阶段用当前状态代替目标，避免优化器发散。因此 batch 不会因为个别 lane 失败而提前跳过其余 lane；失败只影响该 slot 的结果拆分与是否进入物理执行。

### 2.4 batch 结果怎么回到 trial

`grasp_batch` 按 slot 拆成逐 lane 的结果：

```python
for slot, seed in enumerate(seeds):
    success = bool(result.success[slot].item())
    ...
```

每个 lane 得到自己的 `success`、`goalset_index`、`pregrasp/grasp` 轨迹和诊断（含 `batch_slot`、`batch_seeds`）。随后：

- 成功的 lane 进入 `ready` 队列，由空闲 CPU worker 各领一个，成为独立 trial；
- 失败的 lane 由 `record_unexecuted` 在准备进程池中异步写数值记录，不占物理执行 slot；
- batch 的完整规划耗时会计入每个参与 trial 的 `planning_s`（所以逐 trial 规划累计不能当 GPU 忙碌总时长）；GPU 活跃卡秒则按请求只记一次。

### 2.5 闭爪后为什么不用 batch

闭爪后每个 trial 的实测关节状态、锁定值、附着几何和目标姿态已经不同。代码走另一条路径（`manipulation.py:128` 起）：

```python
closed_options = replace(ctx.config, operation_base_mode='fixed') ...
planner = factory(sim, closed_options, side, directory, workspace=...)
result = planner.plan(goal)
```

在拆分执行路径中，这对应 `RemotePlanner.plan` → `PlanningClient.query`（`planning/remote.py:36`），每次只带一个 identity，因此 `batch_size=1`、使用普通 `MotionPlanner`。这些查询仍进入同一个 GPU 池排队，但彼此是独立请求。

## 3. GPU 进程与调度

### 3.1 每卡一个常驻进程，一卡一次一个查询

`PlanningPool` 按 `--gpu` 列表 spawn 进程；每个进程：

```python
os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu),
                  OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
```

每个进程有自己的请求队列，父进程用 `active[index]` 记录占用，只有空闲 GPU 才会被 `dispatch()` 分配新请求（`planning/pool.py:156`）。所以：

- GPU 侧并行度 = GPU 数量；
- 同一张卡上不会同时跑两个 native 查询；
- 没有多 stream、没有同卡多 CUDA 进程、没有 `multi_env=True`。

### 3.2 调度优先级与模型亲和

`submit` 给请求分两级：

| 优先级 | 请求类型 | 目的 |
|---|---|---|
| 0 | `stage='pose'` | 已经执行中的 trial 等闭爪后规划，优先响应，减少“机器人停住等计划” |
| 1 | 闭爪前站位 batch | 新站位规划 |

同一优先级按入队序号 FIFO。`dispatch` 还会优先选择已经加载相同 `model_dir` 的空闲 GPU，减少场景/模型切换；没有匹配时再取任意空闲卡。

### 3.3 故障边界

- GPU worker 进程异常退出或返回 `infrastructure_error` → 整个池标记为 `shared` 故障，停止当前批次，不继续消费失效服务；
- 单个 lane 规划失败只是该 trial 的 `planning_no_solution`，不影响同 batch 其他 lane；
- 规划无解、超时、准备失败、执行失败分别记录，不互相冒充。

## 4. CPU 侧并行与流水

### 4.1 60 个执行进程

`RunResources` 默认 `cpu_workers=60`、`preparation_workers=12`（`runtime/resources.py:13`）。标定开始时：

- 60 个 `execution_worker` 进程，每个设置 `CUDA_VISIBLE_DEVICES=''`，不初始化 CUDA；
- 每个 worker 一次执行一个 trial，内部持有独立的 `Simulation`/`MjData`，负责导航、跟踪、接触与任务判定、记录；
- 父进程只做调度和唯一写 `trials.jsonl`，避免多进程写同一文件。

### 4.2 12 个准备/记录进程

`ProcessPoolExecutor(max_workers=12)` 负责：

- 站位公共准备：摆位、真实静置，生成供三次 trial 复用的初态；
- 未执行 trial 的数值记录：batch 中失败的 lane、准备失败或提前结束的 trial，异步写记录而不占物理 slot。

### 4.3 预取窗口

调度器用 `capacity` 限制在途工作：

```python
capacity = resources.cpu_workers + config.trials_per_station * len(resources.gpu_ids)
```

只有 `buffered + trials_per_station <= capacity` 时才继续提交新的站位准备。这样既能持续喂满 GPU，又不会把所有场景的模型、状态和记录一次性堆进内存。

### 4.4 规划与执行如何重叠

一个站位的时间线：

```text
准备（12 进程之一）
  → 闭爪前 batch（某张 GPU，B=3）
      → lane 成功：拆成 3 个 trial，分别交给空闲 CPU worker
      → lane 失败：准备池异步写数值记录
  → 每个 CPU worker 独立导航、执行 pregrasp/grasp
      → 闭爪后按该 trial 实测状态发 B=1 规划请求（GPU 池排队）
      → 收到计划后，同一个 CPU worker 用同一个 Simulation 继续
```

GPU 不必等待物理执行完成才接下一个查询；CPU worker 等待规划响应时，其他 worker 继续推进。这就是“规划/执行拆分”的实际含义。

## 5. 明确没有并行的部分

| 项 | 当前状态 | 原因 |
|---|---|---|
| 同卡多 native 查询 | 不做 | 每卡一个进程、一个活跃查询，避免多 stream/多上下文竞争 |
| 跨站位 batch | 不做 | 初态、参考底盘、碰撞世界不同，共享世界 batch 不成立 |
| 闭爪后多 trial batch | 不做 | 实测状态、锁定关节、附着对象已经分化 |
| 跨 GPU 拼 batch | 不做 | batch 在单个 GPU 进程内完成 |
| 单 trial 内导航→抓取→抬升 | 串行 | 有真实物理依赖，不能并行 |
| `multi_env=True` | 关闭 | 会失去共享世界与原生 PRM 路径；本轮不改变搜索方法 |
| CUDA Graph | 关闭 | 资源复用不依赖 graph；关闭后状态更新和世界刷新更直接 |
| 视觉记录与规划 | 数值模式不并行渲染 | numerical 模式只记必要数值；视频在标定后离线生成 |

## 6. 实测规模（val100 / 首阶段）

来自交付报告的原始 trial 统计，仅用于说明并行形态，不作为加速倍数：

| 指标 | 首阶段 val103 | 最终 val100 |
|---|---:|---:|
| GPU 数 | 2 | 2 |
| 闭爪前 batch 请求数 | — | 2,821（每请求 3 trial） |
| 闭爪后单 trial 请求数 | — | 1,047 |
| GPU 查询总数 | — | 3,868 |
| 原始 trial 数 | 60 | 8,463 |
| GPU 活跃累计 | 374.379 卡秒 | 20,368.130 卡秒 |
| GPU 同时查询峰值 | 2 | 2 |
| CPU trial 峰值 / 容量 | 8 / 60 | 18 / 60 |
| 准备进程峰值 / 容量 | 12 / 12 | 12 / 12 |

要点：

- GPU 查询峰值等于 GPU 数，说明**没有同卡并发查询**；
- CPU trial 峰值远低于 60，不是 CPU 池容量不够，而是很多 trial 在闭爪前规划阶段就失败，没有进入物理执行；
- 2,821 × 3 = 8,463，正好对应全部原始 trial，说明闭爪前 batch 是三次 trial 的唯一首次规划入口；
- 逐 trial 规划累计包含 batch 的三次完整归属，不能与 GPU 卡秒相加或相除。

## 7. 代码索引

| 关注点 | 位置 |
|---|---|
| GPU 规划服务、逐 lane 拆分 | `src/lastmile_dataflow/planning/pool.py:16,28` |
| GPU 进程、请求队列、优先级与模型亲和 | `src/lastmile_dataflow/planning/pool.py:103,135,151,156` |
| 原生 batch 选择与 planner 配置 | `src/lastmile_dataflow/planning/curobo_v2.py:212,256` |
| 逐 lane 随机流绑定 | `src/lastmile_dataflow/planning/curobo_v2.py:48` |
| batch 抓取规划与结果拆分 | `src/lastmile_dataflow/planning/curobo_v2.py:545` |
| 闭爪后单问题规划 | `src/lastmile_dataflow/planning/curobo_v2.py:667`、`planning/remote.py:36,72` |
| planner family 缓存与资源重绑 | `src/lastmile_dataflow/planning/curobo_v2.py:119,420` |
| 标定总调度、预取窗口 | `src/lastmile_dataflow/workflows/station_calibration.py:18,65,137` |
| CPU 执行/准备/记录进程 | `src/lastmile_dataflow/runtime/execution/workers.py:27,39,72` |
| 资源容量定义 | `src/lastmile_dataflow/runtime/resources.py:13` |
| 候选池与抓取目标 | `src/lastmile_dataflow/runtime/execution/manipulation.py:13,35` |

## 8. 边界说明

- 本文描述的是当前代码与 Panda 配方的实际行为；换 `trials_per_station`、`grasp_candidate_order`、GPU 数量或控制模式后，batch 大小、候选是否逐 lane 不同、缓存 family 数都会变化。
- “batch 内 3 个 lane”测的是同一初态下的执行系统随机性，不应表述为三种独立物理扰动。
- batch 成功只代表规划成功；是否任务成功仍由物理 rollout 与原始 trial 判定，不能用 batch 返回的 `success` 代替。
- 本文的并行数字与耗时来自已记录的交付报告；没有新的 GPU 实测，不声称新的加速倍率或主链闭环。
