# 架构

- 状态：active
- 日期：2026-11-12
- 替代：上一版（按 scenes/runtime/planning/data/policies/workflows 六个领域分组）
- 关联：../reports/simplify-refactor/plan/02-further-simplify-plan.md 第二节

源码在 `src/lastmile_dataflow/`，按 pipeline 组织：5 个流程阶段之外是 5 个各阶段共用的部件和 1 个工具目录。入口脚本只做参数和设备绑定，阶段编排放在各阶段的 `build.py`/`collect.py`/`evaluate.py`，实际推进仿真的只有 `sim/simulation.py`。

## 分层

只允许上层引用下层（含函数内 import），由 `tests/test_layers.py` 检查：

```
第 0 层  utils
第 1 层  sim/model
第 2 层  robots
第 3 层  sim（其余）
第 4 层  planning
第 5 层  operation        内部：episode、protocol、config → outcomes → control → recording → preparation、attempt、curobo
第 6 层  policies
第 7 层  scenes                     (a)(b) 场景
第 8 层  station_map                (c) 站位图  内部：config、sampling、aggregate、heatmap → planner_trials、rl（两者互不引用）
第 9 层  lastmile_dataset、lastmile_benchmark   (d)(e)，两者互不引用
```

跨顶层包一律写绝对导入（`from lastmile_dataflow.sim.simulation import Simulation`），同一顶层包内用相对导入；`__init__.py` 全部为空。cuRobo、torch、trimesh、robosuite、mjlab 这些可选重依赖在用到的函数里 import，主环境不装它们也能导入其余模块。

## 目录职责

| 目录 | 职责 |
|---|---|
| `utils/` | 不含任务语义、不依赖 MuJoCo：`io.py`（JSON/JSONL/YAML 读写、run 目录、仓库路径、`configs/local.yaml`）、`logging_config.py`、`gpu.py`（GPU/EGL 绑定）、`figures.py`（matplotlib Agg、栅格 extent、保存、CJK 字体） |
| `sim/model/` | 只读模型、不推进物理：`mjcf.py`（MJCF 读写与 include 展开）、`geometry.py`（几何与位姿、支撑面、房间地面三角形与地面射线） |
| `robots/` | 机器人。根目录放公共件：`registry.py`（`ROBOTS` 分派、`robot_config/load_robot_config`、`load_robot_spec`、`planning_model`）、`interface.py`（`RobotCommand`、`ROBOT_NAMESPACE`）、`joints.py`、`action.py`（20 维动作布局、限位、躯干联动）、`planning_common.py`。`rby1/`：`config.py`、`adapter.py`（含 `state22`）、`native_actions.py`（MolmoBot 原生控制）、`planning.py`；`panda_omron/`：`config.py`、`adapter.py`（`PandaBody` 与 position 适配器）、`robocasa.py`（RoboCasa 控制与相机出厂校验）、`planning.py`、`assets/` |
| `sim/` | 仿真本体 `simulation.py`（载入、推进、快照、渲染；`begin()` 之后不能恢复或重置；`OperationFailure`、`InitializationError`）、`source.py`、`state.py`（按名称迁移状态）、`settle.py`（静置、`SettleReport`、`clone_preparation`）、`native_scene.py`、`placement.py`（机器人落位、初态检查、`robot_contacts`、`robot_footprint`、`park_robot`）、`contacts.py`、`cameras.py` |
| `planning/` | `result.py`（`PlanResult`）；`curobo/`：cuRobo V2 规划器、`tracing`（含求解证据）、`costs`、`context`、`collision_world`、`head_fov`；`navigation/`：`astar.py`、`routes.py`（带朝向的几何导航）、`follow.py`（底盘沿路线跟随） |
| `operation/` | 站位上的单次操作试验，c、d、e 共用：`config.py`（`ExecutionConfig`、`CollectionConfig`）、`episode.py`（逐 episode 隔离、阶段、资源清理、异常分类）、`protocol.py`、`outcomes/`（算不算成功）、`control/`（怎么动：`context`、`manipulation`、`checkpoint`、`planner_pool`、`remote_planner`）、`recording/`（记下什么：`recorder`、`analysis_video`、`replay`）、`preparation.py`、`attempt.py`（两种执行共用的试验外壳）、`curobo.py`（`CuroboAttempt`：在一个站位上用 cuRobo 做一次操作，含重试） |
| `policies/` | 下游策略：`config.py`（`BACKENDS`）、`task.py`、`process.py`（策略子进程 RPC、`PolicyAction`）、`serving.py`（两个服务共用的 `serve()`）、`molmobot/`（`client`、`service`、移植的 `multitask/`）、`pi05/`（客户端、服务、推理、配置、观测、动作、归一化、训练数据视图 `data_config`、移植的 `core/`） |
| `scenes/` | (a)(b) 场景：`build.py`（阶段编排）、`library.py`（场景配方、场景库读取、按 scene_id 合并站位）、`graph.py`、`tasks.py`（任务发现、抓取候选、支撑障碍、采样圆）、`assets.py`、`edits/`（`placement`、`checks`、`search`、`session`）、`preview.py` |
| `station_map/` | (c) 站位图：`config.py`（`StationRun` 与命令行参数）、`sampling/`（第一步，候选站位：`build.py`、`sample.py`、`visibility.py`）、`aggregate.py`（实测率、选 S0/S1）、`heatmap.py`、`planner_trials/`（第二步旧方法：`build.py` 标定调度、`workers.py`）、`rl/`（第二步新方法：`env/`、`sac/`、训练、采样、评测、`devices.py` 等） |
| `lastmile_dataset/` | (d) 数据集：`collect.py`（配对规划与采集编排）、`continuous.py`（`ContinuousAttempt`：S0 → 导航 → S1 → 操作）、`export.py`（自包含导出与 `load_training_batch`）、`pi05_export.py`（转 π0.5 训练格式） |
| `lastmile_benchmark/` | (d)(e) benchmark：`summary.py`（`BenchmarkConfig`、困难起点、成功率汇总）、`recovery.py`、`build.py`、`policy_rollout.py`（策略闭环执行）、`evaluate.py`、`figures.py` |

## 各阶段调用链

`scripts/` 的子目录与源码顶层包同名，安装脚本在 `scripts/install/`（`main.sh`、`pi05.sh`、`rl.sh`）；另有 `scenes/build_asset_index.py`（THOR 资产角色索引）、`lastmile_dataset/export_pi05.py`（π0.5 训练数据转换）、`operation/replay.py`（离线回放）。

| 脚本（`scripts/` 下） | 编排 | 主要调用 |
|---|---|---|
| `scenes/build.py` | `scenes/build.build_scenes` | 每个 house 一个 worker：`scenes.tasks` 找目标 → `edits/search.BuildContext` 停放机器人、静置、算任务几何 → 原场景冻结；编辑场景经 `edits/placement` 摆放、`edits/checks` 物理检查后冻结。只看物理检查，不采站位 |
| `station_map/sample.py` | `station_map/sampling/build.sample_stations` | 逐场景重开快照 → `edits/search.task_geometry` → `sampling/sample.select_task_stations`（覆盖顺序、真实初始化、编辑场景测可见性）→ 写回场景库 `stations.jsonl`，再导出预览 |
| `station_map/planner_trials.py` | `station_map/planner_trials/build.run_station_map` | 调度器：CPU 准备（`operation/preparation`）→ GPU 规划池（`operation/control/planner_pool`）→ CPU 执行（`planner_trials/workers` → `operation/curobo.run_curobo_attempt`）→ `aggregate` 与 `heatmap` |
| `lastmile_dataset/collect.py` | `lastmile_dataset/collect.run_collection` | 复用或先跑标定 → 全部合格 S0×S1 → A* 路线 → `continuous.run_continuous_attempt` → `export` |
| `lastmile_benchmark/build.py`、`evaluate.py` | `lastmile_benchmark/build.build`、`evaluate.evaluate` | 按实测成功率选困难起点和恢复区域；评测时 `policy_rollout.run_vla_attempt` 驱动 MolmoBot/π0.5 |
| `station_map/rl/{train,score,evaluate}.py` | `station_map/rl/` | RL 站位图，独立环境（mjlab）；脚本先 `select_devices` 绑卡再导入 torch/mjlab，Sampler 子进程经 `devices.sampler_entry` 绑到各自的卡 |

## 关键约定

- 场景准备：每任务一个共享准备上下文。先确定真实支撑面、停放机器人、静置，再生成原场景和障碍编辑场景；没有八类模式、廉价模式代理或修复阶段。
- 站位：场景构建与站位采样分开。采样结果按 scene_id 合并进场景记录，下游只收一个场景库目录；没有合法站位的场景在采样这一步淘汰。
- 标定调度：CPU 准备缓存只读快照；GPU `PlanningPool` 持有规划视图与常驻求解器；CPU `PlanningClient` 把实测状态发回规划池。父进程是 `trials.jsonl` 唯一写者；`AttemptRecorder` 写一个 attempt 的真实动作、状态、RGB 和视频。
- 规划：固定 cuRobo 0.8.0，不维护多版本兼容。`PlannerResources` 每个 GPU 进程至多保留 2 个兼容求解器家族，跨任务复用时重算目标和把手 body。碰撞世界按 geom 导出（原语网格化，mesh 取凸包，保证封闭），不用家具级 AABB；目标、把手单独导出。规划无解与执行失败分开记录。
- 控制与结果：规划参考经本体适配、FK 与真实控制器执行，规划成功不算任务成功。抓取按 strict-pick-v3 的真实力、抬升、支撑、保持与滑移判定；执行终止和任务结果分开保存；普通躯干反馈偏差只记录，严重失控仍停止。
- 策略：不导入外部 MolmoBot 包；策略返回 20 维动作（Panda π0.5 为 12 维），仿真仍由 `sim` 推进。`configs/policies/pi05_rby1` 的 `norm_stats` 需先运行 `scripts/lastmile_dataset/export_pi05.py` 生成。
- 设备：在 CUDA/渲染初始化前绑定。`utils/gpu.py` 在干净子进程里查询 EGL 映射，spawn 的子进程继承显式映射；`CUDA_VISIBLE_DEVICES` 不能替代 EGL 绑定。
