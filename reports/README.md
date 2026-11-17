# 报告

报告记录需求、方案、交付和分析，不作为运行输入；当前命令见 [docs/runbook.md](../docs/runbook.md)。历史报告只描述对应版本，不能作为当前代码已通过真实验收的依据。

## 目录约定

按主题组织，主题目录名用英文小写加连字符；主题内按用途分类，目录按需创建：

- `requirement/`：需求；`plan/`：方案、计划；`delivery/`：结果、交付（含该主题的运行指南）；`analyze/`：分析、现状。
- 尚未明确分类的文档放主题根目录，不另建 `other/`。
- `archives/<主题>/`：已过时的报告（已删除的功能、被取代的方案、旧实验记录），保持原主题内的相对结构，定期整体清理，历史留在 Git 里。主题内不再另建 `archive/`。
- 不在这里堆积视频、日志等运行产物。

## 当前状态

- 精简重构：第一轮见[最终总结](simplify-refactor/delivery/10-final-summary.md)（回归未全绿，RGB 严格一致性未通过）；第二轮及按 pipeline 的目录分层见[第二轮总结](simplify-refactor/delivery/12-second-pass-summary.md)，迁移后只做了最小规模的可运行验证；配置、脚本和报告的整理见[第 13 号记录](simplify-refactor/delivery/13-configs-scripts-reports.md)。
- RL 站位图：旧协议仍在研究，学习策略尚无成功；独立的[方块双指接触单站位实验](rl-station-map/delivery/19-cube-contact-single-station.md)正在验证，不能把 DLS 物理接触当作策略成功；算法与训练主链见[算法与训练 pipeline](rl-station-map/delivery/algorithm-training-pipeline.md)，运行见[运行指南](rl-station-map/delivery/running.md)，接手见[训练接手与结果汇总](rl-station-map/delivery/05-training-handover.md)。
- lastmile-bench v0.1：已用既有标定完成构建，RBY-1 真实评测 TSR=0/2、Recovery=0/2，恢复能力尚无正例；见[现状](benchmark/analyze/01-current-status.md)。
- cuRobo方块基线：[S0246固定10次pick试验](rl-station-map/delivery/22-curobo-cube-pick-10-trials.md)，完整pick成功0/10，均在闭爪后的抬升规划阶段失败。 [关闭cuRobo碰撞对照](rl-station-map/delivery/23-curobo-cube-pick-collision-off-10-trials.md)成功10/10，保留MuJoCo物理碰撞。
- 公开机器人配套资产是否齐全仍是复现限制。

- RL 输入尺度：[S0246 65维归一化检查](rl-station-map/delivery/24-cube-input-normalization-audit.md)，重新采样16万条输入，逐维统计与数值检查。
- RL 方块归一化初版：[误差归一化与奖励调整](rl-station-map/delivery/25-cube-normalization-rewards.md)，2 cm / 20°归一化、碰撞−1、关闭张开奖励、增加抬升进度差奖励，预算300万条健康transition。
- RL 方块上一轮训练：[归一化与新奖励300万条启动记录](rl-station-map/delivery/26-cube-normalized-3m-training-launch.md)，已按用户指令停止，独立run保留实际采样、更新与监控记录。
- RL 方块上一版奖励与训练：[阶段接触事件、平滑闭爪、batch512](rl-station-map/delivery/27-cube-stage-contact-b512-training.md)，停止上一轮后在CUDA0–3从头训练300万条健康样本，旧run完整保留。
- RL 方块300万训练结果：[阶段成功率与奖励分解](rl-station-map/delivery/28-cube-stage-contact-b512-results.md)，完整完成23,281次更新；中期成功约95%–96%，后期回报被重复接触与闭爪奖励主导，最后100个训练回合成功0次，尚无独立checkpoint评估。

- RL 方块当前成功与奖励口径：[连续保持0.25秒与历史最高抬升奖励](rl-station-map/delivery/33-cube-hold-quarter-second-reward.md)，双指各>0.5 N、最低点距桌面≥1 cm、无支撑接触连续保持0.25 s；位置/旋转/接触/抬升权重为20/5/3/8。本轮仅修改与离线验证，上一轮[训练记录](rl-station-map/delivery/32-cube-clearance-training-launch.md)保留原判定。

- RL checkpoint：best 与 TensorBoard 默认30秒窗口的成功率同步比较，每40万健康transition在 `ckpts/` 独立保留完整权重；见[实现与验证](rl-station-map/delivery/35-tensorboard-best-periodic-checkpoints.md)。已有训练进程继续使用启动时逻辑。

- RL PPO：[SAC/PPO 开关与同步多卡接入](rl-station-map/delivery/36-ppo-integration.md)，flat/graph、全局优势归一化、Actor/V独立裁剪、KL停止、V评分，已完成单/双卡真实短验证，未做正式训练。

- PPO正式训练：[CUDA0–3 cube 300万样本启动配置](rl-station-map/delivery/37-ppo-cube-g0123-training-launch.md)，实时进度与启动验收记录在对应run。

## 主题

| 主题 | 内容 | 入口 |
|---|---|---|
| `simplify-refactor/` | 两轮精简重构的方案、进度与总结 | [第二轮方案](simplify-refactor/plan/02-further-simplify-plan.md)、[第二轮总结](simplify-refactor/delivery/12-second-pass-summary.md) |
| `project-overview/` | pipeline 总图与站位热图示意 | [pipeline 图](project-overview/delivery/2026-11-02-pipeline-figure.md) |
| `rl-station-map/` | 固定底盘 SAC / PPO 站位图（mjlab 独立环境） | [需求](rl-station-map/requirement/01-fixed-base-sac.md)、[构建方案](rl-station-map/plan/01-implementation.md)、[分布式采样方案](rl-station-map/plan/03-distributed-sampling-centralized-learner.md) |
| `benchmark/` | lastmile-bench：困难 S0、恢复区域与评测 | [需求](benchmark/requirement/01-lastmile-bench-v0.1.md)、[方案](benchmark/plan/01-lastmile-bench-v0.1.md)、[现状](benchmark/analyze/01-current-status.md) |
| `curobo-time-optimization/` | PandaOmron 场景构建与 cuRobo 标定加速（GPU 规划/CPU 执行拆分） | [当前需求](curobo-time-optimization/requirement/02-panda-omron-time-optimization.md)、[现状](curobo-time-optimization/analyze/01-current-status.md)、[规划失败分析](curobo-time-optimization/analyze/2026-10-25-planning-failure-analysis.md) |
| `collision-handling/` | 局部碰撞表示：机器人柱体/底盘球、结构化局部环境 | [需求](collision-handling/requirement/02-local-collision-representation.md)、[方案](collision-handling/plan/04-local-collision-representation.md) |
| `navigation/` | A* 导航几何修复与软净空 | [专题入口](navigation/README.md)、[软净空实施结果](navigation/delivery/08-navigation-soft-clearance.md) |
| `panda-omron/` | PandaOmron 接入、全流程与整机缩放 | [全流程需求](panda-omron/requirement/02-panda-omron-full-flow.md)、[整机缩放 0.9](panda-omron/delivery/20261111-scale-09.md) |
| `pi05/` | π0.5 双本体接入与运行 | [需求](pi05/requirement/01-pi05-integration.md)、[构建报告](pi05/delivery/03-implementation.md)、[运行指南](pi05/delivery/running.md)、[val103 多站位推理](pi05/delivery/06-val103-cup30-inference.md) |
| `raw-assets/` | THOR 资产库、DROID 抓取库、ProcTHOR 场景库的内容分析 | [analyze/](raw-assets/analyze/) |

## 归档

| 位置 | 内容 | 归档原因 |
|---|---|---|
| `archives/task-modes/` | 八类任务模式构造的需求、方案与交付 | 任务模式功能已删除 |
| `archives/info/` | build-scenes → 标定的 seed 汇总（2026-10-25） | 针对旧命令和旧链路的一次性盘点 |
| `archives/benchmark/docs/` | lastmile-bench v0.1 旧运行指南 | 旧命令和 JSON 配置，已由 docs/runbook.md 取代 |
| `archives/panda-omron/docs/` | PandaOmron phase01 worktree 的运行指南 | 指向已不存在的 worktree 和旧配置 |
| `archives/collision-handling/` | 标定修复诊断方案与 N0–N5 阶段交付 | 原 `collision-handing/archive/`，已被局部碰撞方案接续 |
| `archives/curobo-time-optimization/2026-10-24/` | 加速专题的旧方案、探针与耗时分析 | 原 `curobo-time-optimization/archive/`，已被当前方案接续 |
| `archives/no-edit-calibration/` | 无编辑场景标定实验（10-27）、低成功率归因、Oracle 边界分析与未实施的调整清单 | 旧版本链路上的实验记录，用户 11-12 定为归档 |
| `archives/build-lastmile-datasets/` | 连续采集数据集的需求与首轮实现（引用型结果＋独立物化） | 独立物化入口已删除，采集现在直接导出自包含数据集；用户 11-12 定为归档 |

- RL 方块阶段峰值与成功率选模：[奖励调整及四卡训练](rl-station-map/delivery/29-cube-stage-peak-success-best-training.md)，已完成300万经验/23,281次更新；末50万训练成功率96.80%、末100回合98/100，但末100回合无碰撞成功0/100；best锁定于早期1/1窗口，尚无独立checkpoint评估。

- RL 方块当前训练：[CUDA2–3连续保持0.25秒训练](rl-station-map/delivery/34-cube-hold-g23-training-launch.md)，从头300万健康经验、各128环境、batch512，实际采样与学习更新已核查；此前四卡训练已停止。
