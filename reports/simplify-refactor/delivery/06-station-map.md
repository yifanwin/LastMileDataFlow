# 阶段 4 标定、诊断与执行配置精简

- 状态：active
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 实际改动

- 删除多标签 diagnosis/diagnosis_inputs、场景 mode_config 和对应配置、模式率表与选择函数。
- 删除 diagnose_stations、diagnostic_ik、IK 探测证据及其专用测试，不再消耗诊断预算。
- 删除 StationPlannerCache、重绑冻结初态方法和 reuse_planners 开关及所有内部参数。规划池和 CPU 代理保留，池内 PlannerResources 常驻求解器仍用于批量规划，不删除这一架构。
- 删除只供 IK 诊断的 ik_only 初始化路径、旧 initial_model/initial_state 缓存。
- ExecutionConfig 删除 mode_config，当前 loader 不再读取模式配置或强制三次试验；实际 trial 数仍由 trials_per_station 显式控制。
- 删除旧 workflow 文件，拆成 collect.py 与 station_map.py；RunResources 和记录模式归入 station_map.py，删除 runtime/resources.py。
- 将标定调度闭包改为顶层 _CalibrationScheduler；输入由 CalibrationInputs dataclass 显式传递，完成准备、记录、查询、规划、执行、派发及关闭均为独立方法。
- 删除 evaluate_recipe、prepare_scene_inputs 及房屋隔离输入构建的无人调用实现。正常场景构建的房屋多进程、超时、故障记录保留。
- 标定入口只接受 cuRobo；VLA 继续由 benchmark 使用。

## 验证

58 项相关局部测试通过：标定/采集编排、实际 spawn 调度、失败隔离、场景编排、入口、规划池、规划 seed 与证据。模拟执行测试只覆盖软件行为，不代表真实抓取成功。静态未定义名称检查及 git diff --check 通过；没有运行 GPU 对照。

调度测试不生成轨迹，单独 mock 自包含文件导出；真实文件边界、Panda 布局和搬迁读取由上一轮的导出测试覆盖。没有通过放宽生产路径来容忍缺失轨迹。

## 剩余工作

仍需完成记录器 physics/depth 删除、资产加载合并、其余 runtime 合并和结构整理、过时配置/文档清理，以及完整回归和必要真实最小构建验证。阶段 4 的真实成功率、耗时与热图对照尚未执行；本报告不将其记为验收通过。完整目标继续 active。
