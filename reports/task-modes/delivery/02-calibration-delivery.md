# P4 标定与多标签诊断交付

- 状态：done
- 日期：2026-10-26
- 替代：05-calibration-evidence.md、09-calibration-delivery.md
- 关联：[需求](../requirement/01-task-mode-construction.md)、[方案](../plan/01-task-mode-implementation.md)、[计划表](../plan/02-build-schedule.md)、[构建交付](01-build-delivery.md)、[底盘修复](04-mobile-base-hold-fix.md)、[运行清单](05-run-inventory.md)

## 1. 范围与固定口径

用户于 2026-10-26 将本轮范围收敛为“流程到标定完成即可”。本文件交付场景构建后的真实逐站位标定、S0/S1 资格和多标签诊断；下游采集与评测仅作为既有产物记录在[下游与验证](03-downstream-delivery.md)。

| 项目 | 固定值 |
|---|---|
| 本体 / 任务 | PandaOmron `robocasa-v1`；Pick / Open |
| 站位 | anchor 半径 1 m、lattice 0.25 m；最多 20 个合法 `(x,y,yaw)`，每姿态 3 次 |
| 成功率 | S0 优先 0/3、候补 1/3；S1 对每标签先合格 3/3，无则 2/3 |
| 底盘 | 标定与诊断允许现有联合规划，不切换固定底盘 |
| 诊断阈值 | IK 可行比例上限 0.01；距离/侧向改善 0.2 m；底盘低余量 0.08 m、改善 0.1 m |
| 可见性 | 低/好/改善 0.2/0.6/0.3；参考像素门槛 24 |
| 编辑 | 天然/无编辑优先；最多一次局部 repair；原始输入只读 |

本轮未逐 val 样本调阈值；各 run 的 `config.json` 保存实际快照。

## 2. 标定与诊断机制

- **阶段/lane 证据**：单站与 batch 共用 `planning/evidence.py`；保存 phase、query_index、solve_index、逐 lane success 和返回维度。`trials.jsonl` 只新增 `planning_ref`，不复制大型求解数组。
- **三次分母**：每姿态必须 0/1/2 三次完整、scene/task 绑定且无重复；基础设施错误、初始化错误、记录错误和未知 timeout 不补成任务失败。`planning_no_solution` 是已完成的有限预算结果，与基础设施错误分开。
- **资格**：区分 `incomplete`、`no_success`、`no_high_rate_s1`、`eligible`。S0 按 0/3、1/3 排序；S1 每标签先找 3/3，无则 2/3；同 tier 按路径长度和 station ID 确定性选证据，并保留合格集合。
- **多标签诊断**：`data/evaluation/diagnosis.py` 检查全部八类。Visibility 可由高操作率但低可见起点独立成立；Arm-path/Approach 只接受具体失败 trial 的对应阶段；Articulated 不增加 S0 接触门槛，但 S1 必须有 `physical_open` 完整 verdict。缺 probe、映射或测量保持 unresolved。
- **有界 IK probe**：`diagnostic_ik` 沿用原准备状态、移动底盘、碰撞/FOV 和实际 grasp 输入，每 grasp 独立求解；默认 16 grasp×32 seed、每 S0 至多一次、60 s active 上限。不调用 TrajOpt，不新增 trial 或物理步，不增加原 `plan_count`。
- **几何测量**：诊断在新建、未开始的共享模型 view 中恢复实际准备状态，测底盘、目标水平坐标、clearance、初始化和三相机；导航使用同一 S0 准备状态的完整房间栅格与真实端点。

## 3. 修复后的完整标定

以下 run 位于 `outputs/diagnostics/task-modes/`；逐站与修复前明细见[运行清单](05-run-inventory.md)第 4 节。

| run | 完整记录/计划 | 成功 | 执行后失败 | 未执行(规划无解) | wall s | 资格 / 标签 |
|---|---:|---:|---:|---:|---:|---|
| p4-cup-full-20261026-r02 | 60/60 | 34 | 5 | 21 | 350.2 | eligible；arm_path×7、narrow×7；7 probe |
| p4-train-bottle-full-20261026-r02 | 42/42 | 12 | 11 | 19 | 480.3 | atomizer reachability eligible + intent_hit；8 S0；8 probe |
| p4-high-open-full-20261026-r02 | 60/60 | 14 | 20 | 26 | 1,267.2 | 2×eligible、intent_hit×2；15 S0；12 probe |
| p4-yaw-full-20261026-r02 | 60/60 | 3 | 41 | 16 | 536.8 | 1 eligible、1 no_high_rate_s1、1 no_success；无标签 |

**合计 222 条完整 trial、63 条真实成功、invalid_trials=0**。任务失败 77 条：46 条为执行阶段失败，31 条为执行到 `open`/`lift_actual` 等后续阶段后规划无解。另有 82 条 0 物理步、`not_executed`（按 `reason` 统计的 `planning_no_solution` 共 113 次）。

`p6-independent-calibration-20261026-r01` 在相同 S0013 独立复核 3/3 成功，但仅覆盖 3/60、`shared_station_preparation=false`，只作为复核，不替换完整标定或最终标签。

## 4. 模式证据

| 模式 | 真实证据 | 结论 |
|---|---|---|
| Reachability | atomizer S0000 0/3→S0047 3/3；IK 可行比例 0 | 有正例 |
| Ground | atomizer S0036 0/3→S0047 3/3；直线被挡且 A* 有路径 | 有正例 |
| Arm-path | atomizer S0029 0/3→S0047 3/3；失败支持 goalset IK 有解、TrajOpt 失败 | 有正例；不声称唯一碰撞因果 |
| Approach | atomizer S0044 1/3→S0047 3/3；引用原失败 trial 的阶段证据 | 有正例 |
| Narrow | val Open S0022 0/3→S0021 2/3；底盘余量约 0.02635 m、改善约 0.150 m | 有正例；余量为轮廓外接圆近似 |
| Articulated | val Open S0013 0/3→S0021 2/3；S1 有 `physical_open` 关节进展/保持 | 有正例；S0 不增加先接触把手门槛 |
| Orientation | yaw 对照未形成合格高低率关系 | 无正例，不宣称验证成功 |
| Visibility | 独立两个变体均拒绝；天然视角与遮挡候选未满足代理 | 无正例，不宣称验证成功 |

- Cup 的构造意图是 Ground，但实际命中 Arm-path + Narrow，`intent_hit=false`；7 个 S0 全部 0/3，均引用真实高率 S1。
- train bottle 中 Pepper 编辑候选无成功（no_success）；atomizer 候选命中 Reachability 等多种标签。显式把手 Open 的 train/val 均 eligible，Articulated 意图命中。
- 八类接口全部保留，未将范围缩减为已验证的六类；Visibility 的负结果不被包装为正例。

## 5. 修复前结果

修复（见[底盘修复](04-mobile-base-hold-fix.md)）前的完整标定均保留，但**不能作为修复后任务固有困难的证据**：

| run | 记录 | 成功/失败/未执行 | 结论 |
|---|---:|---|---|
| p4-cup-full-20261026-r01 | 60 | 2/37/21 | no_high_rate_s1 |
| p4-bottle-full-20261026-r01 | 60 | 2/35/23 | 2×no_high_rate_s1、2×no_success |
| p4-high-open-full-20261026-r01 | 60 | 0/34/26 | no_success |
| p4-open-full-20261026-r01 | 51 | 0/0/51 | no_success |
| p4-potato-full-20261026-r01 | 15 | 0/0/15 | no_success |
| p4-clock-full-20261026-r01 | 24 | 0/0/24 | no_success |
| p4-safe-full-20261026-r01 | 45 | 0/0/45 | no_success；Safe 锚点仅 0.153 m |

修复后 Cup 从 2/60 变为 34/60，显式把手 Open 从 0/60 变为 14/60；相同 S0013/seed 42–44 从 0/3 变为 2/3。

## 6. 验证

- 标定交付时全套回归 **354 项：352 通过、2 跳过**（`outputs/logs/task-modes-calibration-delivery-regression-r01.log`）；场景预览改动后最新为 **355 项：353 通过、2 跳过**（`outputs/logs/task-modes-scene-preview-regression-r02.log`）。跳过为显式 OpenGL 检查；真实 GPU 渲染/rollout 单独报告。
- Blackwell/cu126 的 `no kernel image` 已通过切换到 torch 2.7.1+cu128 修复；真实 CUDA 矩阵运算、JAX GPU 和 cuRobo rollout 均通过。
- 独立 IK probe 覆盖 512 次、167 次可行（ratio 0.3262），`plan_count=0`、无新 trial。
- 真实准备状态测量：目标距离 0.790569 m、底盘 clearance 0.176353 m；三相机可见/参考像素 304/304、845/845、292/292。
- 各阶段真实 trial 的 phase/lane、seed、完整性、相机像素/外参和编辑/repair/重载均已检查。

## 7. 边界与后续

- Orientation、Visibility 没有真实正例；对应参数保持初版默认，不声称完成正例定标。
- 全部完整只表示记录与计划齐备，不表示任务成功，也不表示构造意图命中。
- 下游已有探索性 run（12 条 S1 直接示教、1 个 Open episode 评测），见[下游与验证](03-downstream-delivery.md)；用户“本轮止于标定”的范围决定不变。
- 未完成：正例连续采集、困难 S0 策略评测、同协议性能对照。
