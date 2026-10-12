# 42样本续构造：启动报告

**已后台启动，尚未完成或封存整个批次。** 当前目标42样本，GPU3/1两个单元并行；首次调度为 val103 / Cup_30 的 Case1、Case2。

## 修改与验证

- 两场景配置的 max_pick_targets_per_house / max_open_targets_per_house 改为-1，不设最低配额或数量截断；正整数配额兼容保留。
- 不限时所有合格目标参与，空间分散只排序，不因近距离删除；空house不会在资格检查中异常退出。
- expected_samples 对不限字段使用实际资格检查目标数，不乘负数。4 pick×2 case×5变体 + 2 open =42。
- 全部语义、支撑、Chair、大/高障碍物、物理与可见性门槛不变。
- [目标/配额回归18项通过](../../../outputs/logs/scene-unlimited-targets-tests-20261012.log)，[容量回归16项通过](../../../outputs/logs/scene-unlimited-surface-regression-20261012.log)，共34项；不是全仓或真实批次完成声明。

## 资格证据复用范围

旧资格检查使用3/1配额，当前按用户要求复用其合格的4 pick、2 open；不是新不限模式全部目标已穷举的声明。派生文件只更新两个数量字段，核对其它配方字段未变、源recipe摘要、每个选中任务实际资格与Chair候选证据，保留来源文件SHA256。旧资格JSON未改写。

[派生输入](../../../outputs/diagnostics/scene-twohouse-v3-qualified42-unlimited-20261012.json)；批次范围明确为 `previously_qualified_subset_user_requested_42_not_exhaustive`。已完成发现/资格检查的耗时保留，新构造不重跑前两阶段。

## 运行与交付

- [后台状态/时间](../../../outputs/diagnostics/scene-twohouse-v3-qualified42-20261012-215947-orchestration/status.json)
- [构建日志](../../../outputs/logs/scene-twohouse-v3-qualified42-20261012-215947-build.log)
- [构建进度](../../../outputs/scene_datasets/scene-twohouse-v3-qualified42-20261012-215947/progress.json)
- [数据目录](../../../outputs/scene_datasets/scene-twohouse-v3-qualified42-20261012-215947/)

2026-10-12 21:59:48（Asia/Shanghai）启动；预计42不是保证42，物理搜索预算可能导致partial，完整交付须 manifest sealed及最终物理审计通过。目录交付带README，不压缩、不运行策略。
本次未改变用户已有 cli.py、collection 配置等其它改动，未创建分支或提交。
