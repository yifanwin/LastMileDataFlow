# Benchmark 配置

- 状态：active
- 日期：2026-10-26
- 替代：无
- 关联：[需求](../../reports/benchmark/requirement/01-lastmile-bench-v0.1.md)、[运行指南](../../reports/benchmark/docs/运行指南.md)、[导航修复](../../reports/navigation/delivery/07-navigation-geometry-fix.md)

`build-bench --config configs/benchmark/v0.1.json`。构建直接读取已有场景和标定，不启动新的 trial。

| 字段 | 默认值及作用 |
|---|---|
| `low` / `high` | 0.20 / 0.60，含等号；从初始站位启动的参考协议成功率 |
| `region_radius_m` | 0.10 m，高率站位周围的合法局部位姿区域半径 |
| `region_yaw_tolerance_rad` | 0.261799… rad，允许朝向差 15° |
| `map_resolution_m` | 0.05 m，导航与区域的离散分辨率 |
| `navigation_margin_m` | 0.02 m，期望净空，仅用于有限的软代价，不作为通行门槛；0 关闭净空惩罚 |
| `navigation_clearance_weight` | 2.0，低净空惩罚权重；0 按基础平移/转向代价搜索 |
| `navigation_max_penetration_m` | 0.005 m，导航环境/自碰撞的严重穿透阈值；普通轮/底盘—地面接触单独处理 |
| `navigation_whole_body` | true，粗栅格显示整机扫掠圆；false 显示底盘包络。实际导航统一检查整机 XY/yaw，粗栅格不作硬性通行门槛 |
| `local_candidate_radius_m` | null，Recovery Ratio 使用整个目标候选域；正数表示 S0 附近半径 |
| `max_episodes_per_scene` | 0 表示保留全部合格 S0；正数显式随机抽样 |
| `seed` | 42，抽样与 episode seed |
| `effective_translation_m` / `effective_yaw_rad` | 0.02 m / 0.0349066 rad，恢复诊断的有效调整阈值 |
| `horizon` / `wall_timeout_s` | 400 策略步 / 600 s，评测预算 |

导航使用完整来源房间地面，允许绕出采样圆；`radius_m` 不裁剪房间导航。站位连接和按需生成的房间 XY/yaw 状态使用同一几何检查与软代价比较，低于 20 mm 仍可通行。恢复区域使用同一可行性判断，并保留最近实测站位裁剪。新 run 记录 `navigation_protocol=soft_clearance_v1`；标定来源与当前导航设置分开保存，改变软目标和权重不重新标定。修改参数使用新输出目录。
