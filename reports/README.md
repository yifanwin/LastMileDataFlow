# 报告组织与当前状态

**当前五命令软件回归可用；lastmile-bench v0.1 已完成既有数据构建及 RBY-1 真实评测，恢复成功能力尚未取得正例。**
运行方式见 [运行手册](../docs/运行手册.md)，不要从历史实施步骤寻找当前命令。

## 验证边界

- [PyRUA 原生策略接入项目场景与 benchmark](pyrua/plan/01-mobile-policy-integration.md)：在 `exp/pyrua-native` 工作区直接接入本项目场景、S0 和评测，保留 PyRUA Agent 与原生原语算法，适配环境观测、动作和任务反馈；不安排外部 RoboCasa 任务复现。已完成方案修订，尚未实现或运行真实试验。

- [PandaOmron 标定修复与排查方案](no-edit-calibration/plan/03-calibration-repair-and-diagnosis.md)：按任务绑定修复、碰撞几何对照、残余执行问题和小批次复核推进；采样参数调整不在范围内。当前仅完成方案，尚未实施修复。

- benchmark 静态构建图与评测组合图已解耦，按最新要求输出 PNG；已有 RBY-1 两条 rollout 的 head / 第三人称共四个视频单独保留，见[图片交付](benchmark/delivery/phase_02/2026-10-18-png.md)。
- lastmile-bench v0.1 已在独立工作区复用 6 组既有标定完成构建；当前 RBY-1 / Panda 输入分别得到 2 / 1 个 episode，实际导航与区域进入检查通过。RBY-1 Full policy TSR=0/2、Recovery=0/2、coverage=100%，见[实施与构建记录](benchmark/delivery/phase_01/2026-10-18-build.md)。来源标定允许底盘移动，没有重新标定；当前没有 Open 来源数据，该轮尚未接入 Panda VLA；新 π0.5 进度见下方独立报告。

- 第一轮有限真实实验已生成并重载增强场景，执行过 cuRobo V2 逐点规划和连续导航；没有得到成功操作示教。MolmoBot 逐点与单例 bench smoke 也未成功，不能视为困难评测完成。见 [真实实验记录](archives/轻量化/第一轮精简/2026-10-14-refactor-light-step8-val103-cup30.md)。
- 第二轮在迁移后的源码上完成软件回归与真实快照 CPU stepping；没有重新验收 GPU rollout、模型推理和五命令端到端。见 [第二轮结果](archives/轻量化/第二轮精简/实施结果.md)。
- 本次 PandaOmron 文档整理见[交付报告](curobo-time-optimization/delivery/2026-10-24-document-consolidation.md)；该轮未运行新实验。GPU 规划/CPU 执行拆分现已实现，设计见[并行专项](curobo-time-optimization/plan/02-parallel-execution.md)，首阶段实测见下条交付。测试入口与验证类型见 [tests](../tests/README.md)。
- lastmile-datasets 的全组合、两类示教索引和独立物化已完成软件实现，验证见[交付报告](build-lastmile-datasets/delivery/01-implementation.md)；合成数据搬迁读取不代表真实机器人成功采集。
- 构建与 cuRobo 加速资料统一放在 `curobo-time-optimization/`，主线为 **PandaOmron、每站位 3 次、先完成流程加速再统一全量实验**（[当前需求](curobo-time-optimization/requirement/02-panda-omron-time-optimization.md)）。Panda 配置已为 3 次；CUDA Graph 不纳入实现，数值标定关闭在线视觉记录。双池、共享准备和 solver 复用已完成软件检查与首阶段实测。见[当前状况](curobo-time-optimization/info/01-current-status.md)及[最新方案](curobo-time-optimization/plan/01-panda-omron-time-optimization.md)。旧报告随主题迁移归档，首阶段真实双 GPU、20×3 全量标定已完成（225.458 s、20/60 成功），成功/失败回放视频解码通过；结论限于 val103/Cup_30/Case2。百来源全 Case 正式批次已完成：183 accepted/37 rejected，计划 8,466、记录 8,463（另 1 站准备失败），820 成功、284 执行失败、7,359 执行前无解，标定 2.88 h；6 段实际轨迹视频通过。Case3 无物理执行证据，复现命令见[首阶段交付](curobo-time-optimization/delivery/2026-10-24-stage1-experiment.md)及[最终批次交付](curobo-time-optimization/delivery/2026-10-24-val100-experiment.md)；分 Case 的失败原因分布见[规划失败分析](curobo-time-optimization/analyze/2026-10-25-planning-failure-analysis.md)。

历史事实只描述对应版本，不能作为当前代码已通过真实验收的依据。公开机器人配套资产是否齐全仍是复现限制。

## 目录职责

`reports/` 按主题组织，每个主题内部按文档用途分类。例如：

```text
reports/
├── README.md
├── curobo-time-optimization/       # 场景构建与 cuRobo 标定加速
│   ├── requirement/               # 需求文档
│   ├── info/                      # 当前状况
│   ├── plan/                      # 方案、计划文档
│   ├── delivery/                  # 结果、交付文档
│   └── archive/                   # 旧分析、方案与实验记录
├── build-lastmile-datasets/        # 数据集生成与物化需求、交付
└── archives/                      # 已有历史归档
```

当前主题：`curobo-time-optimization/`（[当前状况](curobo-time-optimization/info/01-current-status.md)、[PandaOmron 加速方案](curobo-time-optimization/plan/01-panda-omron-time-optimization.md)、[整理与迁移结果](curobo-time-optimization/delivery/2026-10-24-document-consolidation.md)）、`build-lastmile-datasets/`（[数据集生成需求](build-lastmile-datasets/requirement/01-lastmile-datasets.md)、[实现交付](build-lastmile-datasets/delivery/01-implementation.md)）、`benchmark/`（[lastmile-bench v0.1 需求](benchmark/requirement/01-lastmile-bench-v0.1.md)、[实施方案](benchmark/plan/01-lastmile-bench-v0.1.md)、[困难 S0 benchmark 现状](benchmark/info/01-current-status.md)）、`panda-omron/`。加速主题的 `info/` 保存当前状况，`archive/` 保留被新方案接续的历史材料。

- `task-modes/`：[需求](task-modes/requirement/01-task-mode-construction.md)、[实施方案](task-modes/plan/01-task-mode-implementation.md)、[计划表](task-modes/plan/02-build-schedule.md)、[build-scenes 八模式构造说明](task-modes/info/01-build-scenes-modes.md)。用户最新要求本轮止于标定，P0–P4 已交付，后续采集/导出/benchmark 暂不推进。

  [标定交付](task-modes/delivery/02-calibration-delivery.md)：修复后的四批完整标定共 222 条 trial、63 条真实任务成功；六类有实际标签正例，Orientation/Visibility 尚无正例。最新回归 355 项：353 通过、2 跳过。旧 Case 链路退出；Blackwell 环境及 RoboCasa 闭爪底盘保持问题已修复，历史结果保留，见[修复记录](task-modes/delivery/04-mobile-base-hold-fix.md)。全部 60 个 run 的分类、数字与待复核项见[运行产物总览](task-modes/delivery/05-run-inventory.md)；38 个 accepted 构建场景的渲染图见[候选构建场景预览](task-modes/delivery/build-scenes-previews.md)。[Cup 导航复核](navigation/delivery/06-cup-navigation-review.md)确认保守栅格误拒绝；后续[导航修复交付](navigation/delivery/07-navigation-geometry-fix.md)完成源码修复，新 Cup/Open benchmark 为 2/7、7/7，Train 9/13 对找到参考路线，四条实际导航通过；专项回归 367 项，365 通过、2 跳过。没有新增完整连续操作示教或策略任务成功结论。

- `navigation/`：[导航专题入口](navigation/README.md)、[软净空需求](navigation/requirement/02-navigation-soft-clearance.md)、[修改方案](navigation/plan/04-navigation-soft-clearance.md)、[实施结果](navigation/delivery/08-navigation-soft-clearance.md)。已取消 20 mm 硬门槛，基准为 20 mm 软目标、权重 2.0、独立 5 mm 穿透判据；Cup/Open 各 7/7、Train 11/13 对有路线，五条实际物理导航通过。最终软件回归 374 项，372 通过、2 跳过；没有新增完整连续操作示教或策略任务成功结论。

- `no-edit-calibration/`：[需求](no-edit-calibration/requirement/01-requirement.md)、[计划](no-edit-calibration/plan/01-experiment-plan.md)、[实时状态](no-edit-calibration/delivery/status.md)、[成功率归因](no-edit-calibration/analyze/01-low-success-analysis.md)、[全量 trial 统计](no-edit-calibration/analyze/02-all-runs-trial-statistics.md)、[planner 复用缺陷](no-edit-calibration/analyze/03-planner-reuse-target-binding.md)。2026-10-27 16:20 快照覆盖 14 个 run / 58,441 trial；主 cohort 成功率 2.88%，最大失败池是 goalset 的底座-场景碰撞，pick 执行损失与 planner 复用错绑强相关。

- `pi05/`：[双本体接入需求](pi05/requirement/01-pi05-integration.md)（active）、[Panda 控制对齐评估](pi05/info/01-panda-robocasa-alignment.md)（需求阶段源码评估）、[本轮梳理结果](pi05/delivery/01-requirements-review.md)。

  [cuRobo 与 π0.5 共用控制链路](pi05/info/01-panda-shared-control.md)进一步说明动作汇合、轨迹跟踪、控制时序及待验证条件；尚未切换默认 profile。

  [双本体构建方案](pi05/plan/01-pi05-integration-build.md)规定环境、推理核心、本体适配、工作流和 P0–P6 实施验收；[方案编写总结](pi05/delivery/02-build-plan.md)记录方案阶段的文档交付。当前实现、真实验证与边界见[构建报告](pi05/delivery/03-implementation.md)，命令见[π0.5 运行指南](pi05/docs/01-running.md)。

  首轮构建完成：两权重推理与两本体短 rollout 已运行；Panda 路线 B 固定抓取和 A* 绕障通过，π0.5 短 rollout 均未抓取成功。RBY-1 48 条示教 / 13,167 行已完成 LeRobot 转换和本体统计；尚未微调。最终软件回归共 237 项：236 项通过、1 项跳过。

- 新文档先确定所属主题，再放入对应分类目录；分类目录按需创建。
- 尚未明确分类的文档先放在所属主题根目录，例如 `reports/build-lastmile-datasets/`，不另建 `other/`。
- 已有 `REPORT.md` 和 [archives](archives/README.md) 暂时保留，不因本次分类规则批量搬迁历史文档。
- 报告不作为运行输入；不新增报告附件索引，不在这里堆积视频、日志等运行产物。

- [Panda π0.5 benchmark 首轮运行](pi05/delivery/04-panda-benchmark.md)：重建 RoboCasa profile 并完成 3 站位 × 2 标定，0/6 成功、无高率恢复站位；benchmark 为 0 episode，策略评测入口停止，未加载模型。

- [双本体全量标定需求纠正](pi05/requirement/02-embodiment-full-calibration.md)：Panda-Omron / RBY-1 分别配置固定肩枢轴至 TCP 工作臂展 L 和底盘半径 B；按 RBY 原规则整理为半径 L、间距 B/4，可见性只记录；全部候选物理检查后，均匀保留最多 20 点完整标定。上轮 3 点结果仅为局部结果，新标定已启动。

- 两本体站位配置已改为各自固定臂展与底盘半径四分之一间距；全部候选完成物理检查后，每场景均匀保留最多 20 点，可见性只记录。新 cuRobo 标定启动与命令见[执行记录](pi05/delivery/05-full-calibration-start.md)。
