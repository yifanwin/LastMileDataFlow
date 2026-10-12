# 源码追溯注释

这些文件是当前源码的**仅加注释镜像**，不是运行入口。五个文件均通过 Python AST 一致性检查；原路径、SHA256 和注释对应的原始行号见 [manifest.json](manifest.json)。

正在运行的采集任务冻结了 `src` 摘要。为避免仅加注释也破坏其恢复契约，本次不修改运行源码，不改变物理检查、规划参数或已采集结果。后续修复应建立新的运行身份，不将旧失败重标为成功。

- [运行及候选选择](runtime_no_edit_execution.annotated.py)：C01–C10。
- [cuRobo 配置、warmup 与分段调用](planning_curobo.annotated.py)：P01–P08。
- [抓法来源](tasks_raw_scene.annotated.py)：G01–G02。
- [站位初筛](stations_no_edit_sampling.annotated.py)：S01–S02。
- [失败归因](validation_no_edit.annotated.py)：V01–V02。

生成器：[make_annotated_sources.py](../diagnostics/make_annotated_sources.py)。完整原因及验证边界：[诊断报告](../2026-10-10-ik-collision-code-trace.md)。
