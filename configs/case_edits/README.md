# `configs/case_edits/` 配置说明

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
