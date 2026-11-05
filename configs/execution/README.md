# 执行与逐点标定配置

用于 `collect-lastmile --config`（内置逐点标定与连续采集），加载器为 `runtime/execution_config.py`。标定使用场景库中已经保存的站位，不在这里重新生成站位。

例外：`panda_omron_base_map.json` 是 `evaluate-stations --config` 的底盘可达质量图参数，由 `config.load_base_map_config` 读取（`BaseMapConfig`），不是 ExecutionConfig；字段见文末。

## 重点手动配置

- **选择后端**：cuRobo 用 `curobo_v2.json`；MolmoBot 用 `molmobot.json`，并修改其引用的 [策略配置](../policies/README.md)。
- **实验规模**：`trials_per_station`、`operations`、`attempt_timeout_s`、`seed`。
- **不要任意修改固定约定**：cuRobo V2 的 `planner_backend`、`max_attempts=5`、`torso_error_policy=record_only`；两后端的底盘模式和分辨率要求不同。
- **CLI 手动指定**：`--dataset`、`--assets-dir`、`--output-dir`、唯一 `--run-id`、`--gpu`；记录开关见 [collection](../collection/README.md)。

π0.5 使用 `pi05_panda_omron.json` / `pi05_rby1.json`，Panda 路线 B 示教使用 `panda_omron_robocasa_curobo.json`。本体必须匹配显式 profile，命令见 [π0.5 指南](../../reports/pi05/docs/01-running.md)。

## 数据集参数优先级

`workflows/collection.py` 仅用数据集 geometry 覆盖 `spacing_m`、`width`、`height`、`min_target_pixels`（仅覆盖已存在的键）。操作范围与导航设置使用执行配置，最终以运行保存的有效配置为准。MolmoBot 必须使用 native 控制且几何分辨率匹配的数据集，不能直接对 position/320×240 数据集套用此预设。

## 执行预设

| 文件 | 操作后端 | 底盘模式 | 图像尺寸 |
|---|---|---|---|
| `curobo_v2.json` | cuRobo V2 0.8.0（operation_backend 默认 curobo） | holonomic_joint_planning | 320×240 |
| `molmobot.json` | MolmoBot multitask | policy_relative | 1024×576，当前加载器固定要求 |

MolmoBot 逐点 rollout 与 cuRobo 连续恢复采集不是同一个能力；当前连续采集仅支持 cuRobo。

## JSON 字段

| 字段 | 含义 |
|---|---|
| `operation_backend` | `curobo` / `molmobot` / `pi05`，选择真实操作执行器 |
| `policy_config` | VLA 服务配置 JSON，相对仓库根目录；MolmoBot / π0.5 必填 |
| `planner_backend` | 当前仅 `curobo_v2_v080` |
| `operation_base_mode` | cuRobo 为 `holonomic_joint_planning`：规划含全向底盘；MolmoBot 为 `policy_relative`：策略相对动作 |
| `torso_error_policy` | 仅 `record_only`；躯干反馈偏差按该约定记录，不是自动恢复/重置开关 |
| `third_person_enabled` | 启用第三人称观察及分析视频路径；与原始机器人相机的 record_video 不是同一开关 |
| `radius_m` | 局部地图/分析范围半径（米）；该配置作为构造 baseline_config 时也供站位采样使用 |
| `spacing_m` | 站位采样间距（米；构造引用时）；评测不改变已有站位 |
| `trials_per_station` | 每站位独立 rollout 数，当前 5；不是同一 episode 内的重试次数 |
| `start_count` | 不参与 lastmile-datasets 全组合；逐点标定不使用 |
| `operation_retries` | cuRobo 导航后的操作外层重试次数，0 或 1；逐点无导航时不做该外层重试 |
| `operations` | 任务类型列表：pick / open；构造引用时用于任务发现，当前 collection 遍历已存场景时不据此过滤，评测子集用 CLI `--samples` |
| `min_target_pixels` | 目标可见性像素门槛，当前 24；用于任务发现/站位检查，不是任务成功阈值 |
| `map_resolution_m` | 局部栅格地图单元边长，当前 0.05 m |
| `attempt_timeout_s` | 单 episode 执行时间预算（秒），当前 300；不是模型加载时限 |
| `width` / `height` | 执行相机图像宽/高（像素）；MolmoBot 当前必须 1024×576 |
| `seed` | trial 随机种子基础值 |
| `max_attempts` | cuRobo V2 原生 plan_pose 参数，当前必须为 5；不是 trial 数 |

连续采集阈值 `lastmile_low/high` 与不参与配对的保留字段 见 [lastmile_datasets](../lastmile_datasets/README.md)。未填写字段取 ExecutionConfig 默认值，不从另一个 JSON 继承。

## 常用可选项（现有 JSON 未填写）

- `head_fov_enabled=true`：cuRobo 头部视野约束；`head_fov_margin=0.9`、`head_fov_weight=10000`、`head_fov_min_depth_m=0.01` 控制边界、权重和最小深度。
- `navigation_margin_m=0.02`：期望净空的软目标，小于该值仍可通行；`navigation_clearance_weight=2.0` 控制有限惩罚，设 0 关闭惩罚。
- `navigation_max_penetration_m=0.005`：环境/自碰撞超过该穿透量时，导航记严重碰撞失败；普通地面接触另按既有规则处理。它与 20 mm 的软目标分开。
- `navigation_speed_m_s=0.20`、`navigation_yaw_speed_rad_s=0.4`：导航执行速度，也用于将转向折算为参考路线基础代价。
- `arrival_tolerance_m=0.015`、`arrival_tolerance_rad=0.025`、`max_navigation_steps=2400`：到达误差与导航预算。
- 其他规划和打开动作参数直接见 `ExecutionConfig`；改变物理/成功判定参数时需要重新验证，不为凑通过率修改。

成功率读取实际 trial。`--max-trials` 截断可导致标定不完整，不能用于正式困难 S0 选择。

## PandaOmron cuRobo

`panda_omron_curobo.json` 使用相同生产入口，默认局部底盘—机械臂联合规划、固定 lift、闭爪后固定底盘抬升。`navigation_whole_body=true` 使用保持姿态整机包络及高度建立导航栅格；`local_base_radius_m` 在当前局部 XY 两轴各约束 ±该值。`grasp_candidate_order=tcp_distance`、`grasp_roll_rad` 和 `approach_offset_m` 是显式候选参数，当前预设针对 Cup 杯沿案例。`initial_settle_s` 仅在独立站位操作开始时通过真实 stepping 静置；连续采集不会到站重置。切换 `operation_base_mode=fixed` 可做 Panda 固定底盘对照，须重新标定。`head_fov_enabled=false` 不启用 RBY 头部 FOV 约束；站位仍检查真实相机可见性。详见 [Panda 指南](../../reports/panda-omron/docs/panda-omron.md)。

Panda 配置的 `shared_station_preparation=true` 表示每站位静置一次，并用全局 GPU 规划池与独立 CPU trial 池标定。`initial_settle_s` 保留为站位准备时长，不再每个 trial 重复。资源和记录模式由 CLI 的 `--gpu`、`--cpu-workers`、`--preparation-workers`、`--recording-mode` 指定；不参与物理配置匹配。Panda 的 3 次、1/3 与 1.0 阈值只在对应配置生效。

`panda_omron_all_cases_curobo.json` 用于百来源 Case1–3 标定，开放 pick/open，其余预算、三次与阈值同主 Panda 配置。

## 底盘可达质量图（panda_omron_base_map.json）

| 字段 | 含义 |
|---|---|
| `spacing_m` | 地面网格间距，0.10 m；原点为物体碰撞包围盒中心 |
| `heading_offsets_deg` | 相对“底盘指向物体中心”的朝向偏角，−60…60 每 15°，共 9 个 |
| `max_grasps` / `grasp_position_scale_m` / `grasp_rotation_scale_rad` | 最多 5 个 grasp；物体系 TCP 位姿最远点采样的位置/旋转尺度 |
| `approach_offset_m` / `grasp_roll_rad` | 资产 grasp 到规划 TCP 的偏移与绕接近轴 roll，与固定抓取一致 |
| `seeds_per_goal` | 每 `(站位, grasp)` 的 IK seeds，32；全部返回并逐个复核 |
| `warm_start_seeds` / `warm_start_radius_m` / `warm_start_yaw_rad` | 热启动占预算内最多 8 个；邻点 0.15 m、朝向差 30° 内的已完成有效解 |
| `distinct_joint_tolerance_rad` | 统计不同解时的关节最大差，0.001 rad；不丢弃候选 |
| `pose_tolerance_m` / `pose_tolerance_rad` | MuJoCo FK 复核目标误差，5 mm / 0.05 rad |
| `characteristic_length_m` | Jacobian 平移行的特征长度，0.941 m |
| `collision_alpha` / `collision_beta` / `collision_query_m` | 距离势 $e^{-\alpha d}d^{-\beta}$ 参数（m⁻¹，无量纲）与查询上限 0.5 m |
| `penalize_support_surface` | 目标支撑物（桌面）是否进入距离惩罚，默认 false；目标物体始终只做硬碰撞检查 |
| `scoring_workers` / `chunk_stations` | CPU 评分进程数；每个候选文件的最多站位数 |
| `region_m` | 可选 `[dx_min, dx_max, dy_min, dy_max]`，相对物体中心裁剪试跑区域；null 为全范围 |

