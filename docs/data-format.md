# 最小数据格式

主记录用 JSONL，实验配置只保存一次。没有封存清单、校验摘要、重复索引或兼容旧协议的读取分支。

| 阶段 | 主记录 | 内容 |
|---|---|---|
| scene-datasets | `scenes.jsonl` | 场景 ID、accepted/rejected、相对场景路径、任务、站位、障碍物放置 |
| 逐点标定 | `trials.jsonl` | 场景/任务/站位、试验号、种子、状态、原因、相对 rollout 路径 |
| lastmile 配对 | `pair_index.jsonl` | 全量 S0×S1 计划、导航难度及执行结果，包含未执行和无路径 |
| lastmile 训练入口 | `train_index.jsonl` | 两类成功完整示教；引用型指向来源 run，物化后指向内部 rollout |
| benchmark 定义 | `episodes.jsonl` | 场景、任务、困难起点、种子 |
| benchmark 内部证据 | `oracle.json` | 每场景共享恢复区域、原始计数引用、可达路径、难度和分组；策略不可见 |
| benchmark 执行 | `results.jsonl` | episode 对应结果、终止与记录完整性 |

`config.json` 保存有效参数、种子、机器人/相机、后端或策略配置。`summary.json` 和热图是可重建的阅读产物，不作为协议入口。引用型生成读取原标定，不复制第二份 trials；独立物化仅复制训练索引所选的唯一轨迹与必需观测。

标定 run 的 `config.json.resources` 保存 `station_workers/gpu_ids/egl_devices/reuse_planners`，不参与物理执行配置匹配。新 trial 行包含原场景内的 `station_index`；seed 为 `config.seed + station_index × trials_per_station + trial`。并行时父进程预分配唯一 `rollout_id`，JSONL 行可按完成顺序排列，读取端不依赖行序。

## 输出目录文件树

以下为当前实现的产物布局，路径相对各自 run 根目录。主记录只有 JSONL；`index.html`、`previews/`、`figures/` 是可删除重建的阅读产物，不作为协议入口。

### 场景库 run（`outputs/scene_datasets/<run>/`）

```
<run>/
├── config.json                  # 生效配方：recipe / robot / geometry / seed，只写一次
├── scenes.jsonl                 # 主记录：每场景一行，accepted 与 rejected 都保留
├── summary.json                 # 构建概况：计数、每 house 来源统计、阶段耗时
├── index.html                   # 只读预览页，引用 previews
├── previews/<scene_id>.png      # 每场景一张组合图（派生预览，非相机输入）
└── scene-files/
    ├── <scene_id>/              # 每场景都建目录；rejected 目录为空
    │   ├── scene/               # 编辑后场景：model.mjb / initial.npz / config.json / model.xml
    │   └── before-scene/        # 仅 Case1/Case2（编辑场景）：同步静置、无新增物体的对照，文件同上
    └── <scene_id>-assets/       # 新增物体资产；只有 Case1/Case2 生成
        ├── catalog.json         # 资产目录：asset_id / xml_path / root_body / category
        └── <asset_id>/asset.xml # 单个 THOR 资产
```

`scene/` 与 `before-scene/` 内：`model.mjb` 是实际载入来源，`initial.npz` 保存初始 `state/state_spec/control_json/clock_remainder`，`config.json` 是该场景使用的机器人配置，`model.xml` 仅为可读副本。

`scenes.jsonl` 记录字段（提前终止的 rejected 记录可能只到 `scene_id/status/reason/...` 为止）：

| 字段 | 含义 |
|---|---|
| `scene_id` | 场景 ID，等于 `scene-files/<scene_id>` |
| `status` | `accepted` / `rejected` |
| `reason` | rejected 原因（如 `too_few_stations`、预算耗尽） |
| `seed` | 场景构建种子 |
| `case` / `house` / `task_id` | Case1/2/3、来源 house、任务 ID |
| `requested_count` / `actual_count` | 计划放置数 / 实际落地数量 |
| `candidate_attempts` | 候选放置尝试次数 |
| `physical_trials` | 物理静置试验次数 |
| `task` | 任务描述对象，见下表 |
| `placements[]` | 新增物体放置结果，见下表 |
| `station_candidates[]` | 全部候选站位（含被过滤项），与 `stations` 同结构 |
| `stations[]` | 最终采用的站位，采集端直接使用 |
| `scene` / `before_scene` | 场景目录相对路径 |
| `before_stations[]` | 对照场景的最终站位 |
| `build_unit_elapsed_s` | 该场景构建耗时 |

`requested_count/candidate_attempts/physical_trials` 在提前终止的记录中可能缺失；`actual_count`、`placements`、`stations`、`scene` 只出现在完整构建的记录，`before_scene`、`before_stations` 只出现在 Case1/Case2 的编辑场景。

`task` 对象：

| 字段 | 含义 |
|---|---|
| `instance_id` / `target_body` | 场景内实例 ID / MuJoCo body 名 |
| `asset_id` / `category` | 资产 ID / 物体类别 |
| `task_id` / `operation` | 任务 ID（`pick-xxxx` / `open-xxxx`）/ `pick`、`open` |
| `room_id` | 来源房间号 |
| `dimensions_m` | 物体尺寸（米） |
| `source_object_id` | 数据集内原始物体 ID |
| `center_world` / `anchor_world` | 物体中心 / 任务锚点的世界坐标 |
| `local_bounds` | 物体局部 AABB 的 min/max |
| `instruction` | 任务自然语言描述 |
| `support_obstacles[]` | 支撑面/障碍 AABB：`body/min/max/kind/evidence` |

`placements[]` 字段：`asset_id`、`category`、`point_id`、`point_xy`、`point_sources[]`（`kind/region_id`）、`pose`（`position` 与 `quaternion_wxyz`）、`yaw_rad`、`kind`（`surface` / `floor` 等）、`support_height_m`、`support_geom`、`support_instance`、`surface_coverage_verified`、`measured_size_m`（`width/depth/height`）、`world_bounds`（`min/max`）、`settled_pose`。

站位记录（`station_candidates` / `stations` / `before_stations` 及准备文件中的 `requested_station` 同结构）：

| 字段 | 含义 |
|---|---|
| `station_id` | 站位 ID（`S0006`） |
| `xy` / `grid_index` / `base` | 平面坐标 / 候选网格下标 / 完整初始 base `[x,y,yaw]` |
| `observation_camera` | 可见性检查使用的相机 |
| `initial` | 初始机器人状态：`base_xyyaw`、`lift`、`arm_q`、`gripper_width` |
| `geometry` | `valid` 或 `geometry_filtered` |
| `ground` | 底盘是否落地 |
| `collisions[]` | 初始化静置中记录到的接触，见下表 |
| `non_robot_qpos_unchanged` | 非机器人状态是否保持不变 |
| `visibility` | 目标可见性，见下表；仅检查初始化，不代表任务可行 |
| `initial_yaw_adjustments[]` | 初始朝向调整记录 |
| `independent_simulation` | 是否为独立仿真 |
| `check_scope` | 检查范围（`static_geometry`） |
| `initialization_attempt_id` | 与站位对应的初始化 attempt |
| `new_solver_warnings[]` | 新增求解器告警 |
| `reason` | 被过滤原因；`valid` 时为 null |

`collisions[]` 字段：`body_names`、`geom_names`、`geom_ids`、`distance_m`、`position_world`、`normal_force_n`、`time_s`、`robot_contact`、`allowed_floor_base`、`intended_target_contact`、`forbidden`、`kind`。
`visibility` 字段：`target`、`visible_pixels`、`image_fraction`、`bbox_xyxy`、`method`、`task_feasibility`。

`config.json`：`recipe`（场景发现、Case 规则、采样、增强与预算）、`robot`（本体/控制/相机）、`geometry`、`seed`。
`summary.json`：`status`、`accepted`、`rejected`、`excluded_targets`、`build_workers`、`gpu_ids`、`wall_elapsed_s`、`phase_wall_s`（`discovery_s/qualification_s/construction_and_previews_s/cleanup_s`）、`sources[]`（每 house 的 `discovered_task_count/eligible_pick/eligible_open/selected_pick/selected_open/pick_pairwise_distances_m/selection_method/elapsed_s`）。

### 逐点标定 run（`outputs/<calibration-run>/`）

```
<run>/
├── config.json                    # 生效配置、profiles、资源与 GPU 分配
├── trials.jsonl                   # 主记录：每 trial 一行
├── summary.json                   # 标定概况与失败清单
├── rollouts/<rollout_id>/         # 每个 attempt 一个目录
│   ├── trajectory.npz             # 实际执行轨迹
│   ├── recording.json             # 记录完整性与布局
│   ├── planning.json              # 本 attempt 的规划阶段日志
│   ├── initial_state.json         # attempt 起始实测状态
│   ├── execution_diagnostics.json # 耗时与 active budget
│   ├── task_evidence_initial.json # 初始任务证据（有实际执行时）
│   ├── attached_object.json       # 抓取几何拟合结果（发生过抓取规划时）
│   ├── events.jsonl               # 任务判定事件（当前仅成功判定写入）
│   └── verdict-<n>.json           # 失败判定协议快照（仅部分 attempt）
├── planning/<query_id>/           # 规划查询目录：<rollout_id>-batch 或 <rollout_id>-q<n>
│   ├── query.json                 # 查询身份与预算
│   ├── batch_grasp_result.json    # 批量抓取结果（-batch）
│   ├── solver_trace.json          # IK/TrajOpt 逐 seed 结果
│   ├── planner_identity.json      # cuRobo 版本、关节、碰撞与 FOV 设置
│   ├── planner.yml / mobile.urdf / mobile_base.json
│   ├── collision_world.json / target_world.json
│   ├── fk_check.json / target_query_range.json
│   ├── carried_geometry.json      # 单次查询（-q<n>）
│   └── planner_reuse.json         # 复用来源与作用域
├── preparations/<scene_id>/<station_id>/
│   ├── preparation.json           # 站位准备与实测初始状态
│   └── state.npz                  # 冻结的 initial state
└── figures/<scene_id>/
    ├── heatmap.json               # 热图说明
    ├── success_heatmap.npz        # 网格、成功率与权重
    └── success_heatmap_stations.svg
```

`trials.jsonl` 字段：

| 字段 | 含义 |
|---|---|
| `status` | 试验结论：`success` / `failure` / 终止状态（如 `planning_no_solution`） |
| `reason` | 终止原因原文（`mobile_pick`、`lift_contact_hold_or_slip`、`base_outside_workspace` 等） |
| `execution_termination` | `{status: not_executed/completed, reason, phase}`；完成≠任务成功 |
| `task_outcome` | `{status: success/failure/unknown, ...}`；unknown 不当作失败 |
| `recording_complete` / `replay_complete` | 数值记录 / 回放所需状态是否完整 |
| `observation_complete` | 视觉观测是否完整（数值模式下可为 false） |
| `executed_steps` | 实际执行的控制步数 |
| `navigation` | 导航结论；未请求时为 `{status: not_requested}` |
| `error` / `failure_scope` | 基础设施错误及作用域（`episode` / `shared`） |
| `scene_id` / `task_id` / `station_id` | 场景、任务、站位 |
| `station_index` / `trial` / `seed` | 站位内下标 / trial 序号 / 实际种子 |
| `rollout_id` | 对应 `rollouts/<rollout_id>/` |

seed 由 `config.seed + station_index × trials_per_station + trial` 得到。`task_outcome.status=unknown` 的行仍可能保留轨迹（例如执行到一半后判定未知），统计时按未知处理。

`trajectory.npz`（Panda 实测字段；RBY-1 另有 `state22`）：

| 键 | 形状 | 含义 |
|---|---|---|
| `time_s` | (N+1,) | 边界时间戳（秒） |
| `base_xyyaw` | (N+1, 3) | 实测世界 base `x/y/yaw`，m / rad |
| `qpos` / `qvel` / `ctrl` | (N+1, ·) | MuJoCo 状态与每步控制 |
| `integration_state` | (N+1, ·) | 离线回放所需的积分内部状态 |
| `control_json` | (N+1,) | 每步控制配置快照 |
| `clock_remainder` | (N+1,) | 控制时钟余量 |
| `camera_xpos` / `camera_xmat` / `camera_fovy` / `camera_intrinsic` / `camera_sensorsize` | (N+1, C, ·) | 相机位姿、视场角、内参与传感器尺寸 |
| `state` / `state_spec` | (N+1, D) / 标量 | 语义本体状态及其布局掩码 |
| `actions` / `requested_actions` | (N, ·) | 实际下发 / 请求的绝对目标动作 |
| `phases` | (N,) | 该控制步的阶段（Panda cuRobo 为 `pregrasp/approach/close/lift_actual`；MolmoBot 为 `navigation/arrival/vla_operation`） |

N+1 个边界状态对应 N 个实际控制间隔；没有真实物理步时不补行（如规划无解的 `rollouts/` 仅有初始 1 帧、`actions` 长度为 0）。

`recording.json` 字段：`complete`、`steps`、`frames`、`errors[]`、`replay_complete`、`observation_complete`、`missing_depth_cameras[]`、`controller_profile`、`layout`（`robot/state_key/state_fields/action_fields/state_layout/action_layout/action_convention/time_alignment`）、`mujoco_version`、`model_dir`、`state_spec`、`cameras`（别名→实际名称）、`resolution`、`control_hz`、`seed`、`task`（与场景库 `task` 同结构）、`preparation`（`path/actual_base/requested_station`）、`videos`（视角→`path/frames/fps/sample_times_s/source`，默认空）。

`planning.json` 为列表，逐条记录阶段：`native_batch_grasp` / `native_grasp_result`（`query_id/seed/batch_slot/batch_seeds/queries[]/planning_s/joint_names/reference_world/control_dt_s/time_dilation/query_path/worker/gpu/pid`）与 `lift_actual`（`goal_world/positions/source/diagnostics`）。

`initial_state.json`：`time_s`、`robot`（`base_xyyaw/lift/arm_q/gripper_width`）、`target`（`mjcf_body/pose_world_xyz_wxyz`）、`instances`（body→`pose_world_xyz_wxyz`）、`qpos/qvel/ctrl`。

`execution_diagnostics.json`：`wall_elapsed_s`、`trial_wall_s`、`simulation_elapsed_s`、`attempt_wall_budget_s`、`remaining_active_s`、`timings`（各阶段 `wall_s/calls`）、`active_budget`（`limit_s/planning_s/execution_s/queue_s/plan_count/created_at`）、`timing_scope`、`schema_version`、`execution_worker`、`recorder_finalize_wall_s`。

`task_evidence_initial.json`：`operation`、`initial`（`base/lift/target/height_m/fingers/workspace_center_xy/workspace_radius_m`）、`control`（`arm/height_target/grasp_row/grasp_source/seed`）。
`attached_object.json`：`physics_attachment`、`sphere_count`、`fit_frame`、`fit_type`、`fit_time_s`、`fit_reused`、`fit_debug`、`metrics`（`coverage/protrusion/surface_gap*/max_uncovered_gap/volume_ratio`）。
`events.jsonl`：每行 `code`、`phase`、`task_outcome`；`verdict-<n>.json` 是失败判定时保存的 `task_outcome` 快照（文件名含判定协议版本）。

`planning/<query_id>/` 文件说明：

| 文件 | 内容 |
|---|---|
| `query.json` | `query_id`、`identities[]`（`scene_id/station_index/trial/attempt_id/seed`）、`budgets[]`、场景与准备路径 |
| `batch_grasp_result.json` | 每个 batch lane 的 `success/index/stages/diagnostics`（`-batch`） |
| `solver_trace.json` | IK/TrajOpt 逐 seed 的 `success/position_error/rotation_error/feasible` |
| `planner_identity.json` | cuRobo `version`、`joint_names`、`tool_frames`、碰撞与 FOV 设置 |
| `planner.yml` | 完整 cuRobo 配置 |
| `mobile.urdf` / `mobile_base.json` | 机器人模型 / 参考位姿、局部关节限位与工作区 |
| `collision_world.json` / `target_world.json` | 查询范围内碰撞体 / 目标 geom 列表（`frame=measured base`） |
| `fk_check.json` | 初始 FK 误差 `translation_error_m`、`rotation_error_rad` |
| `target_query_range.json` | 最大球半径、最小查询范围与调整原因 |
| `carried_geometry.json` | 单次查询携带的几何信息（`-q<n>`） |
| `planner_reuse.json` | 资源复用来源、scope、是否复用 solver、种子 |

`preparation.json` 字段：`status`（`ready` 等）、`requested_station`、`actual_base`、`measured`（实测 `time_s/robot/target/instances/qpos/qvel/ctrl`）、`state`（`state.npz`）、`state_spec`、`model_dir`、`elapsed_s`、`settled_simulation_s`；失败时另有站位准备错误信息。

`figures/<scene_id>/`：`heatmap.json` 为说明（`sigma_m`、核函数、遮挡掩码、加权规则、`unknown_is_not_zero`）；`success_heatmap.npz` 含 `origin`、`resolution_m`、`free`、`success_rate`、`weighted_trials`、`sigma_m`、`max_support_distance_m`；SVG 为可视化。热图只用于阅读，困难起点选择读取原始 trial。

`config.json` 顶层：`dataset`（场景库 run 路径）、`config`（物理/规划/操作参数）、`collection` 与 `calibration_collection`（录制开关、分辨率、步数上限、穿透阈值）、`calibration`、`gpu`、`profiles`（每场景的机器人配置与初始状态）、`resources`（`station_workers/gpu_ids/egl_devices/reuse_planners/cpu_workers/preparation_workers/recording_mode/split_planning`）、`seed`。

`summary.json`：`status`、`elapsed_s`、`calibration`（`planned_trials/recorded_trials/preparation_failures[]/failure/peaks/gpu_active_s/elapsed_s`）、`scenes[]`（`scene_id/calibration_complete/eligible_pairs/successes/shared_failure`）。`eligible_pairs` 为合格 S0 站位数 × S1 站位数，即计划配对数量，不是成功数。

## 场景库与标定关系

场景构建完成发现任务、资格筛选、增强与站位采样，accepted/rejected 都保留在 `scenes.jsonl`。
每个 accepted 场景给出任务和最终站位，采集端按这些站位执行，不重新生成一套点集。
`evaluate-stations()` 写 `trials.jsonl`；`collect-lastmile()` 读取该标定，或在当前 run 标定一次，再写全量配对与两类成功示教视图。
平滑热图仅用于阅读，选点读取原始 trial 计算出的实测率。场景、任务与控制 profile 必须匹配。

## lastmile 训练来源与物化

选点：`rate <= low` 为 S0，`rate >= high` 为 S1；配对为同场景任务的完整笛卡尔积，不受附近距离和数量上限截断。`pair_index.jsonl` 先保存全部计划，再更新结果；不再写重复的连续采集 `episodes.jsonl`。benchmark 的同名文件不变。

训练索引的公共字段包括场景、任务、站位、seed、真实任务结论和记录完整性：

- `s1_direct_operation`：所有真实成功且记录完整、有实际控制步的 calibration attempt；不受高率阈值限制，没有 `s0` 或 `navigation`。
- `s0_to_s1_continuous`：导航成功、操作成功且记录完整的真实连续 attempt；另含 `pair_id`、`s0/s1`、导航结论，以及 `euclidean_distance_m`、`astar_path_length_m`、`navigation_steps`。

距离单位米；A* 长度按发送给跟踪器的完整折线计算；导航步数为实际 `navigation` 控制间隔数，不含 `arrival`、操作或底层物理子步。失败保留已执行步数，未知不填 0。

引用型条目以 `source_run`（相对本数据集 run）与 `rollout_id` 定位源 attempt。
独立物化将成功轨迹统一复制到新输出的 `rollouts/dXXXXXX/`，训练条目改为 `rollout_path`（相对新数据集根目录），并以 `source.run/rollout_id` 保留历史来源。同一来源只复制一次，不同 run 的相同局部编号不合并。

内部 rollout 含原始 `trajectory.npz`、`recording.json`、必要相机 RGB/已启用深度，以及 `metadata.json`（相机列表、录制模式、动作定义及历史有效配置）。历史来源中的外部路径仅供追溯，不参与训练读取。物化不包含仿真重放资产或模型权重；非必需诊断视频不复制。

`pair_index.jsonl` 在物化后仍保留全集；成功 pair 指向内部副本，失败 attempt 只保留来源信息，不承诺有轨迹副本。`summary.json` 的 `completed/incomplete` 描述文件处理是否完成，不是机器人任务成功率。

原始训练读取器为 `data.export.lastmile_dataset.load_training_batch()`：返回 `[B,T,22]` state、`[B,T,20]` action、phases/time 和按相机分组的 RGB/深度，不做动作语义转换或补帧。具体命令见运行手册第 3 节。

## 场景载入

`scene-files/<scene>/` 包含 `model.mjb`、`initial.npz`、机器人及载入配置；有构建 spec 时另存 `model.xml` 便于查看。
MJB 是实际载入来源，需使用生成时的 MuJoCo 版本。XML 不是跨版本自动回退契约。
实例映射只用于定位物体，不是审计 registry。原始外部资产只读。

## 实际执行轨迹

`rollouts/<id>/trajectory.npz`：`time_s/qpos/qvel/ctrl/base_xyyaw` 和本体状态数组为 N+1 个边界状态（RBY-1 为 `state22`，PandaOmron 为 `state`），`actions/requested_actions/phases` 为 N 个实际控制间隔。`base_xyyaw` 是实测世界坐标 x/y/yaw，单位 m/rad；旧轨迹未记录该字段时恢复诊断为 unknown，不从数组下标猜测。没有真实物理步就不伪造轨迹行。
`phases` 在下发动作前设置，标明该实际间隔的执行阶段；MolmoBot 使用 `navigation/arrival/vla_operation`，不再用记录器的隐式 `operation` 默认值。
`recording.json` 记录是否完整、步数和错误；事件用于诊断。RGB 图片默认保留，视频、深度、逐物理步日志按需选择。
RGB PNG 使用无损压缩级别 1，减少在线编码耗时，文件体积相应增大。
没有 canonical/raw 双份记录，也不保存独立 replay、final-state、outcome 或物理日志派生索引。

关闭逐物理步日志时仍保留 4 ms 任务事实与原接触统计，只省去完整遥测行构造和序列化；开启日志时保留原字段。复用的闭爪前 planner 目录用 `planner_reuse.json` 引用同 run 内首次构造的资源目录，路径相对于 run 根目录；当前 trial 的 seed、规划、FOV 与 solver 日志仍独立记录。

RBY-1 的 20D 动作：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`。
底盘/手臂是增量，夹爪/躯干是绝对目标；机器人配置决定控制模式和合法范围。

PandaOmron 使用语义 `base_xyyaw / lift / arm_q / gripper_width`，在记录边界按明确字段顺序展平为绝对目标；位置与开口单位 m，角度 rad，base 使用世界参考系。`recording.json` 的 `layout` 保存 state/action 字段、布局与 `state[t], action[t], state[t+1]` 时间约定；物化后的 `metadata.json` 同样保留。Panda 不填充左臂或头部，也不写 `state22`。不同本体布局分别读取 batch，运行步骤见 [Panda 指南](../reports/panda-omron/docs/panda-omron.md)。

## 结果含义

Benchmark v0.1：每个合格 S0 一条 episode，不按 S0×S1 复制。标定开放底盘仍有效；≤20% / ≥60% 表示从初始站位启动的参考协议任务成功率。区域由高率初始站位构造，不从成功轨迹终点补造高率站位。

`oracle.json` 的 `regions` 按 scene 保存网格、朝向、锚点、原始计数和局部 patch；`episodes` 按 episode ID 保存 case/task/failure_family、S0 计数及 difficulty。difficulty 包含 `path_length_m`、`euclidean_distance_m`、`detour_factor`、`recovery_ratio` 及分子/分母；欧氏距离为零时 detour 为 null。区域近似不是连续空间的实测成功概率图。

评测 `results.jsonl` 同时保存任务结论和 `recovery_success/recovery_first_time_s/base_path_length_m/final_displacement_m/max_displacement_m/base_yaw_travel_rad/final_yaw_delta_rad/max_yaw_delta_rad/effective_adjustment/diagnostic_status`。恢复结果独立于 TSR，轨迹缺失不当作恢复失败。

`summary.json` 给出总体/分组 SR、覆盖率、`macro_by_case`（主 Macro）、`macro_by_task`、`macro_by_failure_family` 及恢复诊断覆盖率。分组有未知任务结果时正式 Macro 为 null；另报有效子集 SR，不移除未完成组。

真实失败、规划无解、超时、初始化/记录/基础设施错误分开记录。逐点成败率只计算已完成的可评测试验；试验数未满不用于困难起点选择。导航成功不等于操作成功；记录完整也不等于任务成功。

MolmoBot 转换按需生成 `data.npz`，引用原图像。非原生控制模式的夹爪动作需要显式标定；不能直接声称此简化导出是完整的上游训练格式。

## 路径与环境

完整输入准备与路径规则以 [setup](setup.md) 为准。

用户配置由各配置加载器明确解析：data_root 显式设置，数据路径相对 data_root，配置与解释器路径相对 repo root 或写绝对路径。保存的 scene/run 配置不做通用字段扫描或目录重定位；普通主记录用 `io.rows()` 读取。
移动输入/输出树时保持相对关系，并重新安装环境；MJB 只承诺同版本恢复，不把导出的 XML 当作已验证的独立资产包。


### 场景库预览

`build-scenes` 的 accepted 场景新增 `before_scene`（相对数据集根目录的同步静置无新增物体对照快照）
和 `before_stations`（该对照的最终站位，沿用 `stations` 的字段）。仅编辑场景保存这两个字段；
旧记录可缺失，不重写历史记录。对照使用与编辑后相同的候选网格、初始化/可见性检查和最终选点规则。

`index.html` 引用 `previews/<scene_id>.png`，每个场景一张组合图，不生成散落子图。
这些是只读派生预览，不是机器人相机输入，也不是操作成败证据。预览错误只在日志和页面展示，
不改写主记录。补生成可覆盖派生图片和页面，不覆盖场景快照或采样记录。

## Benchmark 可视化与视频

`<benchmark>/visualization/` 输出每条 episode 的 `<episode_id>.png` 与 `summary.png`，仅含静态构建信息和私有 oracle。`<evaluation>/visualization/` 独立输出实际轨迹、结果和两个视角最终帧的组合 PNG。评测读 benchmark 原始数据，不回写 benchmark，也不依赖其图片是否已生成。不生成 HTML 或 JavaScript。

评测 rollout 默认输出 `videos/head.mp4`、`videos/third_person.mp4`；录制器在初始状态及每个实际控制边界各捕获一次。`recording.json.videos` 按视角保存 `path`、`frames`、`fps`、`sample_times_s`、`source`。`source` 为 `live_render`、`recorded_images` 或 `recorded_state_reconstruction`。相机逐帧 PNG 不再默认输出于 benchmark 评测；其他采集流程保持各自配置。操作步骤见 [benchmark 指南](../reports/benchmark/docs/运行指南.md)。

## 实测状态离线回放

`trajectory.npz` 同时保存初始帧及每个实际控制步后的 `integration_state`、`state_spec`、`control_json`、`clock_remainder`、相机位姿和内参。`actions`、`requested_actions`、`phases` 仍各对应一次实际控制步，不为无执行的 trial 补行。

`recording.json` 保存 MuJoCo 版本、`model_dir`、相机别名/名称、尺寸、控制频率和 `replay_complete`。公共准备资料位于 `preparations/<scene>/<station>/`，trial 引用同一准备状态，但独立执行。数值记录完整与视觉示教模态完整分别判定。

离线输出的 `replay.json` 标明 `source=measured_state_replay`、实测时间戳、视频播放时间及原始帧索引。回放只恢复实测状态并调用 `mj_forward`，不积分、不重新判定成败。
