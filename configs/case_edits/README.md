# `configs/case_edits/` 配置说明

## 新增：纯程序化障碍物增强（独立入口）

- `procedural_obstacles_val103_cup30_chair_debug.json`：Cup_30，5 份独立随机变体，每份必须尝试新增 1 把 Chair。
- `procedural_obstacles_val103_cup30_random_1to3.json`：随机 1–3 个，保留必须 Chair，按餐桌环境允许桌面小物体。
- `procedural_obstacles_val103_cup30_case1.json`：`construction.case=Case1`，1–3 个、恰好一椅，其余目标附近桌面。
- `procedural_obstacles_val103_cup30_case2.json`：`construction.case=Case2`，6–12 个、全在目标自身支撑面，至少两件实测大／高物体。
- Case 配置共享全量静态预筛（少于 5 则跳过）、增强后全量独立初始化和距离／方位覆盖式压缩到最多 20 点。
- 上述配置使用 `procedural-obstacles-v1`，入口 `augment-obstacles --config ... --run-id ...`。
  **不传给下述四 Agent `case-edit` 入口，不需要 API/provider 配置。**
- 路径按文件所在目录解析；当前相对路径对应新 worktree，迁移位置需显式调整。
- 完整方法、阈值、真实验收范围和审计命令见 [程序化障碍物增强](../../docs/procedural-obstacles.md)。

## 既有四 Agent case-edit 配置

自动 **case → 场景编辑**流水线（四 Agent 本地单场景构造）的配置。用 CLI：
`case-edit --request <本目录某 json> --api-settings configs/agent_api.json [--provider xera]`。
请求契约见 `construction/scene_request.py` 的 `SceneConstructionRequest` 等。

> 该流水线只做**构造**（`verification_mode=construction_only`），**不验证移动任务**。
> 数据集检索与真实移动任务验收当前明确 unsupported。

## 请求文件（`case1-val103.json` / `case1-5-val103.json` / `case3-val103.json`）

三者结构相同，只差 `case_type` 与 `case_description`。请求版本固定 `"0.2"`。

| 字段 | 当前值 | 含义 |
|---|---|---|
| `request_version` | `"0.2"` | 请求契约版本，只接受 0.2。 |
| `scene_input.kind` | `"single"` | 只支持单场景；`dataset` 未实现。 |
| `scene_input.dataset_dir` | `../../../molmospaces_data/assets/scenes/procthor-10k-val` | ProcTHOR 场景根。相对本文件解析。 |
| `scene_input.scene_id` | `"val-103"` | 场景 ID；会校验与实际 split 目录一致。 |
| `case_type` | `case1` / `case1.5` / `case3` | v0.2 范围只含这三类。 |
| `case_description` | 各文件不同 | 自然语言构造目标。case1=不利接近位；case1.5=三方向差异；case3=桌面障碍阻挡接近路径。 |
| `task_type` | `"pick"` | v0.2 只实现 pick 构造。 |
| `objective_mode` | `"beneficial_reposition"` | 也可 `required_reposition`。 |
| `verification_mode` | `"construction_only"` | 也接受 `mobile_task`，但当前实际只跑构造。 |
| `target_variant_count` | `1` | 每个目标生成的变体数。 |
| `seed` | `42` | 随机种子。 |

### `budgets`（`ConstructionBudget`）

| 字段 | 当前值 | 含义 |
|---|---|---|
| `max_rounds` | `2` | 最大提案轮数。 |
| `proposals_per_round` | `3` | 每轮提案数。 |
| `samples_per_proposal` | `4` | 每个提案的采样数。 |
| `max_samples` | `24` | 总采样上限。 |
| `max_agent_calls` | `20` | Agent 调用总预算。 |
| `timeout_s` | `900` | 整个请求的墙钟期限（s）。 |
| `agent_timeout_s` | `240` | 单次 Agent 调用超时（s）。 |

### `construction`（`LocalConstructionConfig`）

| 字段 | 当前值 | 含义 |
|---|---|---|
| `asset_library` | `../../../molmospaces_data/assets/objects/thor` | 资产检索根（THOR 根目录），按需检索类别并实际编译。 |
| `max_contexts` | `6` | 局部上下文数量上限。 |
| `max_nodes` | `240` | 局部图节点上限。 |
| `assets_per_category` | `2` | 每类别检索资产数。 |

未写出、走默认值的字段：`radius_m=2.5`、`max_radius_m=4.5`、`max_initialization_trials=96`、
`initialization_candidates=3`、`max_information_requests=2`、`observation_distance_m=[0.8,2.2]`、
`min_target_pixels=24`。

## `settling.json`（构建期静置/校验阈值）

供 `case-edit --settle-config` 使用，映射到 `runtime/preparation.py: SettleConfig`。
这是一份**宽松构造阈值**：取消源场景稳定窗口门槛，编辑范围仍检查稳定、严重穿透和支撑。

| 字段 | 当前值 | 默认 | 含义 |
|---|---|---|---|
| `settle_s` | `1.0` | `1.0` | 编辑后静置时长（s）。 |
| `max_settle_s` | `10.0` | `3.0` | 静置上限（s），放宽。 |
| `require_source_stability` | `false` | `true` | 关掉源场景稳定窗口门槛。 |
| `window_s` | `0.12` | `0.25` | 稳定性判定窗口（s）。 |
| `speed_m_s` | `0.15` | `0.025` | 线速度上限，放宽。 |
| `angular_speed_rad_s` | `0.5` | `0.10` | 角速度上限，放宽。 |
| `drift_m` | `0.03` | `0.006` | 位置漂移上限，放宽。 |
| `rotation_rad` | `0.15` | `0.04` | 姿态变化上限，放宽。 |
| `penetration_m` | `0.03` | `0.01` | 严重穿透阈值。 |
| `support_tolerance_m` | `0.02` | `0.008` | 支撑面容差。 |
| `boundary_tolerance_m` | `0.005` | `0.001` | 边界容差。 |

## `views.json`（配对视图渲染）

供 `case-edit --view-config` 使用，映射到 `recording/edit_views.py: ViewConfig`。
head 与动态辅助相机配对；辅助相机数量和搜索次数有界，覆盖不足明确记录。

| 字段 | 当前值 | 含义 |
|---|---|---|
| `width` / `height` | `640` / `480` | 渲染分辨率。 |
| `fovy_rad` | `0.785398…`（π/4） | 相机垂直视场角（rad），需在 (0.1, 2.5)。 |
| `max_expansions` | `3` | 取景扩张次数上限（0–5）。 |
| `max_review_retries` | `1` | 复查重试上限（0–5）。 |
| `initial_group_tolerance` | `0.02` | 初始视角分组容差。 |
| `camera_position_tolerance_m` | `0.02` | 配对相机位置容差（m）。 |
| `camera_matrix_tolerance` | `0.03` | 配对相机矩阵容差。 |

未写出、走默认值的字段：`initial_aux_views=2`、`max_aux_views=3`、`max_camera_trials=32`。

## Agent 侧配置

`../agent_api.json`（即 `configs/agent_api.json`）是用户本地独立 provider 配置，见顶层 README。
**不要把密钥提交或复制到报告。**
