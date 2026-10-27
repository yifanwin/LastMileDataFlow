# 无编辑全体 val（val_0–val_999）全目标 cuRobo 标定需求

- 状态：active（需求定稿，待实现）
- 日期：2026-10-26
- 替代：同文件初版
- 范围变更：2026-10-26 由 `val_0`–`val_99`（100 个）扩展为 `val_0`–`val_999`（1000 个）
- 关联：[任务模式需求](../../task-modes/requirement/01-task-mode-construction.md)、[实验方案](../plan/01-experiment-plan.md)、[计划表](../plan/02-schedule.md)、[任务模式构建说明](../../task-modes/info/01-build-scenes-modes.md)、[val100 全量实验](../../curobo-time-optimization/delivery/2026-10-24-val100-experiment.md)、[运行手册](../../../docs/运行手册.md)

> 工作目录名 `no-edit-calibration-val0-99` 和分支名保留创建时名称，实际范围以本文为准。

## 1. 一句话目标

在 `procthor-10k-val` 的 `val_0`–`val_999` 共 1000 个原始场景上，**不做任何场景编辑**，为 PandaOmron 的 Pick / Open 目标及其周围候选站位执行 cuRobo V2 标定；按 task-mode 资格口径筛选任务，统计“无用场景”“全成功场景”“有 S0 和 S1 的有效场景”，并把规划与执行失败原因细化到 IK 不可达 / IK 约束不可行 / TrajOpt、阶段、机械臂、抓取候选和具体执行原因。

## 2. 固定范围

| 项目 | 取值 |
|---|---|
| 数据根 | `/data1/wenyifan/lmdf/molmospaces_data` |
| 来源场景 | `assets/scenes/procthor-10k-val/val_0` … `val_999`，共 1000 个 |
| 本体 | PandaOmron `robocasa-v1`，三相机 |
| 任务类型 | `pick`、`open` |
| 场景编辑 | 无：不加障碍物、不移动目标、不改初始姿态；原始 XML/资产只读 |
| 操作后端 | cuRobo V2 0.8.0，`holonomic_joint_planning` |
| 每站 trial | 3 次（task-mode 资格口径的固定要求） |
| 站位上限 | 每任务最多 20 个合法站位（保留现有硬上限） |
| 标定记录 | `numerical`，标定期间不录视频 |
| 运行方式 | `evaluate-stations --recipe` 直接标定；不单独执行 `build-scenes`，不产出独立 build run |
| 并行 | 8 卡并行；CPU trial 容量 60 起步，可按实际 CPU/内存余量提高 |
| 准备优化 | 无编辑路径不测三相机可见性；同房屋复用一份冻结基线；准备与标定流水线重叠；热图每批标定后离线导出 |
| 视频 | 全部标定处理完成后，统一离线回放渲染一次，不新增物理步 |
| 数据划分 | 1000 个来源均记为 `val` |

## 3. 目标与站位的定义

### 3.1 “符合条件的目标”

沿用现有任务发现与预筛，不另造规则：

- Pick：自由关节、最小边 ≤ `max_pick_object_size_m`，有合法数据抓取，有合法支撑面。
- Open：白名单类别、hinge/slide 关节、有可用把手、未完全打开。
- 两种任务都要求采样圆内存在来源房间地面三角形。

每个房屋选**全部**合格目标：`max_pick_targets_per_house=-1`、`max_open_targets_per_house=-1`。

### 3.2 “目标周围”

- 以目标锚点为圆心，半径 1.0 m、间距 0.25 m 的 lattice，共 49 个候选 XY。
- 在独立 clone 中做真实机器人初始化，只保留几何合法站位。
- 最终每个任务最多保留 20 个合法 `(x, y, yaw)`，与现有 task-mode 标定口径一致；不放开到 49 点全量。

### 3.3 标定

- 每个站位执行 3 次独立 trial。
- 每个无编辑任务的准备有 300 s 墙钟上限；超时写 `task_timeout` rejected，继续下一任务，不阻塞整批，也不生成部分站位或 trial。
- 每个房屋的准备有 20 min 墙钟上限；超时终止该房屋进程并写 `house_timeout` rejected，其他房屋继续。
- 使用 task-mode 的 trial 有效性口径：基础设施错误、初始化/记录错误、未知 timeout 不算作任务失败，也不进入成功率分母；`planning_no_solution` 是已完成的有限预算结果，保留在规划失败统计中。
- 不把 0 物理步的规划无解写成任务失败；`task_outcome=unknown` 保持 unknown。

## 4. 处理流程

1. `evaluate-stations --recipe` 逐 house 加载原始场景，发现并预筛 Pick / Open 任务。
2. 为每个任务准备共享上下文，生成 1 m / 0.25 m 候选站位，真实初始化筛选合法站位，最多保留 20 个。
3. 冻结一次性无编辑场景快照（`model.mjb + initial.npz`），写入内部输入缓存 `outputs/_calibration_inputs/<run-id>/`；它不是 build run，也不是交付物。
4. 在同一命令内对快照逐站执行 3 次 cuRobo 标定，产出 `trials.jsonl`、`summary.json`。
5. 每批标定完成后，基于原始 trial、`planning_ref` 指向的规划证据和 rollout 元数据，生成逐失败明细、目标类别/task/station 成功率和汇总统计；更新后续批次的 `exclude_categories`。
6. 同一批的离线热图导出与下一批的发现/准备/标定并行；全部标定完成后，统一运行离线回放渲染，生成视频清单。

不单独运行 `build-scenes`，不生成 `outputs/scene_datasets` 交付 run，不生成场景预览或构建报告。内部输入缓存保留到视频回放完成，之后可按需清理。

## 5. 分类与统计口径

### 5.1 task-mode 资格

直接复用 `data/evaluation/selection.py::mode_calibration` 的定义：

- 每站必须恰好 3 次有效 trial；任一站位不足 3 次则整个任务 `incomplete`。
- 任务资格：
  - `incomplete`：标定不完整；
  - `no_success`：完整但全部 trial 失败；
  - `no_high_rate_s1`：有成功，但没有任何站位达到 2/3 及以上；
  - `eligible`：至少一个站位达到 2/3 及以上。
- S0 选择：优先 0/3；没有 0/3 时取 1/3。
- S1 选择：优先 3/3；没有 3/3 时取 2/3。
- “有 S0 和 S1 的有效场景” = `eligible` 且 S0 集合与 S1 集合都非空。本文后续“有效场景”均指这一条。

不使用 task-mode 的八类诊断标签，也不把无编辑场景标成某个 `proposal_intent`；资格只在分析层按上述规则计算。

### 5.2 任务级分类

任务 = 一个目标物体（Pick）或一个可操作关节（Open）。

| 类别 | 定义 | 处理 |
|---|---|---|
| 全失败任务 | 所有站位的全部有效 trial 均失败（成功率为 0） | 丢弃；统计数量与占比 |
| 全成功任务 | 标定完整，且全部有效 trial 均成功（成功率为 100%） | 保留；统计数量与占比 |
| 有效场景 | `eligible`，且同时存在 S0 和 S1 | 保留；进入失败原因统计 |
| 有成功但无 S0/S1 配对 | 有成功但 S0 或 S1 缺失，或未达到 `eligible` | 保留并单列原因 |
| 不完整/基础设施失败 | 标定不完整、共享故障、初始化/记录错误 | 单列，不归入以上类别 |

分类优先级：全失败 > 全成功 > 有效场景 > 有成功但无 S0/S1 配对 > 不完整/基础设施失败。全成功任务通常只有 S1、没有 S0，保留并统计，但不进入有效场景的失败主表。

“无用场景”同时按两种口径统计：

- 按 task：全失败任务数 / 有有效 trial 的任务数；全失败任务数 / 发现任务数。
- 按 house：没有任何有效场景的 house 数 / 1000；没有任何合格目标的 house 数单独列出，不与“有目标但全失败”混在一起。

### 5.2.1 目标类别成功率与后续批次排除

目标类别按 `task.category` 统计：Pick 为物体类别，Open 为家具类别。

- 每个 chunk 完成后，累计统计 `category × operation` 的 tasks、houses、stations、有效 trials、successes、success_rate。
- 若某类别累计有效 trial ≥ 30、覆盖 ≥ 2 个 house、成功数为 0，则加入 `exclude_categories`；后续 chunk 的配方在 `_select_tasks` 中跳过该类别，记录 `excluded_target_category`。
- 排除只影响后续批次，不回写已有 run；被排除类别已经跑出的结果保留并计入最终统计。
- 由于流水线会提前准备下一个 chunk，排除通常滞后一个 chunk 生效；从再下一个 chunk 起完全不准备该类别。
- 最终必须输出类别、task、station（视角）三层成功率；station 级明细保存在 `target_success_rates.json`，Markdown 给出类别与 task 摘要。

### 5.3 失败原因归纳表（trial 级）

参照 `2026-10-25-planning-failure-analysis.md` 的格式：每条有效 trial 只归入一个最终行，行合计等于有效 trial 数；success 单列。

#### 5.3.1 归因顺序

按实际执行顺序取**首个失败点**，后续阶段不重复计数：

```text
goalset → pregrasp/approach → grasp → lift → 执行 → success
```

`pregrasp` 对应参考表中的 approach 阶段。

#### 5.3.2 IK 收敛、可行与 TrajOpt 判定

- 收敛：`position_error < 0.005 m` 且 `rotation_error < 0.05 rad`。
- 可行：收敛且 `feasible=True`（关节限位、自碰撞、环境碰撞、目标碰撞、工作圆全部满足）。
- IK 不可达：该 lane 没有任何 goal 收敛。
- IK 约束不可行：至少一个 goal 收敛，但没有任何 goal 可行。
- TrajOpt 不可行：IK 有可行 goal，但该 lane 的 TrajOpt 在 `max_attempts=5` 内无成功轨迹。

证据来自 `solver_trace.json` 的 `success`、`position_error`、`rotation_error`、`feasible`、`constraint_terms`，阶段与 lane 归属来自 `phase_evidence`、`batch_slot`、`batch_seeds`。

#### 5.3.3 表格行

| 阶段 | 具体原因 | 判定 |
|---|---|---|
| IK (goalset) | 目标位姿不可达（位置 ≥5 mm 或姿态 ≥0.05 rad） | 无收敛 goal |
| IK (goalset) | 位姿收敛但约束不可行（碰撞/关节限位/工作圆） | 有收敛、无可行 |
| TrajOpt (goalset) | IK 有解、轨迹不可行 | 有可行、TrajOpt 失败 |
| IK (approach) | approach 阶段 IK 不可达 | 无收敛 goal |
| IK (approach) | approach 阶段位姿收敛但约束不可行 | 有收敛、无可行 |
| TrajOpt (approach) | approach 阶段 IK 有解、轨迹不可行 | 有可行、TrajOpt 失败 |
| IK (grasp) | grasp 段（8 cm 线性接近）IK 不可达 | 无收敛 goal |
| IK (grasp) | grasp 段位姿收敛但约束不可行 | 有收敛、无可行 |
| TrajOpt (grasp) | grasp 段 IK 有解、轨迹不可行 | 有可行、TrajOpt 失败 |
| 执行 | 抓住物体后 lift 规划无解 | lift 阶段规划失败 |
| 执行 | `lift_contact_hold_or_slip` | 按 attribution 再分 MissedGrasp / GraspSlip / DroppedObject / InsufficientLiftOrHold |
| 执行 | `open_no_grip` / `open_contact_lost` / `open_missing_hold` / `open_joint_or_contact_hold` | 按原始 reason 逐项 |
| 执行 | `base_outside_workspace` | 原始 reason |
| 执行 | `mobile_base_arm_tracking_timeout` | 原始 reason |
| 执行 | `tcp_tracking_timeout` | 原始 reason |
| 执行 | `arm_tracking_timeout` | 原始 reason |
| 执行 | `navigation_tracking_timeout` | 原始 reason |
| 执行 | `control_limit:*` | 按异常文本细分 |
| 执行 | 其他具体 `reason` | 逐项展开，不合并为“其他” |
| 基础设施/未知 | 初始化、记录、共享故障、未知 timeout | 单独表，不计入任务失败 |
| — | success | — |

表格列：

- 主表：`pick` / `open` / `合计` / `占全部有效 trial`。
- 分组表：`有效场景` / `全失败任务` / `有成功但无 S0/S1 配对`，每组给行计数与组内占比。
- 交叉表：`物体类别（Pick）/ 家具类别（Open）× 失败原因`，格式同参考分析。
- 执行细分表：`phase × 原始 reason × attribution.category`。
- 约束细分表：`constraint term × positive trial 比例 × top links`。

主表分母是该组内 `mode_trial_evaluable` 为真的 trial 数；success 计入分母，基础设施/未知 timeout 不计入主表百分比，单独表统计。`head_fov_enabled=false` 时不会出现 FOV 失败；若后续开启，新增 `FOV` 行。

#### 5.3.4 补充证据指标

- TrajOpt 失败中目标位姿已到达（<5 mm）的比例；
- TrajOpt 失败站最佳位置误差 p50 / p90 / max；
- 约束不可行 trial 中 `target_collision`、`scene_collision`、`cspace`、`self_collision`、`workspace_circle` 的 positive 比例；
- 约束不可行 trial 的 top collision links（gripper / link5 / link6 / link7 / base 等）；
- Open：把手高度 z 与最小位姿误差的分档统计；
- Pick：物体类别 × 原因交叉表。

#### 5.3.5 逐失败明细

输出 `planning_failures.jsonl`，每条失败 trial 一行：

```text
scene_id, task_id, operation, station_id, station_index, trial, attempt_id,
arm, seed, batch_slot, plan_stage, phase, query_index, solve_index,
failure_component, ik_unreachable, ik_constraint_infeasible, trajopt_failed,
grasp_index, goal_count, min_position_error_m, min_rotation_error_rad,
positive_constraints, failure_reason, planning_ref
```

`planning.json` 有两种形态：已执行 trial 是规划条目列表；0 物理步的未执行 trial 是含 `diagnostics` 的单条记录。分析代码需要同时兼容。trial 与 lane 的对应：`batch_seeds[slot] == trial.seed`；单 lane 记录取 slot 0。

#### 5.3.6 证据边界

- `selected_link_costs` 只覆盖 top-12 解，不是全部 seed；不能逐 seed 归因。
- TrajOpt 失败无法继续拆分环境碰撞/自碰撞/关节限位/速度约束，只保留 IK 约束项。
- 生产规划只返回被选中的求解结果，`ik_denominator=unavailable_selected_results`；能回答阶段/分量/机械臂/抓取候选，不能声称完整 seed 分母。需要 seed 级证据时，单独运行 `diagnostic_ik` probe，不计入 trial。

### 5.4 执行失败明细

执行失败在主表中按原始 `reason` 成行；另外输出逐 trial 明细 `execution_failures.jsonl`。

对每条执行失败，除 trial 已有字段外，需要补记 `attribution` 与 `retry_history`（当前 `_trial_record` 未保存，见第 8 节）。抓取候选、机械臂和 seed 从 rollout 的 `task_evidence_initial.json` 的 `control` 字段读取（`arm`、`grasp_row`、`grasp_source`、`seed`）。

逐失败输出一行：

```text
scene_id, task_id, operation, station_id, station_index, trial, attempt_id,
phase, status, reason,
attribution_category, attribution_reason,
task_outcome_reason, first_failure_phase, first_failure_time_s,
arm, grasp_row, grasp_source, seed,
executed_steps, retry_count, retry_reasons,
navigation_status, navigation_reason,
recording_complete, collisions
```

执行失败按以下层次归类，同时保留原始 `reason` 字符串，不只给粗分类：

| 类别 | 典型 `reason` | 证据 |
|---|---|---|
| 抓取/接触失败 | `lift_contact_hold_or_slip`、`open_no_grip`、`open_contact_lost`、`open_missing_hold`、`open_joint_or_contact_hold` | `task_outcome`、`attribution.category`（MissedGrasp / GraspSlip / DroppedObject / LostHandleContact / InsufficientLiftOrHold / ArticulationFailure） |
| 跟踪/控制失败 | `robocasa_reference_tracking_timeout`、`tcp_tracking_timeout`、`arm_tracking_timeout`、`mobile_base_arm_tracking_timeout`、`navigation_tracking_timeout`、`control_limit:*`、`base_outside_workspace`、`torso_feedback_limit` | `execution_termination.phase`、`attribution.category=TrackingFailure` |
| 执行中途规划失败 | `planning_no_solution`、`planning_budget_no_solution`、`fov_constraint_failed` | `execution_termination.phase`、`planning.json` |
| 碰撞失败 | 含 `collision`、`nonfinger`、`penetration` 的 reason | `attribution.category=Collision`、`collisions` |
| 仿真失败 | `nonfinite_state`、`mujoco_warning` 等 `SIMULATION_FAILURES` | `attribution.category=SimulationFailure` |
| 基础设施/记录 | `initialization_error`、`recording_error`、`infrastructure_error`、`missing_physics_evidence`、`physics_trace_gap`、`invalid_physics_evidence`、`malformed_physics_evidence` | 单独统计，不进入任务失败 |
| 未知 timeout | `status=timeout` 且 `task_outcome.status=unknown` | 单独统计 |

汇总维度：

- 按 `operation × phase × reason`；
- 按 `attribution.category`；
- 按失败阶段（`settle` / `navigation` / `pregrasp` / `approach` / `close` / `lift_actual` / `hold` / `open` / `open_hold` / `retry_release`）；
- 按 task / station / house；
- 重试次数与重试原因；
- 成功 trial 的抓取候选、机械臂和 seed 分布，作为失败对照。

## 6. 视频统一渲染

- 标定阶段统一使用 `--recording-mode numerical`，不生成视频。
- 全部标定完成后，只对已有 rollout 运行 `evaluate-stations --replay-from`，不重新执行物理仿真，`new_physical_steps=0`。
- 默认布局为三相机 + 俯视底盘轨迹的 2×2 grid；需要单相机时再显式指定。
- 每段视频保留来源 run、scene、task、station、trial、rollout、结果和失败原因，生成一份视频清单。
- 默认渲染范围（已确认）：
  - 5.3 主表中每个失败行各 1 条代表，按 `scene_id`、`station_id`、`trial` 确定性取第一条；没有 rollout 的行（如未执行）跳过；
  - 每个 operation 1 条成功代表；
  - 每个有效场景最多 1 组 S0/S1 对照，按 `station_id` 确定性选择；
  - 生成 `manifest.jsonl`，记录视频与 scene/task/station/trial/rollout/reason 的对应关系。
- 不把“有视频”当作任务成功或失败的新证据；视频只用于复核原始 rollout。

## 7. 交付物

- 标定 run：`outputs/<calibration-run-id>/`，含 `trials.jsonl`、`summary.json`、`rollouts/` 和必要热图。
- 内部输入缓存：`outputs/_calibration_inputs/<calibration-run-id>/`，含 `scenes.jsonl` 和冻结快照；不是交付物，不生成 `outputs/scene_datasets`，回放完成后可清理。
- 失败明细：
  - `planning_failures.jsonl`：每条规划失败一行，字段见 5.3；
  - `execution_failures.jsonl`：每条执行失败一行，字段见 5.4；
  - `failure_summary.json`：按 5.3/5.4 维度的计数与占比；
  - `failure_table.md`：按 5.3.3 的主表、分组表、交叉表、约束细分表生成；
  - `target_success_rates.json`：类别、task、station 三层成功率与排除建议；
  - `replay_manifest.jsonl`：代表视频的 scene/task/station/trial/rollout/reason 映射。
- 分析报告：`reports/no-edit-calibration/delivery/` 下的 Markdown，至少覆盖：
  - house / task / station / trial 四层计数；
  - 全失败、全成功、有效、未完成任务数与占比；
  - 无用场景的 task 口径与 house 口径；
  - S0 / S1 数量、成功率分布和配对数量；
  - 目标类别、task、station 三层成功率与后续批次排除列表；
  - 5.3.3 的 trial 级失败原因主表（pick / open / 合计 / 占比）；
  - 规划失败按 goalset / approach / grasp × IK 不可达 / IK 约束不可行 / TrajOpt 的分布；
  - 执行失败按 phase × 原始 reason × attribution.category 的分布；
  - IK 约束项与 top collision links 的分布；
  - 基础设施/未知 timeout 单独列示；
  - 明确“unknown 不是失败”“未执行不是任务失败”。
- 视频：`outputs/replays/<replay-run-id>/` 与视频清单。
- 每次运行使用新 run-id，不覆盖已有结果，不补造 rollout。

## 8. 当前实现缺口与最小改动

1. **没有直接标定入口**：当前 `evaluate-stations` 只接受已有数据集，不能从配方直接发现任务和采样站位。需要增加 `evaluate-stations --recipe`，内部复用任务发现、`BuildContext` 和站位选择，生成一次性无编辑快照后直接标定；不新增第 6 个 CLI 命令，不产出独立 build run。
2. **站位上限保留 20**：不修改 `max_final_stations` 的硬上限。
3. **资格口径在分析层实现**：不启用 task-mode 诊断；在分析代码中复用 `mode_calibration` 的 3 次有效性、`no_success` / `no_high_rate_s1` / `eligible` 和 S0/S1 选择规则，避免给无编辑场景伪造 `proposal_intent`。
4. **trial 记录补充执行归因**：`_trial_record` 目前只保存 `status`、`reason`、`task_outcome`、`execution_termination`、`navigation` 等，丢失 `attribution`、`retry_history`、`exception_detail`。为满足执行失败明细，最小改动是把 `attribution` 和 `retry_history` 加入 trial 记录；`first_failure_phase` 已在 `task_outcome` 内，`execution_termination.phase` 已保存。
5. **旧配方不可直接用**：`configs/scene_datasets/panda_omron_val100.json` 是 `scene-dataset-recipe-v1`，当前 `main` 只接受 v2，需要新写 v2 配方。
6. **失败分析尚无现成脚本**：需要新增一个只读分析模块，读取 `trials.jsonl`、`planning_ref`、`task_evidence_initial.json` 和 rollout 元数据，输出 5.3/5.4 的明细与汇总。
7. **规划细分不需要改规划器**：当前 `solver_trace.json` 已保存 `position_error`、`rotation_error`、`feasible`、`constraint_terms`，可以直接复现参考分析的 IK 不可达 / 约束不可行 / TrajOpt 三类；只需分析代码正确读取并按 lane 归因。
8. **CPU worker 上限写死 60**：`RunResources` 校验 `1 ≤ cpu_workers ≤ 60`，CLI help 也写 1–60。需要放开为按机器 CPU 数或显式上限校验，否则无法按实际余量多开。`preparation_workers` 默认 12，是否放宽由试点决定。

## 9. 规模、耗时与分批

基准：2026-10-24 的 val0–99（100 个来源，每房 1 Pick + 1 Open，每任务 ≤20 站）实测 220 个合格构建单元、183 个 accepted、2,822 个站位、8,466 个 trial；构建约 54 min，数值标定约 2.88 h（双卡、60 CPU worker），标定 run 约 4.8 GB。

扩展到 1000 个来源、每房全部合格目标后：

| 项目 | 粗略预计 |
|---|---|
| 构建/准备 | 9–30 h（25 房并行 chunk） |
| 标定 | 100–250 h（约 4–10 天连续运行；8 卡压缩规划段） |
| trial 数 | 约 50–150 万（单房可能 50–90 个合格任务，以 P0 实测为准） |
| 存储 | 约 0.5–1.2 TB（内部快照 + 标定 run），准备 ≥1.5 TB 空闲 |
| 视频 | 代表集 + 每有效场景最多 1 组 S0/S1，渲染与存储另计 |

以上是按旧 run 吞吐的线性外推，不是承诺。实际取决于合格目标数、站位数、规划无解比例、GPU 数量和规划失败占比。本次使用 8 卡并行（`--gpu 0 1 2 3 4 5 6 7`），CPU trial 容量从 `--cpu-workers 60` 起步，按试点实测的 CPU、内存和 load 余量提高；`--preparation-workers 12` 默认。8 卡主要压缩规划段，物理执行、CPU trial 和准备阶段仍是瓶颈；最终耗时以 `val_0`–`val_4` 试点实测为准。

### 9.1 分批执行

1000 个来源一次性构建/标定的单次失败代价过高，建议按 100 个来源一批，共 10 批：

- 每批使用独立 calibration run-id 和同名内部输入缓存，不覆盖前一批；
- 每批完成后立即运行失败分析，确认失败证据字段完整、无共享故障；
- 全部批次完成后，用只读分析模块汇总 10 个 calibration run；
- 批次只按 house 范围切分，不按结果挑选来源，避免选择性偏差；
- 先跑 `val_0`–`val_4` 试点，再跑 `val_0`–`val_99` 做 10% 规模验证，最后启动剩余 9 批。

## 10. 已确定口径（需求定稿）

| # | 项目 | 确定值 |
|---|---|---|
| 1 | 每个房屋的目标 | 全部合格 Pick / Open 目标 |
| 2 | 每任务站位上限 | 20 |
| 3 | 每站 trial | 3 |
| 4 | 任务资格 | task-mode：`incomplete` / `no_success` / `no_high_rate_s1` / `eligible` |
| 5 | S0 选择 | 0/3 优先，其次 1/3 |
| 6 | S1 选择 | 3/3 优先，其次 2/3 |
| 7 | 有效场景 | `eligible` 且 S0、S1 都非空 |
| 8 | 全失败任务 | 丢弃并统计；失败明细单独成组保留 |
| 9 | 全成功任务 | 保留并统计；不进入有效场景失败主表 |
| 10 | 无用场景统计 | task 口径与 house 口径都报 |
| 11 | 失败原因格式 | 按 5.3 的 trial 级主表：IK 不可达 / IK 约束不可行 / TrajOpt / 执行具体 reason |
| 12 | seed 级 IK probe | 默认不跑；需要时单独跑，不混入 trial |
| 13 | 视频 | 全部标定完成后统一回放；按 6 节代表集 |
| 14 | 数据划分 | 1000 个来源全部记为 `val` |
| 15 | 试点 | 先跑 `val_0`–`val_4`，再跑 `val_0`–`val_99` 做 10% 验证 |
| 16 | 全量组织 | 1000 个来源按 100 个一批，共 10 批；每批独立 run-id |
| 17 | 运行方式 | `evaluate-stations --recipe` 直接标定；不单独 build run，不产出 `outputs/scene_datasets` |
| 18 | 并行 | 8 卡并行；每卡规划 worker 默认 1，显存/利用率有余量时提高到 2；`--cpu-workers` 60 起步可提高 |
| 19 | 目标类别排除 | 累计 ≥30 有效 trial、≥2 个 house、0 成功；后续 chunk 跳过；流水线下滞后一个 chunk |
| 20 | 流水线 | 前一批在 GPU 标定时，后一批同步发现/准备 |
| 21 | 准备优化 | 不测三相机可见性、同房屋复用冻结基线；热图每批标定后离线导出，与下一批并行 |

需求无其他待确认项。下一步按此实现无编辑构建路径、失败分析模块和统一渲染。

## 11. 不改变的事实与边界

- 无编辑标定只回答“在该原始场景、该站位、该控制配置下任务能否完成”，不构造困难模式，不产生 `final_labels` 或 `intent_hit`。
- 规划无解、未执行和 unknown 保持 unknown，不写成任务失败。
- 构建阶段不跑 IK/TrajOpt；真实成败只能来自标定 trial。
- 生产规划的 IK/TrajOpt 只暴露选中结果，不能伪造完整 seed 分母；seed 级结论只来自显式 probe。
- 所有原始 XML、外部资产和既有 run 只读；本次新建独立 run。
