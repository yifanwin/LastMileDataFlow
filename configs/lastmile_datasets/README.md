# 连续采集配置

`v0.json` 用于 `collect-lastmile --config configs/lastmile_datasets/v0.json`，由同一个 `ExecutionConfig` 加载，**不是场景库配方**。流程为困难起点 S0 → A* 导航到较好站位 S1 → cuRobo 操作，不在中途重置/传送。

## 重点手动配置

1. 先准备对应场景库，CLI 指定 `--dataset`、`--assets-dir`。可用 `--calibration` 复用完整标定；不传则本次先逐点标定。复用时要求有效 ExecutionConfig 和场景/机器人/传感器/策略 profile 一致，建议 evaluate-stations 与 collect-lastmile 都使用本文件，而不是先用 curobo_v2.json 标定再切换本文件。
2. 按实验定义修改 `lastmile_low`、`lastmile_high`、`lastmile_nearby_m`，并固定记录。
3. 用 `start_count`、`max_s1_candidates`、`attempt_timeout_s` 控制规模和预算；每次使用新 run-id，显式选 GPU。

## v0.json 全部字段

| 字段 | 当前值 / 含义 |
|---|---|
| `operation_backend` | curobo；当前连续采集只支持此后端 |
| `planner_backend` | curobo_v2_v080；固定 V2 0.8.0 |
| `max_attempts` | 5；V2 原生规划参数，当前不可改为其他值 |
| `operation_base_mode` | holonomic_joint_planning；与 cuRobo 匹配 |
| `trials_per_station` | 5；逐点标定的每站位试验数，复用标定时也需匹配配置 |
| `lastmile_low` | 0.2；S0 原始 trial 实测成功率 ≤ 此值 |
| `lastmile_high` | 0.8；候选 S1 原始 trial 实测成功率 ≥ 此值 |
| `lastmile_nearby_m` | 1.0；S0 与候选 S1 的平面距离上限（米） |
| `start_count` | 3；每场景最多选取的困难 S0 数 |
| `max_s1_candidates` | 5；每 S0 最多尝试的 S1 数，按成功率排序；当前执行代码中 0 也后备为 5，不表示无限 |
| `operation_retries` | 1；导航后操作至多一次外层重试，另受目标状态和故障条件限制 |
| `third_person_enabled` | true；第三人称观察/分析视频 |
| `attempt_timeout_s` | 300 秒；每 episode 执行预算 |
| `operations` | pick / open 类型约定；当前 collection 不按此列表过滤已有场景，子集用 CLI `--samples` |
| `seed` | 20261012；随机种子基础值 |

未填写字段用类默认值，**不继承 execution/curobo_v2.json**。例如本文件默认 `radius_m=2.0`、`spacing_m=null`、width/height 为 320×240。若数据集 geometry 提供同名项，则覆盖相应默认值（如半径、间距、分辨率），详见 [execution](../execution/README.md#数据集参数优先级)。共用参数解释见 [execution](../execution/README.md)。

低/高阈值含等号，且要求 `0 ≤ low < high ≤ 1`。选择使用实际试验计数，不从平滑热图挑点；找不到配对或 A* 无路径是有效负结果，不补造 rollout。
