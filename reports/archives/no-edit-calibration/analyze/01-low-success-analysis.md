# 无编辑标定成功率偏低的归因分析

- 状态：active
- 日期：2026-10-27（16:20 快照）
- 替代：无
- 关联：[需求](../requirement/01-requirement.md)、[计划](../plan/01-experiment-plan.md)、[实时状态](../delivery/status.md)、[全量统计](02-all-runs-trial-statistics.md)、[planner 复用缺陷](03-planner-reuse-target-binding.md)

> 只读分析，不修改任何标定记录。数据源已更新为 `outputs/` 下全部 14 个含 `trials.jsonl` 的 run：11 个标定 run（主 cohort，58,314 trial）+ 3 个 cup30 诊断 run（127 trial），合计 58,441 trial。`no-edit-val-all-c006` 尚未产出；`exp-cup30-last2` 扫描时仍在追加。

## 0. 相对上一版的修正

上一版（c004/c005 口径）的核心方向成立，但有三处方法与结论需要修正：

1. **`target_world` 的“正确/错误”判定口径错误。** 目标 body 名是 `..._1_0_2`，碰撞几何实际挂在子 body `..._1_1_2` 上；按 `task.target_body` 字符串前缀匹配会把大量正常绑定判为 `wrong`。本版改为解析场景 `model.xml`，按 body 子树内的碰撞 geom 名称集合判定。
2. **open 不能看 `target_world` 判断错绑。** `target_world` 是 `target_bodies - handle_bodies`；约 41% 的 open trial 中 `handle_body == target_body`，此时 `target_world` 为空是设计正常。open 的错绑证据应看 `handle_world.json`。
3. **“新建 planner 的 16 个 query 全部正确”不能推广到 trial 级。** 一个 trial 有 `preclose` 和 `pose` 两类 query，可能分别落到不同 worker/planner；trial 级“是否出现过新建 planner”不再能代表绑定正确。

## 1. 结论

主 cohort 成功率 2.88%（pick 0.90%、open 4.19%），不是单一缺陷。按对失败量的贡献排序：

### 1.1 goalset 规划是最大失败池，主导约束是底座与场景碰撞，不是目标错绑

- 42,888 条 `planning_no_solution`，占 73.55%；其中 goalset 阶段占绝对多数。
- solver trace 里 96% 以上的失败 trial，最大约束代价项是 `constraints/scene_collision`，且 95% 以上落在 `omron_base`（其余为 `panda_link0/link5` 等）。`constraints/target_collision` 只在不到 0.2% 的失败里是最大代价项。
- 站位记录 `clearance_base_m < 0` 的 trial 占 44.4%。负间隙时 pick/open 成功率 0.55%/3.28%，间隙 >0.2 m 时 2.21%/8.03%。
- 因此，goalset 的主要瓶颈指向**底座被放得太靠近家具，规划时无法让出无碰撞位姿**；目标几何错绑会恶化约束，但在 41,403 条 goalset 失败中，`target_collision` 主导的不到 0.2%，不能用它解释这一层。

### 1.2 planner 复用目标绑定缺陷是 pick 执行阶段的主因

- c005 的 10,914 个 query 中 10,882 个复用 solver（99.7%），只有 32 个新建；全量 trial 中新建 planner 覆盖不足 1%。
- 按 XML 子树口径，pick 的 `target_world` 正确仅 384/23,178（1.66%）：成功率 13.80%，已执行子集 39.85%；错误/空 22,764 条，成功率 0.53–0.77%，已执行子集 1.74–2.72%。
- **cup30 对照实验**给出方向最直接的对照：同一套代码与配方参数，
  - 单目标 run（`exp-cup30-only`，所有复用都在同一任务内，60/60 绑定正确）：37/60 = **61.67%**；
  - 多目标 run（`exp-cup30-last2`，跨任务复用，39/59 空、17/59 错）：2/59 = **3.39%**，并出现 17 次 `IndexError`。
  - 二者任务集合不同（单目标只有 Cup_30，多目标含 4 个目标），因此不是完美随机对照；但方向与任务内配对、已执行子集一致。
- 缺陷的直接损失集中在 pick 的 `lift_actual`：3,180 条姿态规划无解 + 1,842 条 `IndexError` + 56 条张量维度异常，合计 5,078 条，占 pick 已执行 trial 的 74.7%。
- open 的 `handle_world` 同样普遍错绑：正确仅 117/35,136（0.33%），空 88.0%、错 11.7%。但 open 的绝对成功主要受高度可达性与执行跟踪限制，错绑不是唯一主因。

### 1.3 目标高度超出可达范围，站位采样未做预筛

- open 把手 `anchor_z < 0.4 m`：14,967 trial，**0 成功**，100% `planning_no_solution`；`0.4–0.5 m`：1,041 trial，0 成功。
- pick 目标 `anchor_z < 0.4 m`：2,598 trial，0 成功；`≥1.4 m`：192 trial，0 成功。
- 这些 trial 的最小 IK 位置误差中位数约 8.5 cm（open z<0.5），不是 5 mm 容差问题，而是目标位姿整体不可达。
- `sample_stations` 目前只检查初始碰撞、地面和可选可见性，不检查目标高度、臂展包络或底座净空。

### 1.4 open 执行阶段的参考跟踪超时

- 已执行 open 8,632 条中，3,979 条（46.1%）终止于 `pregrasp` 的 `robocasa_reference_tracking_timeout`；另有 `approach` 779 条（9.0%）、`open` 475 条（5.5%）。
- 与高度相关：z 0.4–0.7 为 62.6%，z 0.7–1.0 为 43.5%，z≥1.6 为 28.3%。
- 与 handle 绑定相关：handle 正确的 21 条已执行 trial 中只有 2 条超时（9.5%）；空/错为 45.7%/49.9%，但 correct 样本很小。
- 单条超时 trial 的轨迹（`no-edit-pilot-c000/r000278`）显示：273 tick 内底座 xy 仅移动 1.2 cm，末端离把手仍有 1.46 m，参考点没有被跟踪上。控制侧原因需要单独回放诊断，不能继续归入规划失败。

## 2. 证据

### 2.1 主 cohort 总体

| 指标 | pick | open | 合计 |
|---|---:|---:|---:|
| trial | 23,178 | 35,136 | 58,314 |
| success | 209 (0.90%) | 1,472 (4.19%) | 1,681 (2.88%) |
| 已执行 trial | 6,794 | 8,632 | 15,426 |
| 已执行成功率 | 3.08% | 17.05% | 10.90% |

| 状态 | 数量 | 占比 |
|---|---:|---:|
| planning_no_solution | 42,888 | 73.55% |
| failure | 11,382 | 19.52% |
| episode_error | 1,898 | 3.25% |
| success | 1,681 | 2.88% |
| timeout | 465 | 0.80% |

### 2.2 失败原因（trial 级）

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

### 2.3 绑定状态 × 成功率

| 操作 | 绑定状态 | trial | success | 成功率 | 已执行 | 已执行成功率 |
|---|---|---:|---:|---:|---:|---:|
| pick | target correct | 384 | 53 | 13.80% | 133 | 39.85% |
| pick | target wrong | 14,508 | 112 | 0.77% | 4,124 | 2.72% |
| pick | target empty | 8,256 | 44 | 0.53% | 2,525 | 1.74% |
| pick | target mixed | 30 | 0 | 0.00% | 12 | 0.00% |
| open | handle correct | 117 | 12 | 10.26% | 21 | 57.14% |
| open | handle wrong | 4,096 | 168 | 4.10% | 1,003 | 16.75% |
| open | handle empty | 30,923 | 1,292 | 4.18% | 7,608 | 16.98% |

> `target_world` 为 `target_bodies - handle_bodies`；open 的判定使用 `handle_world`。绑定状态是 trial 内所有 query 的主状态，`preclose` 与 `pose` 可能不同，精确到阶段的统计见 2.6。

### 2.4 cup30 对照实验

| run | 目标数 | trial | success | 成功率 | 绑定状态 |
|---|---:|---:|---:|---:|---|
| exp-cup30-only-20261027-1542 | 1（Cup_30） | 60 | 37 | **61.67%** | correct 60 |
| exp-cup30-last-20261027-1542 | 4（各 1） | 2 | 1 | 50.00% | correct 2 |
| exp-cup30-last2-20261027-1600 | 4（各 1） | 59 | 2 | **3.39%** | empty 39 / wrong 17 / correct 3 |

> 两个 run 的 station 半径均为 1.0 m、执行配置相同；差别是单目标时复用永远落在同一任务内，多目标时跨任务复用触发错绑。`last2` 扫描时仍在追加。

### 2.5 目标高度分层

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

### 2.6 底座间隙 `clearance_base_m`

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

> `clearance_base_m` 是底盘半径 0.35 m 的网格净空，负值表示该半径下与场景重叠，但站位仍可能通过实际机器人初始碰撞检查。全 cohort 负间隙占比 44.39%。

### 2.7 执行阶段构成

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

### 2.8 goalset 失败的主导约束项

对每个 goalset 失败 trial，取 solver trace 中 `selected_link_costs` 最大的约束项/连杆：

| 操作 | 失败类型 | n | 主导项 | 主导连杆 |
|---|---|---:|---|---|
| pick | IK 约束不可行 | 8,588 | scene_collision 8,580 (99.9%) | omron_base 8,183 (95.3%) |
| pick | TrajOpt 不可行 | 5,604 | scene_collision 5,194 (92.7%) | omron_base 5,097 (90.9%) |
| pick | IK 不可达 | 2,219 | scene_collision 2,205 (99.4%) | omron_base 2,151 (96.9%) |
| open | IK 约束不可行 | 4,538 | scene_collision 4,530 (99.8%) | omron_base 4,422 (97.4%) |
| open | TrajOpt 不可行 | 7,444 | scene_collision 7,177 (96.4%) | omron_base 6,965 (93.6%) |
| open | IK 不可达 | 14,522 | scene_collision 14,498 (99.8%) | omron_base 14,491 (99.8%) |

> 该表来自上一份同口径扫描（n 略小于本版主 cohort），比例稳定。成功 trial 也可能带有残余场景代价，因此“主导项”只用于比较失败内部，不作为可行性判据。

## 3. 建议（按性价比）

1. **修复 planner 复用的目标/把手绑定**：在 `rebind_resources()` 中重算
   `target_bodies`/`handle_bodies` 并重建 target/handle checker，或把 scene identity 纳入
   `PlannerResources` key。pick 的 lift 失败和 `IndexError` 会直接消失。
2. **`attach_target_geometry()` 对空 checker 显式失败**，归类为基础设施/未知，而不是让
   `IndexError` 逃逸成 episode_error。
3. **站位采样增加可达性预筛**：目标高度带（open 先排除 <0.5 m；pick 排除 <0.4 m 和 ≥1.4 m）、
   底座净空（优先 `clearance_base_m ≥ 0.1 m`）和臂展包络。先用历史站位离线验证阈值，再改采样；
   这会显著减少无效 trial，但不改变“困难 S0”的定义。
4. **单独诊断 open 的 pregrasp 跟踪超时**：用回放对比指令与实测位姿，确认是底座被挡、参考轨迹
   不可达还是控制增益问题；不要把它继续混在“规划失败”里。
5. **报告口径**：goalset / execution / infrastructure 分开；绑定正确子集单独报告；不要用
   trial 级“是否新建 planner”代替 query 级绑定状态。

## 4. 限制

- 本分析基于既有产物，未重跑修复后的实验；除 cup30 对照外没有随机化，绑定正确的子集不是随机样本。
- `clearance_base_m` 是保守底盘半径的网格指标，不等于 cuRobo 底座球的实际净空。
- 执行跟踪超时只做了 1 条失败 + 1 条成功的轨迹对照，不能代表全部超时。
- `no-edit-val-all-c006` 未产出，主 cohort 仍不是完整 val 集；`exp-cup30-last2` 仍在追加。
