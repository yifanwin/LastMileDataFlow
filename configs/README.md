# 配置

所有输入配置是 YAML，由各自的加载器用 `config.read_config`（`yaml.safe_load`）读取；只写与代码默认值不同的字段，未写的字段取对应 dataclass 的默认值。运行目录里写出的 `config.json`、`summary.json` 等产物仍是 JSON。从仓库根目录运行。

子目录名就是读取它的源码包名：配置由哪个包的代码加载，就放在同名目录下，子路径与包内子目录对应。

| 目录 | 加载它的代码 | 用途 |
|---|---|---|
| `scenes/` | `scenes.library.load_scene_recipe`（`scripts/scenes/build.py`） | 场景配方：房屋、目标筛选、障碍数量、站位参数、构建进程 |
| `robots/` | `robots.registry.load_robot_config` | 机器人模型、控制 profile、相机与初始姿态；由场景配方的 `robot_config` 引用 |
| `policies/` | `policies.config.load_policy_config` | MolmoBot / π0.5 权重、tokenizer、解释器；由执行配置的 `policy_config` 引用，`scripts/lastmile_benchmark/evaluate.py --config` 直接读取 |
| `station_map/` | `station_map.config`（`--config`）→ `station_map.planner_trials.build` 用 `operation.config.load_execution_config` 解析 | 执行配置：物理执行、规划、导航、成功率阈值；站位标定（`scripts/station_map/planner_trials.py`）和连续采集（`scripts/lastmile_dataset/collect.py`）共用 |
| `station_map/rl/` | `station_map.rl.training` | 独立 RL 站位图研究配置 |
| `lastmile_benchmark/` | `lastmile_benchmark.summary.load_benchmark_config` | 困难起点、恢复区域与评测预算；`scripts/lastmile_benchmark/build.py` 不给 `--config` 时用默认值（即 v0.1） |

`local.example.yaml` 可复制为 `local.yaml`，只提供 `assets_dir`（站位标定、采集省略 `--assets-dir` 时使用）。

## 路径规则

- 含数据输入的配置显式填写 `data_root`（当前统一为 `/data1/wenyifan/lmdf/molmospaces_data`）；数据、权重、tokenizer 路径相对 `data_root`。
- 配置文件和解释器路径（`robot_config`、`policy_config`、`python`）相对仓库根目录，也可写绝对路径。不是相对 YAML 所在目录。
- 命令行路径按当前调用目录解释。

## 文件

| 文件 | 说明 |
|---|---|
| `scenes/panda_omron.yaml` | PandaOmron 场景配方：procthor-10k-val 前 10 个 house，每 house pick、open 各 1 个目标；每个任务一份原场景、一份编辑场景 |
| `robots/panda_omron.yaml` | PandaOmron，robocasa-v1 控制、三相机；场景构建和 cuRobo、π0.5 执行共用 |
| `robots/rby1.yaml` | RBY-1，position-v1 |
| `station_map/panda_omron_curobo.yaml` | PandaOmron cuRobo 站位标定与连续采集（3 次/站位，1/3 与 1.0 阈值） |
| `station_map/rby1_curobo.yaml` | RBY-1 cuRobo 站位标定与连续采集 |
| `station_map/molmobot.yaml` | MolmoBot 逐站位策略 rollout，RBY-1 native profile、1024×576 |
| `station_map/pi05_panda_omron.yaml`、`pi05_rby1.yaml` | π0.5 逐站位策略 rollout |
| `policies/molmobot.yaml`、`pi05_panda_omron.yaml`、`pi05_rby1.yaml` | 策略服务配置 |
| `lastmile_benchmark/panda_omron.yaml` | Panda benchmark，1/3 与 1.0 阈值 |
| `station_map/rl/val103_cup30.yaml` | RL 站位图研究 |
| `station_map/rl/val103_cup30_s0246_y04.yaml` | 固定历史 S0246-y04 世界位姿的单站位 SAC 训练 |

冒烟脚本自己持有的配置（`panda_omron_scene.yaml`、`panda_omron_position.yaml`、`panda_omron_mobile.yaml`）放在 `tests/smoke/`。

## 场景配方

必填 `data_root`、`dataset_dir`、`assets_dir`、`asset_index`、`robot_config`、`houses`。`edits` 默认桌面障碍 1–3 个（目标 0.3 m 内同一支撑面）、地面 0–2 个；`edits.enabled: false` 只生成原场景。`max_proposals`、`variant_timeout_s` 控制编辑候选预算，`task_timeout_s` 控制单任务总预算，`build_workers` 控制房屋并行数。

## 机器人

`model_path` 是 MuJoCo 机器人 XML（相对 `data_root`），不改写外部 XML。`arm_working_reach_m` 是站位圆盘半径，`base_radius_m` 的四分之一是站位网格间距。`name: panda_omron` 选择 Panda 单臂适配器，`initial` 用 `base_xyyaw/lift/arm_q/gripper_width`；`observation_camera` 是站位筛选相机。命名空间 `robot_0/` 和 RBY-1 规划适配器 `joint_waypoints_v1` 是协议常量，不再写在配置里。执行流程从场景快照读取机器人配置，改这里的文件不会追溯修改已有快照；改变控制模式、步长、相机或分辨率后要重新生成场景和标定。

RL 研究配置通过 `stations.robot_config` 指向该机器人 YAML（相对仓库根目录）。训练、评分、验收启动时读取其中的 `arm_working_reach_m` 与 `base_radius_m`，生成有效采样半径和间隔并保存到 checkpoint。尺寸值不再乘一次 `scene.robot_scale`；缩放模型后需同步维护机器人 YAML 中的实际尺寸。

只训练一个底盘位姿时，设置 `stations.fixed_station: {station_id: S0246-y04, base_xyyaw: [x, y, yaw]}`，其中 x/y 为世界坐标米数，yaw 为世界朝向弧度。该选项替代圆盘网格和空间划分，生成唯一 train 站位，仍检查 Home 碰撞和地面支撑；不合法时直接报错。所有并行环境在该位姿重置，训练结束后只评估并评分该站位，结果标记 `scope: fixed_station`，不表示留出站位泛化。`training.debug_station_ids` 保持空列表；删除 `fixed_station` 恢复原网格模式。站位编号随网格参数变化，历史站位应从原始站位记录取实际位姿，不能在新网格里按同一编号猜测。

RL 训练默认每 30 秒向 `<run>/tensorboard/` 更新监控指标；可选 `training.tensorboard_every_seconds` 调整该间隔，必须大于零。它独立于 `training.log_every`（learner JSON 和 checkpoint 的 transition 间隔）。旧配置可直接使用，查看方式和统计口径见 [RL 运行指南](../reports/rl-station-map/delivery/running.md#tensorboard-实时监控)。

## 执行配置

- 场景库的 `geometry` 只覆盖 `spacing_m`、`width`、`height`、`min_target_pixels`；操作范围与导航设置以执行配置为准，最终以 run 里保存的有效配置为准。
- cuRobo 的规划后端（`curobo_v2_v080`）、`max_attempts=5`、躯干误差策略 `record_only` 是代码常量，结果文件照旧写出。
- `trials_per_station` 是每站位独立 rollout 数；`operation_retries` 是 cuRobo 导航后的操作外层重试（0 或 1）；`attempt_timeout_s` 是单 episode 执行预算。
- `lastmile_low/high` 选连续采集的 S0（低成功率）和 S1（高成功率），全部合格站位全组合配对。
- `smoothing_sigma_m` 只影响展示热图；`export_figures: false` 跳过热图。
- 可选：`head_fov_enabled` 及 `head_fov_*`（cuRobo 头部视野约束）；`navigation_margin_m`、`navigation_clearance_weight`、`navigation_max_penetration_m`（导航软净空与严重穿透阈值）；`navigation_speed_m_s`、`navigation_yaw_speed_rad_s`、`arrival_tolerance_*`、`max_navigation_steps`（导航执行与到达判定）。改变物理或成功判定参数要重新验证。
- 进程数、记录模式和试验预算不在执行配置里，由 `scripts/station_map/planner_trials.py`、`scripts/lastmile_dataset/collect.py` 的命令行给出（`--assets-dir --max-trials --record-video --recording-mode --planning-workers-per-gpu --cpu-workers --preparation-workers`），不参与标定复用的配置匹配。`--max-trials` 截断的标定不完整，不能用于正式困难 S0 选择。
- MolmoBot 逐点 rollout 与 cuRobo 连续采集不是同一能力；连续采集只支持 cuRobo。

## 策略

`python` 指向本项目的 `.venv/bin/python`（MolmoBot）或 `.venv-pi05/bin/python`（π0.5）。`task_horizon` 是每任务策略步上限（默认 400）；`gripper_mode` 默认 `model_default`（pick 连续、open 二值）。`load_timeout_s=900` 是模型服务启动等待，`rpc_timeout_s` 是单次 RPC 等待，episode 总预算用执行配置的 `attempt_timeout_s`。π0.5 的 `embodiment` 必须匹配场景机器人，命令见 [π0.5 指南](../reports/pi05/delivery/running.md)。`pi05_rby1.yaml` 的 `norm_stats` 指向的统计文件目前不存在，使用前需先运行 `scripts/lastmile_dataset/export_pi05.py` 重新生成。

## Benchmark

构建直接读取已有场景和标定，不启动新 trial。`low/high`（默认 0.20/0.60，含等号）是参考协议下起点成功率阈值；`region_radius_m`、`region_yaw_tolerance_rad` 是高率站位周围合法恢复区域；`navigation_*` 同执行配置；`local_candidate_radius_m: null` 用整个候选域算 Recovery Ratio；`max_episodes_per_scene: 0` 保留全部合格起点；`horizon`/`wall_timeout_s` 是评测预算。导航使用完整来源房间地面。

运行步骤见 [运行手册](../docs/runbook.md)。
