# 执行与逐点标定配置

用于 `evaluate-stations --config`，也可供 `collect-lastmile --config` 使用。加载器为 `runtime/execution_config.py`。标定使用场景库中已经保存的站位，不在这里重新生成站位。

## 重点手动配置

- **选择后端**：cuRobo 用 `curobo_v2.json`；MolmoBot 用 `molmobot.json`，并修改其引用的 [策略配置](../policies/README.md)。
- **实验规模**：`trials_per_station`、`operations`、`attempt_timeout_s`、`seed`。
- **不要任意修改固定约定**：cuRobo V2 的 `planner_backend`、`max_attempts=5`、`torso_error_policy=record_only`；两后端的底盘模式和分辨率要求不同。
- **CLI 手动指定**：`--dataset`、`--assets-dir`、`--output-dir`、唯一 `--run-id`、`--gpu`；记录开关见 [collection](../collection/README.md)。

## 数据集参数优先级

`workflows/collection.py` 会用数据集 config.json 的 geometry 覆盖 `radius_m`、`spacing_m`、`width`、`height`、`min_target_pixels`、`map_resolution_m`、`navigation_margin_m`（仅覆盖已存在的键）。因此只改执行 JSON 不一定生效；最终以运行保存的有效配置为准。MolmoBot 必须使用 native 控制且几何分辨率匹配的数据集，不能直接对 position/320×240 数据集套用此预设。

## 两个预设的区别

| 文件 | 操作后端 | 底盘模式 | 图像尺寸 |
|---|---|---|---|
| `curobo_v2.json` | cuRobo V2 0.8.0（operation_backend 默认 curobo） | holonomic_joint_planning | 320×240 |
| `molmobot.json` | MolmoBot multitask | policy_relative | 1024×576，当前加载器固定要求 |

MolmoBot 逐点 rollout 与 cuRobo 连续恢复采集不是同一个能力；当前连续采集仅支持 cuRobo。

## JSON 字段

| 字段 | 含义 |
|---|---|
| `operation_backend` | `curobo` / `molmobot`，选择真实操作执行器 |
| `policy_config` | MolmoBot 服务配置 JSON，相对仓库根目录；MolmoBot 必填 |
| `planner_backend` | 当前仅 `curobo_v2_v080` |
| `operation_base_mode` | cuRobo 为 `holonomic_joint_planning`：规划含全向底盘；MolmoBot 为 `policy_relative`：策略相对动作 |
| `torso_error_policy` | 仅 `record_only`；躯干反馈偏差按该约定记录，不是自动恢复/重置开关 |
| `third_person_enabled` | 启用第三人称观察及分析视频路径；与原始机器人相机的 record_video 不是同一开关 |
| `radius_m` | 局部地图/分析范围半径（米）；该配置作为构造 baseline_config 时也供站位采样使用 |
| `spacing_m` | 站位采样间距（米；构造引用时）；评测不改变已有站位；也作为热图平滑宽度的后备值 |
| `trials_per_station` | 每站位独立 rollout 数，当前 5；不是同一 episode 内的重试次数 |
| `start_count` | 连续采集最多选择的困难 S0 数，当前 3；逐点标定不使用 |
| `operation_retries` | cuRobo 导航后的操作外层重试次数，0 或 1；逐点无导航时不做该外层重试 |
| `operations` | 任务类型列表：pick / open；构造引用时用于任务发现，当前 collection 遍历已存场景时不据此过滤，评测子集用 CLI `--samples` |
| `min_target_pixels` | 目标可见性像素门槛，当前 24；用于任务发现/站位检查，不是任务成功阈值 |
| `map_resolution_m` | 局部栅格地图单元边长，当前 0.05 m |
| `smoothing_sigma_m` | 展示热图的平滑尺度（米）；null 时取 spacing_m，再后备 0.1 m；不用于困难点选择 |
| `smoothing_support_distance_m` | 保留的支持距离参数，当前 0.35 m；当前 collection 热图调用未使用此值，修改不产生该距离限制 |
| `attempt_timeout_s` | 单 episode 执行时间预算（秒），当前 300；不是模型加载时限 |
| `width` / `height` | 执行相机图像宽/高（像素）；MolmoBot 当前必须 1024×576 |
| `seed` | trial 随机种子基础值 |
| `max_attempts` | cuRobo V2 原生 plan_pose 参数，当前必须为 5；不是 trial 数 |

连续采集额外字段 `lastmile_low/high`、`lastmile_nearby_m`、`max_s1_candidates` 见 [lastmile_datasets](../lastmile_datasets/README.md)。未填写字段取 ExecutionConfig 默认值，不从另一个 JSON 继承。

## 常用可选项（现有 JSON 未填写）

- `head_fov_enabled=true`：cuRobo 头部视野约束；`head_fov_margin=0.9`、`head_fov_weight=10000`、`head_fov_min_depth_m=0.01` 控制边界、权重和最小深度。
- `navigation_margin_m=0.02`、`navigation_speed_m_s=0.20`、`navigation_yaw_speed_rad_s=0.4`：A* 导航余量和执行速度。
- `arrival_tolerance_m=0.015`、`arrival_tolerance_rad=0.025`、`max_navigation_steps=2400`：到达误差与导航预算。
- 其他规划和打开动作参数直接见 `ExecutionConfig`；改变物理/成功判定参数时需要重新验证，不为凑通过率修改。

成功率读取实际 trial，平滑热图只展示结果。`--max-trials` 截断可导致标定不完整，不能用于正式困难 S0 选择。
