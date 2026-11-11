# 采集导出与 benchmark 精简增量

- 状态：done（本轮增量；完整阶段 3–6 尚未完成）
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 改动与结果

- 删除 benchmark 的 mode_run、多标签诊断选起点、按标签筛恢复锚点和私有标签输出，只保留实测成功率选择困难起点和参考站位；恢复区域、导航筛选和策略评测保留。
- BenchmarkConfig 并入 benchmark.py，删除旧配置模块，更新全部调用。
- 删除 materialize_lastmile 和独立引用格式分支；采集收尾调用 finalize_dataset，将唯一成功示教和必需观测导出到当前 run 的 rollouts/dXXXXXX，训练索引和成功配对仅通过内部 rollout_path 定位，不再输出外部 source_run/source.run。来源轨迹去重、边界检查、batch 读取保留。
- π0.5 读取自包含数据集；从标定记录准备示教时直接执行同一导出流程。
- 删除 fixed_pick.py 固定抓取后端和 smoke 的 pick 分支，不删除本体规划器的固定底盘支持。
- 删除两项已删除模式 benchmark 测试，适配已有导出和 Panda batch 测试；同步当前数据说明。

## 验证

MUJOCO_GL=disable、PYTHONPATH=src，主仓库环境运行 test_lastmile_dataset、test_panda_recording、test_benchmark、test_benchmark_progress、test_recovery、test_mode_downstream，共 30 项通过；git diff --check 通过。

覆盖轨迹边界、重复来源、缺失观测、Panda 布局、搬迁后禁止访问来源目录的读取，以及 benchmark 选择和恢复区域。未运行 GPU 或真实采集。

## 未完成与限制

阶段 3 场景构建替换、阶段 4 标定拆分及诊断/规划器精简、执行配置删除和整体文档收尾尚未完成。旧模式诊断与选择代码仍被这两条旧流程调用，不能宣称完整阶段 6 已结束。阶段 5 的自包含导出已接入，但主 workflow 尚未按计划拆成 collect.py；当前保留原始 attempt 和训练导出副本，未删除实际采集文件。RL 专属逻辑未修改。
