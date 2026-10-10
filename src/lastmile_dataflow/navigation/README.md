# `navigation/`

- **状态：`astar.py` 已为无编辑采集提供网格 A*、禁止穿角及地面距离场。** 原编辑流水线的第四阶段入口仍未实现。
- 职责：可通行几何、路径搜索和实测闭环到站。
- 预定契约：NavigationRequest → 动作流；不得设置 qpos 或恢复状态。
- 依赖与全流程图见 [完整架构](../../../docs/architecture.md)。

连续底盘执行由 `runtime/no_edit_execution.py` 通过现有 20D 协议完成，不设置执行期 qpos。
导航网格是逐碰撞 geom 的保守底盘高度栅格，真实全身碰撞由执行器逐物理步检查。
