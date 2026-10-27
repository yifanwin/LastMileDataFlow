# 从 workflow 找到算法和执行

源码分为六个领域目录：**workflow 负责串流程，领域模块负责算法，runtime 负责实际运动，data 负责记录与结果。**
下面的代码树和流程图用于定位当前实现，不代表真实任务已经端到端验证。

| 目录 | 职责与主要入口 |
|---|---|
| `scenes/` | 场景/资产加载、任务发现、采样初始化、障碍物放置与场景物理检查；`dataset.py`，以及 `loading/`、`tasks/`、`stations/`、`augmentation/` |
| `runtime/` | 实际 stepping、机器人动作、构建事务、任务判定；`simulation.py`，以及 `control/`、`construction/`、`execution/`、`outcomes/` |
| `planning/` | cuRobo V2、碰撞/FOV、A* 和规划结果执行边界 |
| `policies/` | MolmoBot / π0.5 配置、服务与客户端 |
| `data/` | 根层 `recorder.py`；`rendering/` 图像视频、`evaluation/` 统计与选择、`visualization/` 图表、`export/` 模型导出 |
| `workflows/` | 场景构建、采集与 benchmark 的短编排 |

## 代码树：从入口找到实现

只列主要目录和阅读入口，省略 `__init__.py` 及辅助文件。路径相对于仓库根目录。

```text
src/lastmile_dataflow/
├── cli.py                         # 五个命令的参数与分发
├── config.py                      # 共用配置
├── utils/                         # 共用基础工具
│   ├── io.py                      # 读写与 run 创建
│   └── logging_config.py          # 统一日志格式与终端颜色
├── workflows/                     # 短编排，不放核心算法
│   ├── scene_datasets.py          # build_scenes：每任务共享准备与模式循环
│   ├── collection.py              # evaluate_stations / collect_lastmile
│   ├── station_calibration.py     # 全 run 标定调度：CPU 准备 / GPU 规划 / CPU trial
│   └── lastmilebench.py           # build / evaluate
├── scenes/                        # 场景、任务与候选站位
│   ├── dataset.py                 # 场景库读取与站位选择
│   ├── loading/                   # 场景与资产加载
│   ├── tasks/                     # discovery.py
│   ├── build/                     # 共享上下文、提案、事务检查与搜索
│   ├── modes/                     # 八类候选构造与共用几何代理
│   ├── stations/
│   │   ├── sampling.py            # sample_stations：初始化与可见性检查
│   │   └── initialization.py      # 站位初始化
│   └── augmentation/
│       └── checks.py              # 增强场景物理检查
├── planning/                      # 规划，不直接替代真实执行
│   ├── curobo/                    # cuRobo V2 适配
│   │   ├── planner.py             # V2Planner、PlannerResources、StationPlannerCache
│   │   ├── context.py             # PlannerContext
│   │   ├── costs.py               # ContactManager、FOV 代价
│   │   ├── tracing.py             # solver/phase 追踪
│   │   ├── collision_world.py     # MuJoCo → cuRobo 碰撞世界
│   │   └── head_fov.py            # 头部视野约束
│   ├── robots/                    # PandaOmron / RBY-1 模型与 assets/
│   ├── navigation/                # 几何导航与 A*
│   │   ├── geometry.py            # GeometryNavigation
│   │   └── astar.py               # S0→S1 路径搜索
│   ├── pool.py                    # 每 GPU 一个常驻规划进程
│   └── remote.py                  # CPU 侧规划 RPC 客户端
├── policies/                      # MolmoBot / π0.5 策略
│   ├── molmobot_config.py         # 策略配置
│   ├── molmobot.py                # 客户端
│   ├── molmobot_service.py        # 推理服务
│   ├── multitask/                 # MolmoBot 模型与推理实现
│   └── pi05/                      # 共用 JAX 核心、服务、观测与 RBY 数据配置
├── runtime/                       # 仿真、实际动作与判定
│   ├── simulation.py              # Simulation：生命周期与 stepping
│   ├── execution_config.py        # 执行配置与 profile
│   ├── control/                  # 动作协议与机器人适配
│   ├── construction/             # 构建会话、事务与快照准备
│   ├── execution/
│   │   ├── curobo.py             # run_curobo_attempt
│   │   ├── vla.py                # 两策略共用 run_vla_attempt
│   │   ├── context.py            # 连续执行上下文
│   │   ├── navigation.py         # 路径跟踪与到达检查
│   │   ├── manipulation.py       # cuRobo 操作执行
│   │   ├── preparation.py        # prepare_station：站位共享准备
│   │   └── workers.py            # CPU 准备/执行进程入口
│   └── outcomes/                 # 接触、健康与任务结果判定
└── data/                          # 实际执行证据与结果处理
    ├── recorder.py               # AttemptRecorder：轨迹、状态与图像
    ├── evaluation/               # 原始 trial 统计、S0/S1 选择、评测汇总
    ├── rendering/                # 图像与视频
    ├── visualization/            # 成败热图与导航图
    └── export/                   # lastmile_dataset 物化/batch 读取、MolmoBot 与 LeRobot/π0.5 导出
```

## 主要 workflow

- `scene_datasets`：目标粗筛与空间覆盖选择；每任务准备一次 BuildContext，按八类 intent 构造候选，由父进程写唯一主记录。
- `collection`：`evaluate_stations()` 单独跑逐点 rollout 与热图；`collect_lastmile()` 用匹配标定或在当前 run 标定一次，先落盘全量 S0×S1 计划和已有成功标定示教，再执行连续导航与操作。两个入口共享 run 准备、场景循环和真实执行器，没有 continuous/calibration_only Python 模式开关。
- `lastmilebench`：由完整标定选择困难 S0，保存 native 场景并独立执行策略。设备/profile 工具来自 `runtime.execution_config`，不依赖采集 workflow。

`sample_stations` 只做初始化几何检查（`static_only=True`）或再加头部可见性，不执行规划或任务 rollout；
`evaluate_stations` 才产生真实 rollout，两者不混用。
cuRobo 和 VLA 共用 `Simulation`、接触/任务判定及 `data.recorder`；原生 motor 和 position 的实际语义仍由各自适配器处理。

cuRobo 标定可按站位使用 spawn worker：每 worker 独立载入未开始的场景基线，每个 trial 仍经 `run_curobo_attempt` 新建仿真。父进程分配编号、汇总唯一的 `trials.jsonl`；共享故障通过进程事件停止新增试验。`StationPlannerCache` 每站位最多保留每臂一个闭爪前 planner，相同初态时重绑仿真、FOV 和日志，并为新 seed 重建原生种子管理器；闭爪后继续重建。`ContactSampler` 缓存静态名称与分类，每物理步读取一次接触力，供任务 monitor 和接触统计共用，详细遥测只在需要时生成。

`config.py` 和 `cli.py` 是包根的共用配置和薄入口；`utils/io.py` 管理读写，`utils/logging_config.py` 统一日志配置。CLI 仅五个核心命令，build-scenes 在内部调用发现/资格检查/构建；不保留旧 workflow 文件、旧命令别名或 Shell 包装。

## 总流程：五个命令如何连接 workflow

实线表示命令分发或主要处理步骤，虚线表示产物供后续命令读取；不是完整的 Python 导入依赖图。
图中场景库、标定结果和 benchmark 都是磁盘产物。

```mermaid
flowchart TD
    CLI["cli.py：五个核心命令"]
    CLI -->|build-scenes| BUILD["scene_datasets.build_scenes"]
    BUILD --> DISC["任务发现与空间覆盖选目标"]
    DISC --> CTX["每 task 共用 BuildContext"]
    CTX --> MODES["八类候选 → 几何代理 → 物理事务"]
    MODES --> SAVE["最多 20 合法姿态与三相机 ROI → freeze"]
    SAVE --> DATASET["场景库与候选站位"]

    CLI -->|evaluate-stations| EVAL["collection.evaluate_stations"]
    DATASET -.-> EVAL
    EVAL --> TRIALS["逐站位实际 rollout<br/>cuRobo / MolmoBot / π0.5"]
    TRIALS --> CAL["标定：原始 trials.jsonl 与成败热图"]

    CLI -->|collect-lastmile| COLLECT["collection.collect_lastmile<br/>仅 cuRobo"]
    DATASET -.-> COLLECT
    CAL -.->|可复用的匹配标定| COLLECT
    COLLECT --> EPISODES["S0→S1→操作<br/>pair_index / train_index 与实际执行记录"]

    CLI -->|build-bench| BENCH["lastmilebench.build"]
    DATASET -.-> BENCH
    CAL -.->|既有 cuRobo 标定，允许底盘移动| BENCH
    BENCH --> NATIVE["困难 S0、来源场景与隐藏恢复区域/路径"]
    CLI -->|evaluate-bench| POLICY["lastmilebench.evaluate"]
    NATIVE -.-> POLICY
    POLICY --> RESULT["VLA 独立执行、底盘诊断与分组 Macro"]
```

热图用于展示；S0/S1 配对和困难 S0 选择读取原始 trial，而不是平滑后的颜色。
benchmark 按来源计划使用完整站位的原始计数，不重新标定；构建不依赖策略或 native 场景转换。`data/evaluation/recovery.py` 负责共享区域和诊断，路径搜索复用 `planning/navigation/astar.py`。策略需要的 native 控制适配在评测初始化阶段进行；隐藏 oracle 不发送到策略进程。

## build-scenes：任务共享准备与八类候选

公开入口读取 `TaskModeRecipe` v2。House worker 只载入一次只读源场景，先按数据 grasp、支撑和地面粗筛目标，再按房间/支撑多样性与 XY 距离选目标。显式 targets 同时约束 Pick/Open。

```text
父进程 build_scenes → config.json
  └─ 每 house 一个 worker（spawn，最多 12 个）
      ├─ Source Simulation → discover_tasks → spatial_select
      └─ 每 task 一个 BuildContext
          ├─ clone → 停车/静置一次 → support/lattice/occupancy/clearance
          ├─ 离线 AssetSelector + 延迟真实几何缓存
          └─ intent × variant：
              提案 → cheap proxy → 少量资产 resolve → runtime 事务
              → 碰撞/静置/等时长对照/支撑 → 重测 proxy
              → hints/yaw/覆盖补选（最多 20 合法姿态）→ 三相机 ROI
              → final proxy → freeze / rejected
父进程唯一追加 scenes.jsonl → summary.json
```

`scenes/modes/` 实现 Reachability、Ground、Visibility、Arm-path、Approach、Narrow、Orientation、Articulated。构造器只提供候选与几何代理，不执行 IK/TrajOpt，不写最终标签。共有预算为每变体最多 32 个提案、3 个物理原候选、每个至多一次局部 repair；所有候选都从同一任务基线开始。

no-edit/base-pose 复用同一个 baseline 快照；对象编辑保存各自真实静置快照。最终初始化复用已经检查的名义姿态，仍单独测量三相机像素。相机与物理模型不混改；未知可见性保留 unknown。

普通候选的支撑/碰撞/无路径等负结果进入主记录。共享模型、渲染、I/O 或 worker 故障停止批次，父进程保存已返回真实记录及 infrastructure_error。旧 Case 构造器、在线 ThorLibrary 和中间 discovery/qualified 文件已退出。

## 标定：逐站位 rollout → 原始 trial → S0/S1 门槛

`evaluate-stations` 的主体是标定：逐站位 trial、原始记录与热图；`collect-lastmile` 先做同样的标定再用于配对。
三条实现路径（复用已有标定、全 run 调度器、逐场景串行/站位并行）共享同一份 `trials.jsonl`
格式；后两条路径由父进程唯一写入。

```text
evaluate-stations / collect-lastmile
  |
  +-- evaluate_stations(...)              collect_lastmile(...)（仅 cuRobo）
  |             |                                  |
  +-------------+----------- _run_dataset ---------+
                            |
                            +-- _prepare_run
                            |     数据集 config.json 的 geometry 键覆盖执行配置
                            |     load_scenes；逐场景 robot_config
                            |       + execution_profile + check_profile
                            |     --calibration：profiles 与 calibration_execution
                            |       必须匹配，匹配则直接读 trials.jsonl（prior）
                            |     create_run -> config.json
                            |       （dataset/config/profiles/resources/seed/calibration）
                            |
                            +-- 标定（prior 为空时）
                            |     shared_station_preparation=True 且 cuRobo
                            |       -> calibrate_stations（全 run 调度器，见下）
                            |     否则 _station_trials（逐场景）
                            |       station_workers=1  -> _station_records 串行
                            |       station_workers>1  -> _parallel_station_records
                            |         每 worker 常驻一个已载入场景的 Simulation
                            |         父进程唯一追加 trials.jsonl
                            |
                            +-- collect-lastmile：_plan_collection
                            |     pair_index.jsonl   全部 S0 x S1，status=not_executed
                            |     train_index.jsonl  已有 S1 直采成功示教
                            |
                            +-- 逐场景 process_scene
                            |     evaluate-stations -> _evaluate_scene（只标定）
                            |     collect-lastmile  -> _collect_scene（热图后走 A*）
                            |
                            +-- finally：summary.json
                                  status=completed 仅当全部场景处理且无共享故障
```

### 全 run 调度器：calibrate_stations

```text
calibrate_stations（父进程 = 唯一 trials.jsonl 写入者）
  |
  |  stations()：按场景与站位展开 TrialIdentity 和 TrialBudget
  |    每站位 trials_per_station 次（max_trials 可截断），
  |    seed = config.seed + index*tps + trial
  |
  +-- CPU 准备池（preparation_workers，CUDA_VISIBLE_DEVICES=''）
  |     prepare_job -> SnapshotCache 取基线 -> prepare_station
  |       ready   -> 生成 PlanningRequest(stage='preclose' 批量抓取规划)
  |       失败    -> preparation.json；shared 故障 -> stop
  |
  +-- GPU 规划池（PlanningPool：每卡一个常驻进程，pose 请求优先派发）
  |     preclose 批量：grasp_batch -> 每 trial 一条 PlanningResponse
  |       success    -> ready
  |       非 success -> 交准备池 record_unexecuted（只记录，不跑物理）
  |     pose 单次：CPU worker 运行中发来的分阶段规划，按 query_id 回投
  |
  +-- CPU 执行进程（cpu_workers，各自独立 MjData）
  |     execution_worker -> PlanningClient(绑定批量响应) -> run_curobo_attempt
  |     结果回 results 队列 -> save() -> append trials.jsonl
  |
  +-- 有界开窗
  |     buffered = active + ready + recordings + preparations + batches
  |     仅当 preparations < preparation_workers 且
  |       buffered + trials_per_station <= capacity 才取下一组
  |
  +-- 共享故障（failure_scope=shared / infrastructure_error / worker 退出）
  |     stop.set() -> 停止新增试验，未执行项补记 infrastructure_error
  |
  +-- finally：join 进程、关闭队列、恢复 CUDA_VISIBLE_DEVICES
  +-- 返回 trials + summary（planned/recorded/preparation_failures/peaks/gpu_active_s）
```

真正执行的 trial 都从 `run_curobo_attempt` 进入；split 模式下规划未成功的 trial
由 `record_unexecuted` 只写记录，不跑物理。

```text
run_curobo_attempt
  |
  +-- isolated_episode：建立 episode 作用域并统一关闭资源
  |   （attempt_id 由调用方分配）
  +-- clone_sim(baseline) -> place_frozen_station 或 prepared.state.initialize
  +-- AttemptRecorder 绑定仿真 -> initial_state.json
  +-- 有 path -> navigate（折线跟踪与到达检查）
  |   无 path 且未用 prepared -> 先 initial_settle，然后直接操作
  +-- manipulate_v2：cuRobo 规划并实际执行
  |   带 path 时失败按 operation_retries 重试，标定不重试
  +-- recorder.finish -> execution_termination / task_outcome /
                          recording_complete / replay_complete
```

### 标定结果如何变成 S0/S1

```text
trials.jsonl（一行一次 trial）
  |
  +-- station_calibration
  |     evaluable 状态：success / failure / planning_no_solution / timeout
  |     同一个站位重复 trial 编号 -> ValueError
  |     complete = 每个站位有效 trial 数 == trials_per_station
  |
  +-- eligible_stations
  |     complete=False -> 返回空（不用于困难配对）
  |     只看 geometry=='valid' 站位：rate = successes / trials
  |       S0: rate <= lastmile_low      S1: rate >= lastmile_high
  |
  +-- generate_pairs：S0 x S1 全组合，不在这步按路径或距离过滤
  |
  +-- 热图 aggregate -> figures/<scene_id>：只展示，不参与选择
  |
  -> collect-lastmile 在这里接 A* 与连续 attempt（见下节）
```

## 连续采集：先标定，再在同一次 attempt 中导航和操作

S0 是低成功率起点，S1 是候选高成功率操作站位。`collect_lastmile()` 当前只支持 cuRobo，
不会在连续采集中切换到 MolmoBot。每个 S0/S1 配对新建独立 attempt；进入实际 stepping 后不重置或传送。

```mermaid
flowchart TD
    START["collect_lastmile：加载场景与执行配置"] --> MATCH{"提供匹配标定？"}
    MATCH -->|是| REUSE["读取已有 trials.jsonl"]
    MATCH -->|否| CALIBRATE["当前 run 内逐站位执行 cuRobo 标定"]
    REUSE --> PAIR["由完整原始 trial 选择 S0/S1 配对"]
    CALIBRATE --> PAIR
    PAIR --> HASPAIR{"有可用配对？"}
    HASPAIR -->|否| EMPTY["保留无配对结果，不生成恢复 rollout"]
    HASPAIR -->|是| ASTAR["planning.navigation：真实几何站位图 + 房间 A* 连接"]
    ASTAR --> PATH{"找到路径？"}
    PATH -->|否| NOPATH["记录 path_unavailable<br/>不生成实际执行轨迹"]
    PATH -->|是| ATTEMPT["run_curobo_attempt<br/>新建 Simulation，在 S0 初始化并绑定 recorder"]
    ATTEMPT --> NAV["navigation.navigate<br/>通过实际 stepping 跟踪路径"]
    NAV --> ARRIVE{"到达 S1 且检查通过？"}
    ARRIVE -->|是| OP["manipulation.manipulate_v2<br/>cuRobo 规划并实际执行操作"]
    ARRIVE -->|否| FAILED["保留导航失败或执行异常"]
    OP --> OUT["汇总任务判定、执行终止与记录完整性"]
    FAILED --> OUT
    OUT --> WRITE["pair_index / train_index 与 attempt 记录"]
    NAV -.-> RECORD["Simulation.begin 与实际执行回调<br/>驱动 data.recorder 记录状态、轨迹与按配置启用的图像"]
    OP -.-> RECORD
```

虚线表示执行过程中同步记录，不是执行完再补造轨迹。导航到达、操作成功、执行终止和记录完整性分别报告，
不能用命令退出码或文件存在替代任务成功判定。

## 实际执行与记录入口

- cuRobo：`runtime.execution.curobo.run_curobo_attempt()`。
- MolmoBot / π0.5：`runtime.execution.vla.run_vla_attempt()`。
- 连续上下文与共用导航：`runtime.execution.context`、`runtime.execution.navigation`。
- 导航几何：`planning.navigation.GeometryNavigation` 在独立准备态中保持源关节姿态，以同一个 A* 搜索站位连接与按需生成的房间 XY/yaw 状态；20 mm 只参与软代价，粗包络栅格不作硬门槛。`NavigationSettings` 集中传递目标、权重、穿透阈值和转向折算比例；`runtime.outcomes.contacts.NavigationContacts` 共用于几何查询和两后端的显式导航判停。执行器直接跟踪 `poses`。`data.evaluation.recovery` 保存同一可行性口径的局部位姿区域，离线恢复统计复用该表示。
- 两个 attempt 入口显式接收 `root=...`、`attempt_id=...`；其他在用调用方已同步，不保留旧模块转发。

实际轨迹、初始状态与 PNG 由 `Simulation.begin()` / 实际执行回调触发 recorder；执行器在下发前设置 phase，不再组装一份 recorder 不使用的 before/after 状态。
场景采样仍可用构建期 runtime 能力，但不依赖 workflow；目录分组没有引入新的协议层或注册中心。

## 独立物化

`collect-lastmile --materialize-from` 直接调用 `data.export.lastmile_dataset.materialize_lastmile`，不载入仿真、资产或 GPU 后端。它按 train_index 复制唯一成功轨迹和必要观测，保留 pair 全集及来源，以内部相对路径交付。`load_training_batch` 读取原始 20D 动作窗口；不替代模型专用格式转换。

π0.5 控制与数据准备细节见 [π0.5 运行指南](../reports/pi05/docs/01-running.md)。Panda 路线 B 经 FK 和反馈跟踪产生 RoboCasa 动作，与模型共用 `runtime/control/panda_robocasa.py`；RBY-1 使用 position 动作。

## Panda 标定双池

`workflows/station_calibration.py` 调度全 run 的有界准备、GPU 查询与 CPU trial 队列，父进程唯一写原始 trial。`planning/pool.py` 的 spawn 进程拥有 CUDA、solver 和规划视图；`planning/remote.py` 在 CPU 侧将实测状态转为后续请求。

`runtime/execution/workers.py` 创建独立 MjData，并继续调用原 `run_curobo_attempt`、`ContinuousContext`、跟踪器和任务判定。`protocol.py` 传递数值状态、身份及累计活跃预算；`SnapshotCache` 仅在进程内保留少量只读模型。`data/rendering/replay.py` 使用独立 MjData 逐帧 mj_forward 回放，不参与任务成败；`rollout_video.py` 在同一实测边界组合三路相机与俯视底盘轨迹。
