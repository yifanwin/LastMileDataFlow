# 导航专题

- 状态：active
- 日期：2026-10-26
- 替代：原 `reports/task-modes/` 下独立导航文档的存放位置
- 关联：[当前需求](requirement/02-navigation-soft-clearance.md)、[修改方案](plan/04-navigation-soft-clearance.md)

当前实现为 `soft_clearance_v1`：20 mm 只作为净空软目标，基准权重 2.0、严重穿透判据 5 mm。实际到达且无严重碰撞、记录完整才报导航通过。Cup/Open 新构建均为 7/7，Train 11/13 对找到路线，五条物理导航通过；剩余两对为预算耗尽。完整结果见[软净空交付](delivery/08-navigation-soft-clearance.md)，不与上一轮硬门槛版本混算。

| 分类 | 文档 | 状态 |
|---|---|---|
| requirement | [软净空需求](requirement/02-navigation-soft-clearance.md) | active |
| plan | [软净空修改方案](plan/04-navigation-soft-clearance.md) | done；已实施并保存实际参数 |
| plan | [上一轮几何修复方案](plan/03-navigation-geometry-fix.md) | done；硬余量要求已被新需求替代 |
| delivery | [Cup 导航原因复核](delivery/06-cup-navigation-review.md) | done；静态复核证据 |
| delivery | [上一轮几何修复交付](delivery/07-navigation-geometry-fix.md) | done；原硬门槛版本的构建与四条物理导航 |
| delivery | [软净空实施交付](delivery/08-navigation-soft-clearance.md) | done；当前版本、374 项回归和五条物理导航 |

文档已从 `reports/task-modes/` 迁入，保留原文件名与历史实验数字。任务模式的综合交付和运行清单仍在 [task-modes](../task-modes/delivery/05-run-inventory.md)，运行产物仍在 `outputs/diagnostics/`；本轮不移动资产或实验数据。`reports/archives/` 中旧 A* 实验继续作为历史资料保留。
