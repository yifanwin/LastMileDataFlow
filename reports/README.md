# 报告组织与当前状态

**当前五命令软件回归可用；lastmile-bench v0.1 已完成既有数据构建及 RBY-1 真实评测，恢复成功能力尚未取得正例。**
运行方式见 [运行手册](../docs/运行手册.md)，不要从历史实施步骤寻找当前命令。

## 验证边界

- benchmark 静态构建图与评测组合图已解耦，按最新要求输出 PNG；已有 RBY-1 两条 rollout 的 head / 第三人称共四个视频单独保留，见[图片交付](benchmark/delivery/phase_02/2026-10-18-png.md)。
- lastmile-bench v0.1 已在独立工作区复用 6 组既有标定完成构建；当前 RBY-1 / Panda 输入分别得到 2 / 1 个 episode，实际导航与区域进入检查通过。RBY-1 Full policy TSR=0/2、Recovery=0/2、coverage=100%，见[实施与构建记录](benchmark/delivery/phase_01/2026-10-18-build.md)。来源标定允许底盘移动，没有重新标定；当前没有 Open 来源数据，该轮尚未接入 Panda VLA；新 π0.5 进度见下方独立报告。

- 第一轮有限真实实验已生成并重载增强场景，执行过 cuRobo V2 逐点规划和连续导航；没有得到成功操作示教。MolmoBot 逐点与单例 bench smoke 也未成功，不能视为困难评测完成。见 [真实实验记录](archives/第一轮精简/2026-10-14-refactor-light-step8-val103-cup30.md)。
- 第二轮在迁移后的源码上完成软件回归与真实快照 CPU stepping；没有重新验收 GPU rollout、模型推理和五命令端到端。见 [第二轮结果](archives/第二轮精简/实施结果.md)。
- 本轮整理与实际验证结果见 [整理报告](REPORT.md)。测试入口与验证类型见 [tests](../tests/README.md)。
- lastmile-datasets 的全组合、两类示教索引和独立物化已完成软件实现，验证见[交付报告](build-lastmile-datasets/delivery/01-implementation.md)；合成数据搬迁读取不代表真实机器人成功采集。

历史事实只描述对应版本，不能作为当前代码已通过真实验收的依据。公开机器人配套资产是否齐全仍是复现限制。

## 目录职责

`reports/` 按主题组织，每个主题内部按文档用途分类。例如：

```text
reports/
├── README.md
├── REPORT.md                      # 保留的最近一次整理结果
├── build-lastmile-datasets/        # 示例主题，按实际需要创建
│   ├── requirement/               # 需求文档
│   ├── plan/                      # 方案、计划文档
│   ├── delivery/                  # 结果、交付文档
│   ├── analyze/                   # 分析文档
│   └── 其他文档.md                 # 暂未明确分类的文档
└── archives/                      # 已有历史归档
```

当前主题：`build-lastmile-datasets/`（[耗时分析](build-lastmile-datasets/analyze/2026-10-20-scene-datasets-and-calibration-cost.md)、[时间优化方案](build-lastmile-datasets/plan/2026-10-22-time-optimization-plan.md)）、`benchmark/`（[lastmile-bench v0.1 需求](benchmark/requirement/01-lastmile-bench-v0.1.md)、[实施方案](benchmark/plan/01-lastmile-bench-v0.1.md)、[困难 S0 benchmark 现状](benchmark/info/01-current-status.md)）、`panda-omron/`。

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
