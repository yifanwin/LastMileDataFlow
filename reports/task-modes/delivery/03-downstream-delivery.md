# P5/P6 下游与真实验证

- 状态：active
- 日期：2026-10-26
- 替代：06-mode-downstream.md、07-real-validation.md
- 关联：[标定交付](02-calibration-delivery.md)、[构建交付](01-build-delivery.md)、[底盘修复](04-mobile-base-hold-fix.md)、[运行清单](05-run-inventory.md)

## 1. 范围

用户要求本轮止于标定，P5/P6 不作为本轮验收项。本文件记录已有探索性下游 run、环境与策略 smoke 的实际结果，供后续继续时复用；不把任何未完成环节写成主链闭环。

## 2. train 采集与物化

- 采集只选 train；外部标定复用原执行配置、profile、阈值快照和诊断，不重新标定或重分类。
- 每标签使用诊断保存的同 tier 合格 S1；同一 S0/S1 合并标签，只执行一个连续 attempt。先写完整 `pair_index`，再做观测与连续执行；观测失败仍保留计划。
- 数值标定成功轨迹缺在线 RGB。`render_observations` 加载原 MJB 和完整 integration state，在初始边界及每个实测控制边界渲染，不积分、不重判、不改原 trial，`new_physical_steps=0`。
- 只有完整观测视图进入训练索引；`s1_direct_operation` 与连续成功按 rollout 去重。

实际结果：

| run | 结果 |
|---|---|
| p5-train-negative-20261026-r01 | Potato 无成功，planned/processed/executed pairs = 0 |
| p5-train-collect-20261026-r01 | 13 对：10 对 `astar_no_path`，3 对执行后 `robocasa_reference_tracking_timeout`；train_index 12 条 |
| p5-train-materialized-20261026-r01 | 12 条全部物化：`observation_complete=true`、`measured_state_replay`、与源 attempt 一一对应 |

12 条训练条目全部是 `s1_direct_operation`（atomizer S0043–S0048 的成功操作），**没有成功的 S0→S1 连续配对**。3 条实际执行的连续配对均跟踪超时，10 条在 A* 阶段无路。

## 3. val benchmark

- 模式 `build-bench` 只消费已有标定覆盖的 val；一个有标签 S0 生成一个 episode。
- 私有 RecoveryRegion 取该起点各标签合格 S1 的并集；公开 episodes 不含最终标签/S1，oracle 保存标签、S1、proposal_intent 和诊断引用。
- 未知 timeout、基础设施错误或记录不完整不充任务失败；完整计划分母与 coverage 同时保留。
- 一个站点的 3/3 站位不能替换已选 2/3 S1。

| run | 标定来源 | 站点低/高 | candidate → eligible → written | 排除 |
|---|---|---|---:|---|
| p5-negative-bench-20261026-r01 | incomplete 诊断 | — | 0 → 0 → 0 | 20 站 incomplete |
| p5-full-negative-bench-20261026-r01 | Cup r01（修复前） | 18/0 | 0 → 0 → 0 | no_high_rate_s1=18；旧 20% 汇总保留 |
| p5-full-negative-bench-20261026-r02 | Cup r01（修复前） | 20/0 | 0 → 0 → 0 | no_high_rate_s1=20 |
| p5-mode-bench-20261026-r01 | Cup r02（修复后） | 7/13 | 7 → 0 → 0 | **no_navigation_path=7** |
| p5-open-bench-20261026-r01 | Open r02（修复后） | 7/4 | 7 → 1 → 1 | no_navigation_path=6 |

修复后 Cup 已有 13 个高率站位，原 run 的 7 个低率 S0 均记录 `no_navigation_path`。[专项复核](../../navigation/delivery/06-cup-navigation-review.md)确认七个起点均被保守膨胀栅格屏蔽；七条通往合格 S1 的候选路线通过 363 个真实模型静态位姿检查，尚未验证动态跟踪，原 benchmark 仍为 0 episode。Open 原 run 得到 1 个可评测 episode，其余负结果未独立复核。

后续[导航修复](../../navigation/delivery/07-navigation-geometry-fix.md)已完成：保留 2 cm 余量的新 Cup benchmark 为 2/7、新 Open 为 7/7；Train 的 13 对中 9 对找到参考路线。Cup 两条、Open 一条、Train 一条实际导航均通过且记录完整。它们属于新 run，不改变上表历史计数；导航成功不等于完整任务成功。

再后续已完成[软净空版本](../../navigation/delivery/08-navigation-soft-clearance.md)：取消 20 mm 硬门槛后，Cup/Open 均为 7/7，Train 11/13 对有路线；五条实际导航通过。剩余两对为搜索预算耗尽，未新增完整连续操作示教或策略评测结果。

## 4. val 策略评测

`p6-open-bench-eval-20261026-r01`：对 p5-open-bench 的 `e000000` 运行 π0.5（panda_omron，replan=5，horizon=400）。

- 1/1 执行，400 步，`policy_step_budget_exhausted`，`task_outcome=failure`；recording_complete=true。
- coverage=1.0、TSR=0.0、observed TSR=0.0；base 路径 1.043 m、末位移 0.388 m、末 yaw 变化 −0.508 rad，`effective_adjustment=true`。
- n=1，只能说明该 episode 未完成，不能作为模式难度或策略能力结论。

## 5. 环境、策略与回放

| 检查 | 实际结果 |
|---|---|
| torch / JAX | torch 2.7.1+cu128、CUDA 12.8、Blackwell [12,0]；32×32 matmul 首元素 10416 正确；JAX 0.5.3 GPU 通过 |
| 真实观测转策略输入 | 16D 状态、三路 256×256 RGB；state 不变、`simulation_started=false`、0 物理步 |
| RoboCasa 保持与 TCP 小步 | 关节漂移 8.66e-11 rad；TCP delta 约 0.0101 m / 3.05 s |
| 移动底盘 + TCP 跟踪 | 底盘误差 ~2.8e-4；TCP 位置 6.82e-4 m、旋转 9.61e-4 rad / 5 s |
| π0.5 真权重 | 加载 6.04 s、首推 8.31 s；actions 50×12、raw 50×32；输入为真实 observation.npz |
| π0.5 固定站位闭环 | 60 步，记录/观测完整；failure `policy_step_budget_exhausted`；`uncalibrated_fixed_station=true` |
| 成功轨迹三相机回放 | Cup r000010/S0029，328 帧×3 相机，21.79 s；`new_physical_steps=0`；属 val，未建训练索引 |
| 跟踪精度分析 | 8 条 rollout，approach 末端误差 0.81–8.0 mm；目标位移 ~1e-7 m；未调整容差 |
| 共享准备对照 | shared 11.589 s vs fresh 22.014 s（1.90×）；初始化 49 vs 98；站位/锚点一致 |

策略 smoke 的 `task_success=null` 或固定站位 rollout 不作为困难 benchmark；真实 GPU 运算、真权重推理和任务成功分别报告。

10 段关键轨迹视频已离线渲染：Cup/Open 修复前后各一对、Bottle Ground/Approach、Open Arm-path/Narrow/Articulated 与 Articulated train 对照。均为实测状态回放（`new_physical_steps=0`），清单见[视频索引](../../../outputs/diagnostics/task-modes/p6/analysis/p6-videos-20261026-r01/README.md)。新增 `evaluate-stations --replay-layout grid`，可把三路相机与俯视底盘轨迹合成单条 mp4。

## 6. P6 补充构建与 Visibility/Open 搜索

| run | 接受/拒绝/跳过 | wall s | 结果 |
|---|---|---:|---|
| p6-bottle-build-20261026-r01 | 13/22/5 | 98.9 | 新增 Pepper Arm-path（Vase 台面）与 Atomizer Narrow（Footstool，一次 repair）物理正例 |
| p6-soap-build-20261026-r01 | 4/17/3 | 48.4 | Reachability×3、Orientation×1 接受 |
| p6-high-open-build-20261026-r01 | 2/0/0 | 16.8 | val/train 显式把手各一个 |
| p6-obstruction-build-20261026-r01 | 0/12/0 | 59.2 | 无合法站位对/通道/遮挡对照 |
| p6-obstruction-build-20261026-r02 | 1/17/0 | 18.9 | house 3/4 的 Approach 天然候选接受 |
| p6-obstruction-build-20261026-r03 | 0/36/0 | 128.3 | 保留实际拒绝 |
| p6-visibility-build-20261026-r01 | 0/2/0 | 2.7 | `no_aggregate_visibility_contrast` |

- Visibility 专项搜索：cup 10 个、spray 8 个合法可导航视角均无自然低/高对照；另一个变体检查 8 个候选，全部无聚合对比。Visibility 仍是明确负结果。
- `p6-open-targets-20261026-r01` 纠正了 Safe 锚点仅 0.153 m 的问题，改用 Dresser_220_1 显式把手等实际较高目标。

## 7. 边界与阻塞

- 没有成功的 S0→S1 连续示教；12 条训练数据是 S1 直接操作 + 离线物化观测。
- 原导航误拒绝已修复并完成 Train/Open 扩展复核；新 Cup 2/7、Open 7/7、Train 9/13 对通过路线检查，四条独立物理导航通过。剩余实际净空不足和有限搜索负结果保留；没有因此新增完整连续操作示教或策略成功结果。
- 策略失败、基础设施错误、未知结果与任务失败分开；π0.5 的 1 个 episode 不足以判断难度或策略能力。
- P5/P6 未达到阶段完成条件，整体目标保持 active。
