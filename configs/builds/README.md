# `configs/builds/` 配置说明

阶段二 **v2 场景构建契约**（规则式、有限编辑）。加载入口
`construction/config.py: load_build_config(path)`，字段见同文件 `BuildConfig` /
`BuildProtocol` / `BuildBudget`。`asset_pool` 以本文件所在目录为基准解析。
用 CLI：
`build --house <h> --build-config <本目录某 json> [--initial-snapshot ...]`。

## 顶层字段（`BuildConfig`，schema_version 必须为 `"2.0"`）

| 字段 | 默认 | 含义 |
|---|---|---|
| `schema_version` | 必填 `"2.0"` | 契约版本。 |
| `task_id` | 必填 | 任务标识。 |
| `case_type` | 必填 | `case1 / case1.5 / case2 / case3`，决定必需参数。 |
| `target` | 必填 | 目标实例 ID，或检索占位符 `@retrieved`。 |
| `support` | 必填 | 支撑家具实例 ID，或 `@parent`（父对象）。 |
| `operation` | `pick` | 操作类型。 |
| `region_geom` | `null` | 限制到某个实际碰撞几何的区域。 |
| `robot_base` | `null` | 机器人底盘初态 `[x, y, yaw]`（3 个数，yaw 不是四元数）。 |
| `editable` | `[]` | 允许被编辑的实例 ID 列表。 |
| `allowed_operations` | `["move","rotate"]` | 允许的编辑操作；合法集 `move/rotate/add/delete`（joint 初始化与 scale 未实现）。 |
| `protected` | `[]` | 受保护实例；与 `editable` 不得重叠。 |
| `parameters` | `{}` | 模板参数，按 `case_type` 校验（见下）。 |
| `initial_operations` | `[]` | 构建前直接施加的操作列表，每项 `{"instance","op","pose"}`，`pose` 为 7 维 `xyz + wxyz`（归一化四元数）。 |
| `asset_pool` | `null` | 可选资产池路径。 |
| `protocol` | 见下 | 落位/稳定性/副作用验收协议 `placement-v2`。 |
| `budget` | 见下 | 构建预算。 |

### 各 case_type 的必需参数

| case_type | 必需 `parameters` | 含义 |
|---|---|---|
| `case1` | `distance_range_m: [lo,hi]` | 目标 body 原点到机器人底盘的水平距离区间（m）。 |
| `case1.5` | `frame_body`, `side_a`, `side_b`, `min_distance_difference_m`, `clearance_radius_m`, `min_clearance_difference_m` | 家具局部坐标系中两侧的距离与保守几何净空差异。 |
| `case2` | `handle`（含 `body/axis_local/source/verified=true`）, `desired_direction_world` | 有来源且已验证的部件局部轴，转世界方向。 |
| `case3` | `obstacle`, `approach_offset_m`, `obstacle_distance_range_m` | 障碍在世界坐标接近走廊内且稳定。 |

公共可选参数：`height_range_m`、`yaw_candidates_rad`（默认 0/±90°/180°）。
所有参数走严格校验：未知模板参数、非有限值、非法区间直接报错。

## `BuildProtocol`（`placement-v2`，物理验收阈值）

| 字段 | 默认 | 含义 |
|---|---|---|
| `version` | `placement-v2` | 协议版本，只支持该值。 |
| `settle_s` | `1.0` | 编辑后静置时长（s）。 |
| `max_settle_s` | `2.0` | 静置时长上限（s）。约束 `window_s <= settle_s <= max_settle_s <= 10`。 |
| `window_s` | `0.25` | 稳定性判定窗口（s）。 |
| `speed_m_s` | `0.025` | 窗口内物体线速度上限（m/s）。 |
| `angular_speed_rad_s` | `0.10` | 窗口内角速度上限（rad/s）。 |
| `drift_m` | `0.006` | 窗口内位置漂移上限（m）。 |
| `rotation_rad` | `0.04` | 窗口内姿态变化上限（rad）。 |
| `edge_margin_m` | `0.01` | 物体足迹距支撑面边缘的安全余量（m）。 |
| `support_tolerance_m` | `0.008` | 底高与指定支撑面的允许偏差、指定支撑接触容差（m）。 |
| `penetration_m` | `0.01` | 严重穿透判定阈值（m）。 |
| `protected_translation_m` | `0.02` | 受保护物体允许的最大平移（m）。 |
| `protected_rotation_rad` | `0.10` | 受保护物体允许的最大转角（rad）。 |
| `robot_translation_m` | `0.02` | 编辑期机器人最大平移（m）。 |
| `robot_rotation_rad` | `0.05` | 编辑期机器人最大转角（rad）。 |
| `flight_m` | `0.25` | 轨迹越界行程上限（m）。 |

## `BuildBudget`

| 字段 | 默认 | 含义 |
|---|---|---|
| `candidates` | `48` | 候选编辑数上限。 |
| `edits` | `12` | 实际编辑预算。 |
| `repairs` | `3` | 修复重试预算。 |
| `agent_calls` | `0` | Agent 调用预算（规则式构建默认 0）。 |
| `timeout_s` | `120` | 墙钟期限（s）。 |

## 本目录具体文件

| 文件 | 用途 |
|---|---|
| `case1-retrieval.json` | 从静态索引检索目标；`target="@retrieved"`、`support="@parent"`，`parameters.distance_range_m=[0.8,1.6]`，无 Agent 调用。 |
| `phase2-cached-train0-move.json` | 使用 `--initial-snapshot` 的缓存构建：目标盐瓶显式 `move` 到给定 7 维位姿（`initial_operations`），`distance_range_m=[0.3,8.0]`。 |
| `phase2-cached-train0-zero.json` | 同上场景，但 `initial_operations=[]`（零编辑对照）。 |
| `phase2-cached-train2-move.json` | 同 train0 模式，场景换 train2、目标为闹钟。 |
| `phase3-case1-cup.json` | 阶段三 Cup 抓取的原始构建：目标/支撑与起点（`robot_base=[6.3,2.7,-0.5354]`）明确，`distance_range_m=[1.0,1.5]`，预算 `candidates=12/edits=4/repairs=2`，`timeout_s=900`。 |

`phase2-cached-*` 依赖 `--initial-snapshot` 传入未经编辑的 v1 冻结模型；`case1-retrieval.json`
走 `--index` 检索并需 `--category`。构建输出为 `TaskCandidate`，**不是机器人成功标签**。
