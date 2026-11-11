# LastMileDataFlow 第二轮精简：目录分层与逐文件重构方案

对象：pro5000 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify`，分支 `refactor/simplify`，尖端 `bfd707d`（2026-11-12 00:20 变基完成，EGL 修复 `0f8648c` 已在分支里）。
方式：四路只读审查，不改代码，不运行程序。逐条依据（文件:行、改法、删减量、风险）见 `reports/simplify-refactor/analyze/` 下四份审查报告：

- `analyze/01-structure-workflows.md`：目录结构，workflows/scripts/config/gpu/utils
- `analyze/02-runtime.md`：runtime/（不含 RL）
- `analyze/03-planning-scenes.md`：planning/ 与 scenes/
- `analyze/04-data-policies.md`：data/ 与 policies/

## 一、总体判断

源码约 2.2 万行。上一轮已经删掉了任务模式、cli.py 和引用型数据集，剩下的问题主要有四类：

1. **同一件事写了好几份**。试验外壳（curobo/vla）、派生 Simulation（6 处）、yaw 差（4 份）、到达判定（5 处）、支撑面判断（5 处）、环境碰撞 geom 筛选（5 处）、分割掩码（5 处）、试次状态集合（3 份）、两个策略服务骨架、两套标定引擎。
2. **死代码还不少**，约 800 行。这些代码只有测试或冒烟脚本在用，或者完全没人引用。
3. **违反规范的写法**：150 行级别的长函数，裸 dict/元组当结构用，`SimpleNamespace` 伪造参数，闭包传依赖，函数内定义类，往实例上事后挂属性再 `getattr` 兜底，`except Exception` 把 bug 变成业务标签，约 35 处没有必要的函数内 import。
4. **六个领域目录互相循环依赖**，分组只是名义上的，没有形成分层。

按全部建议做完，源码预计减少 2600–3200 行（约 13–15%），测试减少 450–800 行。最长的函数从 230 行降到 40 行以内。

## 二、目录结构建议

### 现状

`from ..x import` 的引用次数如下，几乎每两个目录之间都是双向依赖：

| 从 → 到 | runtime | scenes | planning | data | policies |
|---|---|---|---|---|---|
| runtime | – | 26 | 8 | 10 | 4 |
| scenes | 15 | – | 2 | 3 | – |
| planning | 13 | 12 | – | – | – |
| data | 6 | 4 | 3 | – | 1 |
| policies | 4 | 3 | – | – | – |

典型的倒挂有：planning 的 pool/remote 引用 runtime 执行代码；控制层的 robot.py 引用导出层的 `state22`；scenes 引用 data/rendering 做可见性判断；recorder 与 lifecycle 互相引用；配置 dataclass 分散在六处。

### 建议：按依赖方向分层，只允许上层引用下层

```
lastmile_dataflow/
  config.py        全部配置 dataclass（并入 execution_config、RunResources，以及 scenes、benchmark 的配置）
  gpu.py  utils/
  geometry.py      纯 MuJoCo 几何和旋转工具（原 scenes/geometry + panda_robocasa 的旋转函数 + tcp_matrix）
  sim/             simulation.py、control/（机器人适配器 + JointMap）、contacts.py、
                   settle.py、state.py（原 construction 的物理部分）、
                   cameras.py（ThirdPersonCamera、CameraRig、ViewConfig、geom_mask、rendered_camera）
  planning/        curobo/、robots/、astar.py、result.py（原 waypoints.PlanResult），只放纯规划
  scenes/          loading/、tasks/（+task_support_obstacles）、stations/（+health.inspect_state）、
                   edits/（+construction/session 的编辑事务）、visibility.py（原 head_views）、dataset.py（两个文件合一）
  execution/       attempt.py（共享试验外壳）、curobo.py、vla.py、manipulation.py、
                   navigation.py（+原 planning/navigation/geometry.py）、context.py、lifecycle.py、protocol.py、
                   workers.py、planning_pool.py、remote.py、recorder.py、outcomes.py（pick+evaluation 合并）
  policies/        process.py（通用策略子进程 RPC）、serving.py（共用 serve()）、config.py、task.py、
                   molmobot/（client、service、multitask/）、pi05/、sac/
  data/            只放离线产物：evaluation/（calibration.py=selection+statistics、benchmark、recovery）、
                   export/、visualization/（plot.py 公共件）、analysis_video.py、replay.py（待定）
  workflows/       scene_datasets、station_map（标定全部内容）、collect（只做连续采集）、lastmilebench、rl_*
```

依赖方向：`config/gpu/utils/geometry → sim → planning → scenes → execution → data → workflows`；policies 只被 execution 和 workflows 引用。

### 落地方式

- **先精简、后搬家**。等各文件精简完，再单独提交一次迁移：只用 `git mv` 移动文件，不改逻辑。这一步不计入精简成果（AGENTS.md 也要求不以移动文件冒充精简）。
- 迁移以后，测试里的 patch 路径要整体改，同时把 `tests/workflows` 里二十多处 patch 私有函数的写法改成注入假执行函数、走公开接口。
- RL 文件这次不搬，只改 import 行。actor-critic 合并之后，再统一收进 `rl/` 包。
- pool.py 和 remote.py 用户之前定为保留，这次只是改个位置（移到 execution/），内容只做内部精简。
- 迁移以后，AGENTS.md 的目录职责表、`docs/architecture.md` 要同步更新。

## 三、单文件精简（按批次，前面的风险低）

### 第 1 批：删死代码，行为不变（约 −800 行，测试约 −300 行）

| 范围 | 删除内容 |
|---|---|
| runtime | `context.follow_arm_points`（SimpleNamespace+lambda，无调用方）、`health.independent_results`、`action.ACTION_LAYOUT`、`pick.py` 113–117 永不返回的 legacy 循环、`simulation` 旧快照兼容分支、`enable_third_person`/`vla._state` 等一行包装、从来不会被赋非空值的 `restoration` 字段 |
| planning | `astar.search`、`waypoints.arm_waypoint_action/navigation_action`（删掉以后 waypoints 就不再引用 runtime）、`V080_SHA`、`configuration(fixed=False)` 分支 |
| scenes | `sample_stations`、`record_station_visibility`、`yaw_variants`、`initialize_station` 的可见性分支、`placement_pose`、`EditAssetCatalog`、`SceneGraph.from_dict`、`select_stations`、`local_task_models`、`AssetRequest` 只有测试在设的字段、`target_moves` 整条链（恒为空）、`proposal_scene` 走不到的 no-edit 分支 |
| data | `edit_views.render_edit_pair/framing_points/in_frame`（文件只剩 ViewConfig 的 3 个字段和 CameraRig）、`replay.render_run/render_observations`、`head_views.camera_state/ray_visibility`、两份 `capture_sensors` 和深度分支、`lastmile_dataset` 里的 `observations_ref/record_depth/replayed`、benchmark 统计里的任务模式分支 |
| workflows | `policy` 参数（整条链上恒为 None）、旧引擎里不可达的 `prepared` 分支、`attempt` 适配键、`LocalConfig.output_dir`、未用的 `gpu_ids`、`build_scenes(workers=)`、`spatial_select` 只有测试在用的参数 |

**顺带修掉的问题**：

- `benchmark._counts(strict=True)` import 了一个不存在的函数 `mode_trial_evaluable`，一旦走到这个分支就会报 ImportError。
- `vla.py` 每个物理小步都新建一个 `ContactSampler`，缓存完全没起作用。
- `Simulation.render` 每帧都给每个相机算一遍完整的标定 dict，然后丢掉。

后两处修好以后，录制会变快。

### 第 2 批：合并重复，抽成共享函数（约 −400 行）

- **runtime**：
  - substep 检查统一返回 `str | None`（R2，顺带去掉 preparation 里的闭包）。
  - Simulation 的来源字段收进 `SceneOrigin`，派生统一走 `sim.derive()`（R3），6 处手抄来源字段和约 10 处 `getattr/hasattr` 一起消失。
  - 新增 `base_error/arrived/base_step_limits`（R5）。注意 `ContinuousContext.yaw_gap` 故意不回绕，不能并进去，改名为 `joint_yaw_delta`。
  - 非有限检查和躯干联动期望各留一份（R7）。
  - 用 `closed_on_error` 替换 5 处 `except BaseException: close; raise`（R8）。
  - 约 35 处函数内 import 提到顶层。
- **planning/scenes**：
  - `SupportRegion.contains`（替换 5 处）、`environment_geoms`（5 处）、`points_in_triangles`（2 处）、`is_load_bearing`（3 处）。
  - 统一用 `quat_yaw`、`base_local_xy`、`rotated_extent`。
  - astar 的 Dijkstra 主循环合成一份。
- **data/policies**：
  - `geom_mask`/`body_geoms` 替换 5 处分割掩码写法。
  - 试次状态统一成 `EVALUABLE`/`TERMINAL` 两个常量，selection 和 statistics 合并成 `calibration.py`。
  - 绘图公共件放进 `plot.py`（Agg、extent、字体缓存）。
  - pi05 的归一化和相机映射各留一份。
  - 两个策略服务共用 `serve()`；`Pi05Client` 不再继承 `MolmoBotClient`，改成组合 `PolicyProcess`。
  - `str(exc) == '...'` 改成异常类。
- **workflows**：
  - 新增 `open_scene()` 上下文管理器（替换 4 处）和 `navigation_grid()`（替换 2 处）。
  - 调度器里的基础设施失败字典、计划试验数的算法各留一份。

### 第 3 批：拆长函数，用 dataclass 代替裸 dict 和元组（约 −500 行）

| 位置 | 现在 | 改成 |
|---|---|---|
| `collect._run_dataset` | 230 行，靠 `process_scene is _collect_scene` 分 7 处支，外面三层原样转发 12 个参数 | `run_station_map()` / `run_collection()` 两个函数，共用 `_prepare_run()` 返回的 `StationRunContext`；`StationRun` 与 `RunResources` 合并成一个类 |
| `station_map._CalibrationScheduler` | 队列里是裸元组靠下标取；`__init__` 70 行就把进程全起来了；`close()` 77 行 | `StationJob`/`ReadyTrial` 两个 dataclass；用 `with self._started()` 管启动和收尾；`close` 拆成 4 个方法 |
| `execution/curobo.py` + `vla.py` | 各写一套建记录器、捕获异常、汇总结果的外壳；curobo 主函数 150 行；vla 靠 `self.pi05` 分 9 处支，`attribution` 是 4 层嵌套三元 | 新建 `attempt.py`，放 `AttemptSession`、`AttemptOutcome` 和 `classify()`（R1）；两个 VLA 后端做成 `VLABackend` 配置对象；`navigate()` 改成显式参数，`_PolicyNavigation` 删掉 |
| 机器人适配器 | 关节表写了 3 份；`PandaRoboCasaAdapter` 继承以后覆盖了 10 个方法；动作下标魔数出现 4 次；`apply_native` 改写适配器的私有成员 | `JointMap` 组合，加 `ARM_SLICE`/`GRIPPER_INDEX` 常量；`NativeControl` 自己持有状态；两张分派表合成一张 `ROBOTS`（R6） |
| `manipulation.py` | 十几个属性在 `__init__` 外创建，`for self.side in ...`，单字母属性 | `_plan()` 返回 `GraspPlan` dataclass，后续步骤以参数接收 |
| `planner.grasp/grasp_batch` | 约七成相同 | `_trial()` 上下文 + `_stages()` + `_common_diagnostics()` |
| `pool.PlanningService.execute` | 135 行，`request.inputs` 是十几个键的裸 dict | 拆成 3 个函数，加 `PlanningInputs`/`GraspLane` 两个 dataclass |
| `scenes/edits/search.BuildContext` | 没有 `__init__`，靠类方法往实例上挂属性，`except BaseException` 兜底 | 显式字段的 dataclass，构造时用 `ExitStack` |
| `scenes/loading/mjcf.py` | 函数里定义异常类，闭包改外层变量，靠异常控制流程 | 先扫一遍 `_needs_native()`，再用显式传参的 `_expand()` |
| 其他长函数 | `build_scene_graph` 160 行、`lastmilebench.build` 148 行、`_build_house` 103 行、`_recovery_trials` 100 行、`_select_tasks` 98 行、`render_scene_preview` 90 行（含闭包）、`discover_tasks` 90 行、`settle_scene` 77 行、`pi05_dataset.convert` 75 行、`analysis_video.frame` 60 行（3 个闭包） | 各拆成 3–5 个单一职责的函数；返回值改成 `NavigationResult`、`GraspCandidate`、`AssetGeometry`、`Placement`、`SettleReport`、`ContactSample`、`OscState`、`PolicyAction`、`RolloutResult` 等 dataclass，写盘时用 `asdict`，键名不变 |
| 异常处理 | `evaluate_pick` 用大 try/except 把内部错误都变成 `malformed_physics_evidence`；`scene_datasets` 父进程 `except Exception` 吞掉原始调用栈；`head_frame` 叠了三层 except | 只在外部输入的边界做检查，其余直接抛出。批量实验的失败隔离（worker 里逐条记录的那几处）保留 |

### 第 4 批：需要 GPU 对照（约 −330 行）

RBY-1 的 `configs/execution/curobo_v2.json` 没有设 `shared_station_preparation`，所以仍走旧的逐场景标定引擎，Panda 已经全部走新调度器。把 RBY-1 迁到新调度器以后，就可以删掉整个旧引擎和这个开关。迁移前要先用 GPU 跑一个 RBY-1 小场景，对照新旧两份 `trials.jsonl`。EGL 修复已经合入，具备运行条件，跑之前先查 GPU 占用，只用空闲的卡。

### 第 5 批：统一格式（单独提交）

curobo、navigation、state、pick、collision_world、analysis_video、lastmilebench 等文件用分号压成一行的密排，其他文件是 black 风格。统一跑一次 `ruff format`，并开启 E702/E703（禁止一行写多条语句）。这一批只改格式，单独提交，方便审阅。

### 第 6 批：目录迁移（单独提交），见第二节

## 四、已定事项（用户 2026-11-12 确认）

1. **目录分层**：认可第二节的分层，上层只引用下层。各文件精简完以后单独提交一次迁移（`git mv`，不改逻辑），同步 AGENTS.md 目录职责表、`docs/architecture.md`、tests 目录分组。
2. **head_fov 保留**。默认值改成 `False`，与配置一致；配置里不再写 `head_fov_enabled: false`。内部照常精简（planner 里的 FOV 分支收进少数几个方法，`tracing.lane_success` 只算一次等），功能和测试保留。
3. **RBY-1 迁到新调度器，删除旧标定引擎**，同时删除 `shared_station_preparation`、`station_workers` 和测旧引擎的测试。迁移前后用 GPU 跑一个 RBY-1 小场景，对照新旧 `trials.jsonl`。
4. **锁死字段改成代码常量**（`planner_backend`、`max_attempts`、`torso_error_policy`、`RobotConfig.name/namespace/planning_adapter`、`operation_retries` 上限等），结果文件照旧写出这些值。同时删除 `calibration_execution` 里已不存在的旧键。旧标定 run 不再保证可复用。
5. **允许改变输出格式**：三个非有限标签合成一个（`nonfinite_physics`，`SIMULATION_FAILURES` 保留对旧标签的识别）；planner 的小 JSON 合成一个 `planner.json`，每次查询只写结果和 solver trace；删除恒为 0 或常量的输出字段；删除 heatmap.json 的说明性字段（说明挪到 `docs/data-format.md`）；scene_datasets 总是写出 `edits` 和 `before_scene`，scene_preview 删除旧记录兼容。
6. **功能去留**：
   - 离线回放 `replay.render_rollout` 保留，补 `scripts/replay.py`（参数：rollout 目录、输出路径、相机、fps）；删除 `render_run`、`render_observations`。
   - `export_molmobot`、`UnsupportedMolmoBotData` 删除；`state22` 挪到机器人状态布局所在的 sim/control 模块。
   - `load_training_batch` 保留，删除深度部分和旧布局兜底。
   - π0.5 训练准备链路（`pi05_dataset.convert`、`Rby1DataConfig`/`Rby1Dataset`）保留并精简：`Rby1DataConfig` 改成显式字段，归一化与 `inference.normalize` 共用一份，相机映射只留一份，`convert` 的命令行挪到 `scripts/export_pi05.py`。`configs/policies/pi05_rby1` 的 `norm_stats` 指向已不存在的 `outputs/training/pi05-rby1-position/...`，在配置注释和 README 里标明“需先重新运行 export_pi05 生成统计”，不伪造路径。
7. **RL**：RL 文件只改 import 行，不搬家。Simulation 保留可写的 `local_model` 属性（并入 `SceneOrigin` 后用 property 暴露），`Simulation(...)` 新增参数给默认值，保证 `scenes/stations/rl.py`、`scenes/loading/rl_scene.py` 不用改。RL 依赖的接口清单见四份审查报告的 RL 一节。
8. **multitask/、pi05/core/ 移植代码**：只删确认无引用的函数（审查报告列了 11 个，逐个 grep 包括字符串引用后再删），不做结构改写。

## 五、配置改造

现状：30 个文件 772 行，全部是 JSON。

1. **改成 YAML**：`configs/**/*.json` 改为 `.yaml`，`config.read_config` 及各 `load_*_config` 统一用 `yaml.safe_load` 读取，`pyproject.toml` 声明 `pyyaml` 依赖。例外：
   - `configs/rl/val103_cup30.json` 保持 JSON（`rl_training.py` 直接 `json.loads`，属于 RL 文件）；
   - run 目录写出的 `config.json` 等运行产物保持 JSON；
   - 读取函数不再接受 `.json` 输入配置，不保留兼容层（RL 那份除外，它不经过 `read_config`）。
2. **删除或合并配置文件**：
   - `collection/` 两个文件只有 `execution` 一个键：删除这个目录，阶段脚本直接接收执行配置，`StationRun` 其余字段用默认值或命令行参数；
   - `execution/panda_omron_scene.json` 不是流程配置，挪到 `tests/smoke/` 由冒烟脚本自己持有；
   - Panda 的 4 份 cuRobo 执行配置（`panda_omron_curobo`、`_no_edit_`、`_all_cases_`、`_robocasa_`）几乎相同，按实际在用的机器人变体合成一份（先核实 `scenes/panda_omron` 引用的是哪份 robot 配置，差异只剩 `operations`、分辨率时用命令行或场景配置表达）；
   - `robots/panda_omron.json`、`robots/panda_omron_mobile.json` 核实没有流程引用后删除，同步改冒烟脚本和测试；
   - 6 个子目录 README 合成一份 `configs/README.md`。
3. **删除冗余字段**：
   - 与代码默认值相同的键（每份配置只写与默认不同的字段）；
   - 全部为 `false`/恒定的 `require_target_visibility`、`require_navigation_clearance`（字段本身随死代码一起从 dataclass 删掉）；
   - 改成常量的字段（第四节第 4 条）；`head_fov_enabled: false`（默认值已改）；
   - `local.example` 的 `output_dir`（`LocalConfig.output_dir` 删掉）；
   - benchmark 配置里已不是 `BenchmarkConfig` 字段的键（加载时报未知键，核实后删）。
4. **`data_root` 统一**：现有 `/data1/wenyifan/molmospaces_data` 和 `/data1/wenyifan/lmdf/molmospaces_data` 两种写法，核实实际数据所在（可能一个是软链接），统一成一个。
5. 同步 `docs/运行手册.md`、`docs/setup.md`、README、tests 里的配置路径。

## 六、执行顺序与验证

| 批次 | 内容 | 验证 |
|---|---|---|
| 1 | 删死代码（第三节第 1 批），顺带修三处问题 | 相关模块的单元测试 |
| 2 | 合并重复（第 2 批） | 相关模块的单元测试 |
| 3 | 拆长函数、dataclass、异常处理（第 3 批） | 相关单元测试 + 用已有 run 的 `config.json`/`summary.json`/`pair_index.jsonl` 对照键名 |
| 4 | 配置改造（第五节） | 配置加载测试 + 每份配置实际加载一次 |
| 5 | RBY-1 迁新调度器、删旧引擎 | GPU 集成对照：RBY-1 小场景新旧 `trials.jsonl` |
| 6 | 统一格式（`ruff format`，单独提交） | 只确认无语义改动（单元测试一次） |
| 7 | 目录分层迁移（单独提交） | 全量单元测试 + 一次小规模端到端（场景构建→标定→采集→benchmark）回归 |

验证规则：

- 验证规模与修改规模相称：简单修改只确认结果；局部修改运行相关测试；大规模修改做必要的集成或回归测试。
- 不重复验证已经确认的内容，不为简单修改运行整套测试。
- 单元测试用 `MUJOCO_GL=disable`。
- 需要 GPU 或渲染的运行：先 `nvidia-smi` 看占用，只用空闲的卡、单卡、少进程；不碰其他用户在用的卡（此前是 4–7）。EGL 设备映射已由 `0f8648c` 修复，运行时仍核对一次进程实际落在哪张卡。
- 测试改成走公开接口、注入假执行函数，不再 patch 模块私有名字；被删功能的测试随之删除。

提交：每批一个或几个提交，提交到 `refactor/simplify`，不推送，不碰 actor-critic 分支。每批完成后在 `reports/simplify-refactor/delivery/` 追加一份简短交付记录（改动、验证、结果、限制），全部完成后写一份总结。
