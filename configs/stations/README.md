# `configs/stations/` 配置说明

阶段三 **v3 固定底盘站位图**采集配置（普通 case1 抓取）。加载入口
`stations/config.py: load_station_config(path)`，字段见 `StationConfig`。
`grasp_path`、`robot_planner_dir` 以本文件所在目录为基准解析。
用 CLI：
`station-map --station-config <本目录某 json> --build <build目录> --run-id <id>`。
站位配置 v3 与物理协议见 [`docs/phase3.md`](../../docs/phase3.md)。

## 固定字段（所有文件共有）

| 字段 | 含义 |
|---|---|
| `target` | 目标实例 ID。 |
| `asset_id` | 资产 ID（如 `Cup_30`）。 |
| `grasp_path` | 抓取库 `.npz` 路径。 |
| `grasp_sha256` | 抓取库摘要，必须 64 位 hex；不匹配拒绝。 |
| `robot_planner_dir` | cuRobo 机器人配置目录。 |
| `grasp_ids` | 使用的抓取行号列表（去重、非负）。 |
| `probes` | 显式底盘探针初态，每个为 `[x,y,yaw]`。**是输入初态，不是导航证据。** |
| `torso_heights` | 候选躯干高度 h 列表（∈[0,0.738]）。 |
| `arms` | 候选手臂（`left`/`right`）。 |
| `radii_m` | 目标周围粗采样半径列表（m）。空列表表示不做粗环采样，只用 probe/source。 |
| `max_candidates` | 候选站位上限（`coarse_samples` 截断此处）。 |
| `max_refinements` | 在已测成败边界附近的局部加密数。 |
| `max_plans` | 规划次数上限。 |
| `max_executions` | 真实执行次数上限。 |
| `timeout_s` | 整个站位图墙钟期限（s）。 |
| `attempt_timeout_s` | 单 attempt 超时（s）。 |
| `approach_offset_m` | 接近深度偏移（m），限定 `[0, 0.02]`。 |

未写出、走默认值的字段：`angle_step_deg=60`、`yaw_offsets_deg=[0]`、`include_source_base=true`、
`refine_step_m=0.10`、`num_ik_seeds=64`、`num_trajopt_seeds=4`、`max_attempts=5`、`seed=0`、
`time_dilation=0.5`、`width=640`、`height=480`、`render_video=true`、`protocol="strict-pick-v3"`。
`case_type` 只支持 `case1`（无把手 fallback），`schema_version` 固定 `3.0`，协议锁底盘。

采样逻辑（`stations/sampling.py`）：先把 `probes` 和源 base 作为显式初始化加入，再按 `radii_m ×
angle_step_deg × yaw_offsets_deg` 做粗环采样，全部通过几何过滤（地面覆盖/房间三角/机器人碰撞）后，
只对预算内配置做规划与真实执行。`max_candidates` 截断范围在配置中明确，**不宣称穷举整张地面**。

## 本目录三个文件的差异（同一站位 0 / 10 mm 接近深度对照）

| 文件 | `radii_m` | `arms` | `torso_heights` | `probes` | 预算 | 用途 |
|---|---|---|---|---|---|---|
| `case1-cup-probe.json` | `[]` | `["left"]` | `[0.738]` | 1 个 probe | `max_candidates=2, max_refinements=0, max_plans=10, max_executions=2, timeout_s=600` | 只测显式 probe 点（无 `approach_offset_m`）。 |
| `case1-cup-contact.json` | `[]` | `["left"]` | `[0.738]` | 同 probe | 同上，额外 `approach_offset_m=0.01` | probe 基础 + 10 mm 接近深度。 |
| `case1-cup-coarse.json` | `[0.65,0.9,1.2]` | `["left","right"]` | `[0.738,0.0,0.369]` | 同 probe | `max_candidates=28, max_refinements=2, max_plans=32, max_executions=4, timeout_s=1200`，`angle_step_deg=45` | 26 个粗位姿 + 最多 2 个局部细化。 |

**注意**：`case1-cup-coarse.json` 中两臂/三高度是候选配置，但受 `max_plans=32 / max_executions=4 /
timeout_s=1200` 限制，配置可能未全测，不能当成双臂实证。

三者 `target`/`asset_id`/`grasp_path`/`grasp_sha256`/`grasp_ids=[794]` 相同，为同一个 Cup_30 实验的
不同接近深度/采样范围对照。输出为分层结果（初始化/规划/执行/case 各自独立结论）。
