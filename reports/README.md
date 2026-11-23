# 报告

报告记录需求、方案、交付和分析，不作为运行输入；当前命令见 [docs/runbook.md](../docs/runbook.md)。历史报告只描述对应版本，不能作为当前代码已通过真实验收的依据。

## 目录约定

按主题组织，主题目录名用英文小写加连字符；主题内按用途分类，目录按需创建：

- `requirement/`：需求；`plan/`：方案、计划；`delivery/`：结果、交付（含该主题的运行指南）；`analyze/`：分析、现状。
- 尚未明确分类的文档放主题根目录，不另建 `other/`。
- `archives/<主题>/`：已过时的报告（已删除的功能、被取代的方案、旧实验记录），保持原主题内的相对结构，定期整体清理，历史留在 Git 里。主题内不再另建 `archive/`。
- 不在这里堆积视频、日志等运行产物。

## 当前状态

- Cup_30真实物体RL：[随机站位接触抬升训练](rl-station-map/delivery/cup30-random-stations-training.md)，沿用方块协议、69D输入和预处理128→16候选；真实验证及正式训练状态见报告。

- Cup_30移动底盘RL：[合并与奖励统一](rl-station-map/delivery/50-cup30-mobile-base-setup.md)、[8M诊断](rl-station-map/delivery/51-cup30-mobile-8m-diagnosis.md)、[熵奖励根因](rl-station-map/delivery/52-cup30-mobile-budget30-e512-results.md)、[当前训练](rl-station-map/delivery/53-cup30-mobile-noenttarget.md)；工作区 .worktrees/rl-mobile。

- 抓取候选：[全量 FPS 与 Cup_30 16位姿图](rl-station-map/delivery/full-corpus-grasp-fps.md)，cuRobo/RL 共用固定种子的全量筛选；真实数据三处一致，未执行轨迹规划或训练。

- 精简重构：第一轮见[最终总结](simplify-refactor/delivery/10-final-summary.md)（回归未全绿，RGB 严格一致性未通过）；第二轮及按 pipeline 的目录分层见[第二轮总结](simplify-refactor/delivery/12-second-pass-summary.md)，迁移后只做了最小规模的可运行验证；配置、脚本和报告的整理见[第 13 号记录](simplify-refactor/delivery/13-configs-scripts-reports.md)。
- RL 站位图：旧协议仍在研究，学习策略尚无成功；独立的[方块双指接触单站位实验](rl-station-map/delivery/19-cube-contact-single-station.md)正在验证，不能把 DLS 物理接触当作策略成功；算法与训练主链见[算法与训练 pipeline](rl-station-map/delivery/algorithm-training-pipeline.md)，运行见[运行指南](rl-station-map/delivery/running.md)，接手见[训练接手与结果汇总](rl-station-map/delivery/05-training-handover.md)。
- lastmile-bench v0.1：已用既有标定完成构建，RBY-1 真实评测 TSR=0/2、Recovery=0/2，恢复能力尚无正例；见[现状](benchmark/analyze/01-current-status.md)。
- cuRobo方块基线：[S0246固定10次pick试验](rl-station-map/delivery/22-curobo-cube-pick-10-trials.md)，完整pick成功0/10，均在闭爪后的抬升规划阶段失败。 [关闭cuRobo碰撞对照](rl-station-map/delivery/23-curobo-cube-pick-collision-off-10-trials.md)成功10/10，保留MuJoCo物理碰撞。

- RoboCasa365 原生场景：三个厨房的 pick 子任务已导入，统一运行接口通过真实验证；pick 判定只有单元测试，见[交付记录](robocasa/delivery/01-native-pick.md)。π0.5 在三个厨房 120 个 RL 网格站位 × 3 seed 上抓取成功 32/360（8.9%，20 s 预算）、175/360（48.6%，50 s 预算），见[抓取实验](robocasa/delivery/02-pi05-pick.md)。
- RoboCasa 固定底盘站位实验见[框架梳理](robocasa/plan/02-fixed-base-station-evaluation.md)；原生物体和自定义长方体的放置、成组对照与人工选集见[场景搭建方案](robocasa/plan/03-pick-scene-construction.md)。
- 固定底盘首版选用同一把水壶远置与杯左 125 mm，PPO 基于 station-quality。合并预检、双评测器、基线和配对验收见[首对实验构建计划](robocasa/plan/04-paired-station-experiment.md)。
- RoboCasa 台面取杯的水壶和长方体首批 14 个变体已完成物理、重载和站位视野检查，见[候选与人工选择清单](robocasa/delivery/03-pick-scene-selection.md)。
- 公开机器人配套资产是否齐全仍是复现限制。

- RL 输入尺度：[S0246 65维归一化检查](rl-station-map/delivery/24-cube-input-normalization-audit.md)，重新采样16万条输入，逐维统计与数值检查。
- RL 方块归一化初版：[误差归一化与奖励调整](rl-station-map/delivery/25-cube-normalization-rewards.md)，2 cm / 20°归一化、碰撞−1、关闭张开奖励、增加抬升进度差奖励，预算300万条健康transition。
- RL 方块上一轮训练：[归一化与新奖励300万条启动记录](rl-station-map/delivery/26-cube-normalized-3m-training-launch.md)，已按用户指令停止，独立run保留实际采样、更新与监控记录。
- RL 方块上一版奖励与训练：[阶段接触事件、平滑闭爪、batch512](rl-station-map/delivery/27-cube-stage-contact-b512-training.md)，停止上一轮后在CUDA0–3从头训练300万条健康样本，旧run完整保留。
- RL 方块300万训练结果：[阶段成功率与奖励分解](rl-station-map/delivery/28-cube-stage-contact-b512-results.md)，完整完成23,281次更新；中期成功约95%–96%，后期回报被重复接触与闭爪奖励主导，最后100个训练回合成功0次，尚无独立checkpoint评估。

- RL 方块当前成功与奖励口径：[连续保持0.25秒与历史最高抬升奖励](rl-station-map/delivery/33-cube-hold-quarter-second-reward.md)，双指各>0.5 N、最低点距桌面≥1 cm、无支撑接触连续保持0.25 s；位置/旋转/接触/抬升权重为20/5/3/8。本轮仅修改与离线验证，上一轮[训练记录](rl-station-map/delivery/32-cube-clearance-training-launch.md)保留原判定。

- RL 随机站位方块：[物理固定底盘与69D输入](rl-station-map/delivery/36-random-stations-physical-base.md)，每个XY仅正对目标、每回合随机初始化，保留升降控制；本轮仅实现与短程验证。
- RL 方块跨场景候选：[原始目标选择与三个扩展任务](rl-station-map/analyze/44-cube-scene-target-selection.md)，继续用相同方块，选择 val3/Cup_12、val29/Cup_28、val2/Cup_12 的原始摆放位置，已完成 CPU 支撑、静置及初始站位检查，未验证抓取成功。
- RL val3 方块：[500万条随机站位训练启动](rl-station-map/delivery/45-val3-cube-random-stations-5m-training.md)、[结果与TensorBoard](rl-station-map/delivery/46-val3-cube-5m-results.md)，完成38,906次更新；训练期中期成功约83%，最后50万降至42.6%；后续独立验证见下。
- RL val3 独立验证：[166.9万与400万checkpoint质量图及各5次视频](rl-station-map/delivery/47-val3-two-checkpoint-evaluation.md)，成功分别4/5、5/5，无碰撞成功分别4/5、3/5；16站软价值图、全部10个视频均已核查。
- RL 站位软价值：[720万checkpoint离线价值图](rl-station-map/delivery/39-offline-soft-station-value.md)，三站69维核对通过，96个有效站位各128动作，正式评分无需加载仿真场景。
- RL checkpoint：best 与 TensorBoard 默认30秒窗口的成功率同步比较，每40万健康transition在 `ckpts/` 独立保留完整权重；见[实现与验证](rl-station-map/delivery/35-tensorboard-best-periodic-checkpoints.md)。已有训练进程继续使用启动时逻辑。

- RL PPO：[SAC/PPO 开关与同步多卡接入](rl-station-map/delivery/36-ppo-integration.md)，flat/graph、全局优势归一化、Actor/V独立裁剪、KL停止、V评分，已完成单/双卡真实短验证，未做正式训练。

- PPO正式训练：[CUDA0–3 cube 300万样本启动配置](rl-station-map/delivery/37-ppo-cube-g0123-training-launch.md)，实时进度与启动验收记录在对应run。
- PPO cube失败排查：[按pipeline分层](rl-station-map/analyze/ppo-cube-s0246-failure-diagnosis.md)，主因KL硬停使300万样本只做666次梯度步，叠加熵塌缩。
- PPO Cup_30迁移：[合并rl-cup、修正PPO、方案A闭爪门槛与增量夹爪、验证与价值图](rl-station-map/delivery/48-ppo-cup-migration.md)；[总结](rl-station-map/delivery/49-ppo-cup-summary.md)：845万模型验证40/40成功、38/40无碰撞。
- [从头训练固定底盘 PPO 与多抓法站位质量图](rl-station-map/plan/04-fixed-base-station-quality.md)，使用 0.9 比例 Panda–Omron，采样半径与间距均扩大至 1.2 倍，按严格抓取成功率、全谱扰动、五头校准依次验收。
- PPO Cup_30移动底盘：[合并rl-mobile、按PPO定奖励与参数、底盘噪声排查](rl-station-map/delivery/54-ppo-cup30-mobile.md)；[总结](rl-station-map/delivery/55-ppo-cup30-mobile-summary.md)：1193万模型验证39/40成功、35/40无碰撞。
- Cup_30抓取候选：[物体级射线预处理与运行前抽样](rl-station-map/delivery/cup30-grasp-preprocessing.md)，1000→564→FPS128，训练/标定默认均匀抽16；仅验证静态几何与消费接口。

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
| `robocasa/` | RoboCasa365 原生场景、统一运行接口与派生 pick 判定 | [需求](robocasa/requirement/01-native-pick.md)、[方案](robocasa/plan/01-native-runtime.md)、[交付](robocasa/delivery/01-native-pick.md)、[运行指南](robocasa/delivery/running.md)、[π0.5 抓取实验](robocasa/delivery/02-pi05-pick.md) |
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

- [可配置移动底盘与升降协同训练](rl-station-map/delivery/40-mobile-base-training.md)：10D动作、75D观测、局部边界、真实控制与训练启动记录。

- RL 最低软价值站位：[S0091-y00五次操作视频](rl-station-map/delivery/41-lowest-value-station-five-rollouts.md)，720万checkpoint，成功5/5、无碰撞成功2/5。

- RL 最高软价值站位：[S0078-y00五次操作视频](rl-station-map/delivery/42-highest-value-station-five-rollouts.md)，720万checkpoint，成功4/5、无碰撞成功3/5。

- [PPO 移动底盘初始采样范围扩大1.5倍](rl-station-map/delivery/56-ppo-mobile-expanded-sampling.md)
