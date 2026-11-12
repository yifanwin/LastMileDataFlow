# 全部已跑 run 的 trial 统计

- 状态：active
- 日期：2026-10-27（16:20 快照）
- 替代：上一版 11 run / 57,191 trial 统计
- 关联：[实时状态](../delivery/status.md)、[成功率归因](01-low-success-analysis.md)、[planner 复用缺陷](03-planner-reuse-target-binding.md)、[需求](../requirement/01-requirement.md)

> 口径：直接汇总 `outputs/` 下所有带 `trials.jsonl` 的 run。共 14 个 run：11 个标定 run（主 cohort，58,314 trial）+ 3 个 cup30 诊断 run（127 trial），合计 58,441 trial。主 cohort 的 scene 无重叠，未做去重扣减。`no-edit-val-all-c006` 尚未产出 trial；`exp-cup30-last2` 扫描时仍在追加，诊断 run 的数字会继续变化。

## 0. 本版口径变化

1. **数据源从 11 个 run 扩到 14 个。** 新增 `exp-cup30-only`、`exp-cup30-last`、`exp-cup30-last2` 三个诊断 run；`no-edit-val-supp8-c003` 已从 428 条补到 1,551 条。
2. **主 cohort 与诊断 run 分开报告。** cup30 是对照实验（单目标 vs 多目标），不混入标定成功率。
3. **目标/把手绑定状态改用场景 XML 的 body 子树判定。** 上一版按 `task.target_body` 字符串前缀匹配，会把正常绑定误判为 `wrong`；open 的 `target_world` 在 `handle_body == target_body` 时为空是设计正常，必须看 `handle_world`。
4. **失败原因按 goalset / execution / infrastructure 分开。** `planning_no_solution` 仍不计为任务失败；`episode_error`、`timeout` 单列。

## 1. 主 cohort 总体成功率

| 指标 | pick | open | 合计 |
|---|---:|---:|---:|
| trial | 23,178 | 35,136 | 58,314 |
| success | 209 | 1,472 | 1,681 |
| trial 成功率 | 0.90% | 4.19% | **2.88%** |
| 已执行 trial | 6,794 | 8,632 | 15,426 |
| 已执行成功率 | 3.08% | 17.05% | 10.90% |
| 任务数 | 545 | 392 | 937 |
| scene 数 | — | — | 2,729 |

trial 状态总量：

| 状态 | 数量 | 占比 |
|---|---:|---:|
| planning_no_solution | 42,888 | 73.55% |
| failure | 11,382 | 19.52% |
| episode_error | 1,898 | 3.25% |
| success | 1,681 | 2.88% |
| timeout | 465 | 0.80% |

## 2. 各 run 成功率（主 cohort）

| run | trial | success | 成功率 | planning_no_solution | failure | episode_error | timeout |
|---|---:|---:|---:|---:|---:|---:|---:|
| no-edit-pilot-c000 | 4,287 | 122 | 2.85% | 3,430 | 527 | 199 | 9 |
| no-edit-val-all-c000 | 1,683 | 18 | 1.07% | 1,308 | 253 | 67 | 37 |
| no-edit-val-all-c001 | 9,618 | 252 | 2.62% | 7,030 | 1,954 | 299 | 83 |
| no-edit-val-all-c002 | 8,601 | 267 | 3.10% | 6,070 | 1,949 | 238 | 77 |
| no-edit-val-all-c003 | 7,806 | 191 | 2.45% | 5,566 | 1,719 | 216 | 114 |
| no-edit-val-all-c004 | 9,927 | 398 | 4.01% | 7,179 | 2,037 | 270 | 43 |
| no-edit-val-all-c005 | 8,223 | 361 | 4.39% | 5,841 | 1,706 | 219 | 96 |
| no-edit-val-supp8-c000 | 450 | 6 | 1.33% | 366 | 58 | 16 | 4 |
| no-edit-val-supp8-c001 | 2,511 | 1 | 0.04% | 1,922 | 415 | 173 | 0 |
| no-edit-val-supp8-c002 | 3,657 | 48 | 1.31% | 2,983 | 470 | 154 | 2 |
| no-edit-val-supp8-c003 | 1,551 | 17 | 1.10% | 1,193 | 294 | 47 | 0 |

## 3. 诊断 run（cup30 对照）

| run | 目标数 | trial | success | 成功率 | target 绑定状态 |
|---|---:|---:|---:|---:|---|
| exp-cup30-only-20261027-1542 | 1（Cup_30） | 60 | 37 | **61.67%** | correct 60 |
| exp-cup30-last-20261027-1542 | 4（各 1） | 2 | 1 | 50.00% | correct 2 |
| exp-cup30-last2-20261027-1600 | 4（各 1） | 59 | 2 | **3.39%** | empty 39 / wrong 17 / correct 3 |

> 三个 run 的 station 半径均为 1.0 m、执行配置相同。单目标时复用永远在同一任务内，绑定正确；多目标时跨任务复用触发错绑。`last2` 扫描时仍在追加，17 次 `IndexError`。

## 4. 失败原因（trial 级，主 cohort）

| 原因 | pick | open | 合计 |
|---|---:|---:|---:|
| IK (goalset) 目标位姿不可达 | 2,216 | 14,509 | 16,725 |
| IK (goalset) 位姿收敛但约束不可行 | 8,447 | 4,190 | 12,637 |
| TrajOpt (goalset) IK 有解、轨迹不可行 | 5,178 | 6,863 | 12,041 |
| `robocasa_reference_tracking_timeout` (pregrasp) | 906 | 3,979 | 4,885 |
| 执行中途规划无解 (lift_actual) | 3,180 | 0 | 3,180 |
| episode_error (lift_actual) | 1,898 | 0 | 1,898 |
| success | 209 | 1,472 | 1,681 |
| `robocasa_reference_tracking_timeout` (approach) | 522 | 779 | 1,301 |
| 执行中途规划无解 (open) | 0 | 1,103 | 1,103 |
| TrajOpt (grasp) IK 有解、轨迹不可行 | 338 | 499 | 837 |
| IK (pregrasp) 位姿收敛但约束不可行 | 138 | 348 | 486 |
| `robocasa_reference_tracking_timeout` (open) | 0 | 475 | 475 |
| `attempt_active_budget` (open) | 0 | 367 | 367 |
| `open_no_grip` (close) | 0 | 275 | 275 |
| 其他 | 146 | 277 | 423 |

## 5. 失败环节

### 5.1 阶段

| 阶段 | pick | open | 合计 |
|---|---:|---:|---:|
| planning（未执行） | 16,384 | 26,504 | 42,888 |
| execution（已执行但失败） | 4,668 | 6,714 | 11,382 |
| infrastructure（episode_error） | 1,898 | 0 | 1,898 |
| timeout（attempt_active_budget） | 19 | 446 | 465 |
| success | 209 | 1,472 | 1,681 |

### 5.2 goalset 子阶段

| 操作 | IK 不可达 | IK 约束不可行 | TrajOpt 不可行 | 合计 |
|---|---:|---:|---:|---:|
| pick | 2,216 | 8,447 | 5,178 | 15,841 |
| open | 14,509 | 4,190 | 6,863 | 25,562 |

goalset 占全部 trial 的 71.0%。其中主导约束项 96%+ 是 `constraints/scene_collision`，95%+ 落在 `omron_base`；`target_collision` 只在 <0.2% 的失败里是最大代价项（详见 [归因](01-low-success-analysis.md) 2.8）。

### 5.3 execution 子阶段

| 操作 | 终止阶段 / 原因 | 数量 | 占已执行 |
|---|---|---:|---:|
| pick (6,794) | lift_actual / planning_no_solution | 3,180 | 46.81% |
| pick | lift_actual / `IndexError` | 1,842 | 27.11% |
| pick | pregrasp / tracking timeout | 906 | 13.34% |
| pick | approach / tracking timeout | 522 | 7.68% |
| pick | hold / success | 196 | 2.88% |
| open (8,632) | pregrasp / tracking timeout | 3,979 | 46.10% |
| open | open_hold / success | 1,472 | 17.05% |
| open | open / planning_no_solution | 1,103 | 12.78% |
| open | approach / tracking timeout | 779 | 9.02% |
| open | open / tracking timeout | 475 | 5.50% |
| open | open / attempt_active_budget | 367 | 4.25% |
| open | close / open_no_grip | 275 | 3.19% |

### 5.4 异常（episode_error）

| 异常 | 数量 | 环节 |
|---|---:|---|
| `RuntimeError: IndexError: list index out of range` | 1,842 | pick, lift_actual |
| `RuntimeError: Tensors must have same number of dimensions: got 1 and 2` | 56 | pick, lift_actual |

两类均发生在 pick 抓取闭合后的 lift 阶段，与 [planner 复用缺陷](03-planner-reuse-target-binding.md) 中 `target_world` 为空 / 携带几何错误一致。

## 6. 绑定状态

按场景 XML 的 body 子树判定；trial 内所有 query 取主状态。

| 操作 | 绑定状态 | trial | success | 成功率 | 已执行 | 已执行成功率 |
|---|---|---:|---:|---:|---:|---:|
| pick | target correct | 384 | 53 | 13.80% | 133 | 39.85% |
| pick | target wrong | 14,508 | 112 | 0.77% | 4,124 | 2.72% |
| pick | target empty | 8,256 | 44 | 0.53% | 2,525 | 1.74% |
| pick | target mixed | 30 | 0 | 0.00% | 12 | 0.00% |
| open | handle correct | 117 | 12 | 10.26% | 21 | 57.14% |
| open | handle wrong | 4,096 | 168 | 4.10% | 1,003 | 16.75% |
| open | handle empty | 30,923 | 1,292 | 4.18% | 7,608 | 16.98% |

## 7. 目标高度与底座间隙

### 7.1 anchor_z

| 操作 | anchor_z | trial | success | 成功率 | pns |
|---|---|---:|---:|---:|---:|
| pick | [0, 0.4) | 2,598 | 0 | 0.00% | 99.69% |
| pick | [0.4, 0.5) | 609 | 5 | 0.82% | 91.79% |
| pick | [0.5, 0.7) | 1,692 | 9 | 0.53% | 74.88% |
| pick | [0.7, 1.0) | 14,058 | 150 | 1.07% | 66.87% |
| pick | [1.0, 1.4) | 4,029 | 45 | 1.12% | 59.17% |
| pick | ≥1.4 | 192 | 0 | 0.00% | 95.31% |
| open | [0, 0.4) | 14,967 | 0 | 0.00% | 100.00% |
| open | [0.4, 0.5) | 1,041 | 0 | 0.00% | 99.04% |
| open | [0.5, 0.7) | 7,287 | 211 | 2.90% | 67.78% |
| open | [0.7, 1.0) | 6,426 | 677 | 10.54% | 50.92% |
| open | [1.0, 1.4) | 4,341 | 422 | 9.72% | 45.47% |
| open | ≥1.4 | 1,074 | 162 | 15.08% | 29.89% |

### 7.2 clearance_base_m

| 操作 | 间隙 | trial | success | 成功率 | pns |
|---|---|---:|---:|---:|---:|
| pick | 负 | 12,642 | 70 | 0.55% | 82.58% |
| pick | [0, 0.05) | 3,096 | 27 | 0.87% | 60.01% |
| pick | [0.05, 0.1) | 2,832 | 39 | 1.38% | 55.97% |
| pick | [0.1, 0.2) | 3,252 | 43 | 1.32% | 55.60% |
| pick | ≥0.2 | 1,356 | 30 | 2.21% | 51.11% |
| open | 负 | 13,243 | 434 | 3.28% | 81.79% |
| open | [0, 0.05) | 5,738 | 293 | 5.11% | 79.40% |
| open | [0.05, 0.1) | 4,449 | 98 | 2.20% | 82.22% |
| open | [0.1, 0.2) | 5,565 | 154 | 2.77% | 76.44% |
| open | ≥0.2 | 6,141 | 493 | 8.03% | 52.17% |

全 cohort 负间隙占 44.39%（25,885/58,314）。

## 8. 目标类别

主 cohort 的类别级 trial/成功数本版尚未重新生成（诊断 run 会抬高特定类别，例如 Cup_30，必须按 run 过滤后再汇总）。结构上仍成立：

- **pick**：有成功类别集中在 vase、bowl、soapdispenser、candle、mug、peppershaker、apple、winebottle 等可稳定抓取的物体；book、statue、plate、potato 等零成功类别累计 trial 已超过 800，不是样本不足。
- **open**：成功集中在 dresser、fridge、sidetable；desk、safe、microwave 零成功。低把手（z<0.5 m）几乎全部落在前两类的底部抽屉/柜门。

## 9. 要点

1. **goalset 是最大失败池（41,403 trial，71.0%），主导约束是底座与场景碰撞，不是目标错绑**；`omron_base` 在 95%+ 的 goalset 失败里是最大代价连杆。
2. **pick 的执行损失集中在 lift_actual**：3,180 条姿态规划无解 + 1,842 条 IndexError，合计占 pick 已执行的 73.9%；与 target 绑定错误强相关。
3. **open 的执行损失集中在 pregrasp 跟踪超时**：3,979 条，占 open 已执行的 46.1%；低把手和 handle 错绑都会放大。
4. **可达性硬边界**：open z<0.5 m 共 16,008 trial、0 成功；pick z<0.4 m 共 2,598 trial、0 成功；pick z≥1.4 m 共 192 trial、0 成功。
5. **cup30 对照**给出最直接的反事实：单目标（绑定正确）61.67%，多目标（跨任务错绑）3.39%。
6. 本统计描述现状，不构成修复后的预期；`no-edit-val-all-c006` 尚未纳入，`exp-cup30-last2` 仍在追加。
