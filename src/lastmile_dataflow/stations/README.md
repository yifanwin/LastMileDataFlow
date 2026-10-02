# `stations/`

第三阶段已实现：严格 v3 配置、分层站位/真实地面与碰撞过滤、预算、固定底盘 cuRobo 执行。
`execution.py` 只通过 `runtime.Simulation` 发生真实运动，开始后不重置。
规划、物理成败、基础设施异常和未知分别记录。浏览导出在 `exporting/`，case/物理审计在 `validation/`。

[使用与限制](../../../docs/phase3.md) · [整体架构](../../../docs/architecture.md)。
