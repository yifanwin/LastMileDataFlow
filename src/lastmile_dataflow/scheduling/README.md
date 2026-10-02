# `scheduling/`

- **状态：规划目录，尚未实现。** 所属：第五阶段。
- 职责：任务队列、独立 attempt 调度、断点续跑和成本预算。
- 预定契约：CollectionJob → AttemptRef[]；配置匹配才允许续跑。
- 依赖与全流程图见 [完整架构](../../../docs/architecture.md)。

第一阶段不提供假实现，也不把该目录视为可用能力。
