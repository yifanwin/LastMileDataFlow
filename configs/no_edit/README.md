# `configs/no_edit/` 配置说明

**无编辑原始场景采集**（不调用编辑 Agent）。加载入口
`stations/no_edit_config.py: load_no_edit_config(path)`，字段见同文件 `NoEditConfig`。
`policy_config` 以本文件所在目录为基准解析。
用 CLI：
`collect-no-edit --assets-dir ... --config <本目录某 json> --run-id <唯一id> [--houses/--targets/--max-trials ...]`。
流程说明见 [`docs/no-edit-collection.md`](../../docs/no-edit-collection.md)。

## 公共字段

### 后端与模式

| 字段 | 默认 | 含义 |
|---|---|---|
| `schema_version` | `no-edit-v1` | 只支持该值。 |
| `operation_backend` | `curobo` | `curobo` 或 `molmobot`（VLA）。 |
| `policy_config` | `null` | MolmoBot 时必填，指向 `policies/molmobot.json`。 |
| `planner_backend` | `legacy` | `legacy` 或 `curobo_v2_v080`（原生 cuRobo v0.8.0 联合 10D 规划）。 |
| `operation_base_mode` | `holonomic_joint_planning` | 必须匹配后端：molmobot 须为 `policy_relative`。 |
| `torso_error_policy` | `record_only` | 固定 `record_only`：躯干误差只记录，不硬终止。 |

### 采样与站位图

| 字段 | 默认 | 含义 |
|---|---|---|
| `radius_m` | `2.0` | 以目标锚点为圆心的采样半径（m）。 |
| `spacing_m` | `null` | 实测点采样间距；`null` 时回退为实际底盘外接圆半径 `robot_footprint['radius_m']`。 |
| `trials_per_station` | `5` | 每个有效站位的重复试验次数（热图分母）。 |
| `discard_below` | `0.05` | 构造成功率低于此值则整体判 `discarded_low_success`。 |
| `start_count` | `3` | 期望选出的 S0 起点数（并作为"偏好 rollout 数"）。 |
| `min_successful_rollouts` | `1` | ≥1 段成功即发布（`retained`）。 |
| `min_target_pixels` | `24` | 目标可见像素下限，低于则初始化不可用。 |
| `map_resolution_m` | `0.05` | 热图/导航图栅格分辨率（m）。 |
| `smoothing_sigma_m` | `null` | 高斯带宽；`null` 沿用采样间距。 |
| `smoothing_support_distance_m` | `0.35` | 最大扩散支持距离，实际截断 `min(3σ, 该值)`。 |

### 导航与到达

| 字段 | 默认 | 含义 |
|---|---|---|
| `navigation_margin_m` | `0.02` | A* 避障膨胀安全余量（m）。 |
| `navigation_speed_m_s` | `0.20` | 导航线速度。 |
| `navigation_yaw_speed_rad_s` | `0.4` | 导航角速度。 |
| `arrival_tolerance_m` | `0.015` | 到站位置容差（m）。 |
| `arrival_tolerance_rad` | `0.025` | 到站 yaw 容差（rad）。 |
| `max_navigation_steps` | `2400` | 单次导航最大控制步数。 |
| `max_s1_candidates` | `0` | 每个 S0 考虑的 S1 候选上限；`0` 表示全部。 |

### 规划/执行预算

| 字段 | 默认 | 含义 |
|---|---|---|
| `attempt_timeout_s` | `300` | 单 attempt 超时（s）。 |
| `scene_timeout_s` | `86400` | 单场景墙钟期限（s）。 |
| `max_operation_plans` | `12` | pick 规划次数上限。 |
| `max_open_plans` | `128` | open 规划次数上限。 |
| `max_grasp_candidates` | `4` | 每次尝试的抓取候选数。 |
| `num_ik_seeds` | `32` | IK 种子数。 |
| `num_trajopt_seeds` | `4` | trajopt 种子数。 |
| `max_attempts` | `3`（`curobo_v2_v080` 强制 `5`） | cuRobo 原生规划尝试上限。 |
| `time_dilation` | `0.5` | 轨迹时间膨胀因子（0,1]。 |
| `approach_offset_m` | `0.01` | 接近深度偏移（m），限 `[0,0.02]`。 |
| `operation_retries` | `1` | 外层重试（V2 至多 1 次）。 |

### 输出与渲染

| 字段 | 默认 | 含义 |
|---|---|---|
| `operations` | `["pick","open"]` | 执行的操作集。 |
| `torso_heights` | `[0,0.369,0.738]` | 候选躯干高度。 |
| `seed` | `20261009` | 主种子（与 house/task/station/trial 派生各 trial 种子）。 |
| `width` / `height` | `320` / `240`（MolmoBot 强制 `1024×576`） | 渲染分辨率。 |
| `min_free_disk_gb` | `50` | 启动前要求的磁盘空闲（GB）。 |
| `third_person_enabled` | `true` | 是否启用第三人称分析相机。 |
| `head_fov_enabled` | `true` | V2 head FOV 硬约束（进程内默认开，配置未写出时生效）。 |
| `head_fov_margin` / `head_fov_weight` / `head_fov_min_depth_m` | `0.9` / `10000` / `0.01` | FOV 约束边距/代价权重/最小深度。 |

### open 操作参数（`operations` 含 open 时）

`gripper_width_m=0.10`（钳口行程）、`open_fraction=0.3`、`open_hinge_rad=π/6`、`open_slide_m=0.10`、
`open_hold_s=1.0`、`open_waypoint_step_rad=0.04`、`open_waypoint_step_m=0.008`。

## 本目录四个文件的差异

| 文件 | 后端 / 规划器 | 关键字段 | 用途 |
|---|---|---|---|
| `val.json` | curobo / `legacy` | `radius_m=2.0`、`spacing_m=null`、320×240 | 旧后端（legacy）默认配置。 |
| `curobo_v080.json` | curobo / **`curobo_v2_v080`** | `max_attempts=5`、`width=320/height=240`、`seed=20261009` | 当前 CLI 默认配置：原生 cuRobo v0.8.0 联合 base+单臂 10D 规划。 |
| `curobo_v080_arm_reach_half_base_radius.json` | curobo / `curobo_v2_v080` | 仅改 `radius_m=0.7928056080178096`、`spacing_m=0.2217592080002628`，其余同 `curobo_v080.json` | **臂展半径 / 半底盘半径间距**的数值冻结配置，见顶层 README 与 `scripts/verify_reach_sampling.py`。 |
| `molmobot.json` | **molmobot** / `legacy` | `policy_config=../policies/molmobot.json`、`width=1024/height=576`、`seed=20261011` | VLA 后端（`configs/policies/molmobot.json` 定义模型），只做站位评测、不跑 A* 导航。见 [`docs/molmobot-station-evaluation.md`](../../docs/molmobot-station-evaluation.md)。 |

`curobo_v080_arm_reach_half_base_radius.json` 是当前模型的数值冻结配置，**不随更换模型自动更新**；
模型变化时先跑 `scripts/verify_reach_sampling.py`，配置与模型不匹配会拒绝通过。该口径不代表任意方向、
任意目标高度都可抓取，也不包含自碰撞约束。

`molmobot.json` 中 `station_only` 语义：`discard_below`/`start_count`/`min_successful_rollouts`/
`operation_retries` 等字段走默认但不参与 MolmoBot 判定，状态终值为 `station_evaluation_complete`
（采样完成，**不表示任务成功**）。
