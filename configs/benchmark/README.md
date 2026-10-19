# Benchmark 配置

状态：active
日期：2026-10-18
替代：无
关联：[需求](../../reports/benchmark/requirement/01-lastmile-bench-v0.1.md)、[运行指南](../../reports/benchmark/docs/运行指南.md)

`build-bench --config configs/benchmark/v0.1.json`。构建直接读取已有场景和标定，不启动新的 trial。

| 字段 | 默认值及作用 |
|---|---|
| `low` / `high` | 0.20 / 0.60，含等号；从初始站位启动的参考协议成功率 |
| `region_radius_m` | 0.10 m，沿自由空间扩张高率站位 |
| `region_yaw_tolerance_rad` | 0.261799… rad，允许朝向差 15° |
| `map_resolution_m` | 0.05 m，导航与区域的离散分辨率 |
| `navigation_margin_m` | 0.02 m，机器人包络额外余量 |
| `navigation_whole_body` | true，使用导航保持姿态的整机扫掠圆包络；false 仅底盘，须作为不同导航设置报告 |
| `local_candidate_radius_m` | null，Recovery Ratio 使用整个目标候选域；正数表示 S0 附近半径 |
| `max_episodes_per_scene` | 0 表示保留全部合格 S0；正数显式随机抽样 |
| `seed` | 42，抽样与 episode seed |
| `effective_translation_m` / `effective_yaw_rad` | 0.02 m / 0.0349066 rad，恢复诊断的有效调整阈值 |
| `horizon` / `wall_timeout_s` | 400 策略步 / 600 s，评测预算 |

导航地图范围沿用来源标定的 `radius_m`。标定次数、底盘活动范围、规划与重试预算读取来源 run，不由本配置反向覆盖。修改参数使用新输出目录。
