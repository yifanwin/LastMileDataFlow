# 实验报告：跨任务 planner 复用导致 Cup_30 成功率崩塌

- 状态：done
- 日期：2026-10-27
- 替代：无
- 关联：[成功率归因](01-low-success-analysis.md)、[planner 复用缺陷分析](03-planner-reuse-target-binding.md)、[全量统计](02-all-runs-trial-statistics.md)

## 1. 目的

验证 [planner 复用缺陷分析](03-planner-reuse-target-binding.md) 的核心假设：

> `V2Planner` 跨任务复用时不重算 `target_bodies` / `handle_bodies`，导致后续任务的 target-only 碰撞世界错绑或为空，从而把高成功率任务打成低成功率。

已知 `val103 / Cup_30` 是高成功率任务。本实验让它在同一队列中最后运行，前面先跑几个其它任务，观察成功率是否崩塌。

## 2. 实验设计

| 项目 | 对照组 | 实验组 |
|---|---|---|
| run-id | `exp-cup30-only-20261027-1542` | `exp-cup30-last2-20261027-1600` |
| GPU | 7 | 6 |
| 队列 | 仅 house 103 Cup_30 | house 97 soapdispenser → 92 vase → 88 peppershaker → **103 Cup_30（最后）** |
| 任务选择 | — | seed 20261027，从 c004/c005 中有成功记录的任务随机取 3 个不同类别 |
| 每任务目标数 | 1 | 1 |
| 标配 | 最多 20 站、每站 3 trial、无场景编辑 | 同左 |
| 并发 | `--planning-workers-per-gpu 1 --station-workers 1 --cpu-workers 1 --preparation-workers 1 --build-workers 1` | 同左 |

单 planning worker + 串行，保证 Cup_30 命中的 planner 实例已经在前面任务中创建；对照组没有前置任务，planner 在 Cup_30 上新建。

配方：`configs/scene_datasets/no_edit_val/exp_cup30_last.json`、`configs/scene_datasets/no_edit_val/exp_cup30_only.json`。

> 说明：实验组第一次运行 `exp-cup30-last-20261027-1542` 在 15:47 被外部信号终止（exit 144，无 traceback），只留下 3 条 trial 和 house 92 的首个 batch 规划；本报告使用完整跑完的 `last2`。旧 run 同样出现了 house 92 的 `target_world=[]`，与本报告结论一致。

## 3. 结果总览

| 指标 | 对照组 | 实验组 |
|---|---:|---:|
| 总 trial | 60 | 132 |
| success | 37 | **2** |
| trial 成功率 | **61.7%** | **1.5%** |
| Cup_30 trial | 60 | 60 |
| Cup_30 success | 37 | **0** |
| Cup_30 成功率 | **61.7%** | **0.0%** |

同一任务、同一场景、同一执行配置、同一 3 trials/站，唯一差别是队列位置与 planner 复用轨迹。

## 4. 逐任务结果

### 4.1 对照组（无前置任务）

| 任务 | trial | success | 失败构成 |
|---|---:|---:|---|
| house 103 Cup_30 | 60 | 37 (61.7%) | 21 未执行规划无解；1 pregrasp 跟踪超时；1 lift 规划无解 |

站位分布：11 站 3/3、2 站 2/3、7 站 0/3。

### 4.2 实验组（Cup_30 最后）

| 顺序 | 任务 | trial | success | 失败构成 |
|---:|---|---:|---:|---|
| 1 | house 97 soapdispenser | 3 | 2 (66.7%) | 1 failure |
| 2 | house 92 vase | 36 | 0 | 19 未执行规划无解；17 `lift_actual` IndexError |
| 3 | house 88 peppershaker | 33 | 0 | 9 未执行规划无解；22 `lift_actual` 规划无解；2 跟踪超时 |
| 4 | house 103 Cup_30 | 60 | **0** | 21 未执行规划无解；38 `lift_actual` IndexError；1 pregrasp 跟踪超时 |

第一个任务自身正常（2/3）；从第二个任务开始全部归零。

### 4.3 target_world 绑定类型

对照组 Cup_30 的 20 个 batch query：**20 correct**。

实验组 44 个 batch query（每站一个，3 trial 共享）：

| 任务 | query 数 | correct | empty | wrong |
|---|---:|---:|---:|---:|
| house 97 soapdispenser（首个，planner 新建） | 1 | 1 | 0 | 0 |
| house 92 vase（复用） | 12 | 0 | 12 | 0 |
| house 88 peppershaker（复用） | 11 | 0 | 1 | 10（几何为 `chestofdrawers`） |
| house 103 Cup_30（复用） | 20 | 0 | **20** | 0 |

即：planner 第一次创建时绑定的 house 97 目标仍正确；其余任务全部错绑。Cup_30 的 20 个站位全部拿到空目标世界。

## 5. 热图

### 5.1 Cup_30：对照组 vs 实验组

对照组（61.7%，11 个站位 3/3）：

![对照组 Cup_30 热图](../../../outputs/experiments/cup30/exp-cup30-only-20261027-1542/figures/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-no_edit-v000/success_heatmap_stations.svg)

实验组（0%，最后一个任务）：

![实验组 Cup_30 热图](../../../outputs/experiments/cup30/exp-cup30-last2-20261027-1600/figures/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-no_edit-v000/success_heatmap_stations.svg)

### 5.2 实验组前置任务

house 97 soapdispenser（首个，2/3）：

![house 97 热图](../../../outputs/experiments/cup30/exp-cup30-last2-20261027-1600/figures/val97-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_2-no_edit-v000/success_heatmap_stations.svg)

house 92 vase（0/36）：

![house 92 热图](../../../outputs/experiments/cup30/exp-cup30-last2-20261027-1600/figures/val92-pick-vase_492808e89eee9bf3081c206f6cb82173_1_0_2-no_edit-v000/success_heatmap_stations.svg)

house 88 peppershaker（0/33）：

![house 88 热图](../../../outputs/experiments/cup30/exp-cup30-last2-20261027-1600/figures/val88-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_6-no_edit-v000/success_heatmap_stations.svg)

热图由 `scripts/export_no_edit_heatmaps.py` 离线导出，没有新增物理步。

## 6. 结论

1. **现象复现**：同一 Cup_30 从单独运行的 37/60（61.7%）降到队列最后的 0/60，且从第二个任务开始前置任务也全部归零。
2. **机制吻合**：实验组 Cup_30 的 20 个 batch query 的 `target_world` 全部为空；对照组 20 个全部正确。失败环节集中在 `lift_actual`：38 条 `IndexError: list index out of range`，与 `attach_target_geometry()` 取空 `target_checker.scene_model.mesh[0]` 的路径一致。
3. **首个任务不受影响**：house 97 是 planner 的创建者，`target_bodies` 在其自身的 sim 上计算，故仍为 correct。
4. 因此，跨任务复用时未重建 target/handle 绑定，足以把一个 60%+ 的任务打成 0%，并解释全量数据中 pick 的大量 goalset 约束失败与 lift 失败。

## 7. 限制

- 单 seed（42）、每站 3 trial；结论依赖的是“61.7% → 0%”这一量级差异，不是精细的统计置信区间。
- 实验组只有 4 个任务，前置任务的选择虽固定 seed，但样本量小。
- 对照组与实验组不在同一时刻运行，GPU 竞争不同；但两者的失败模式差异（target_world empty/wrong vs correct）是结构性的，不受运行速度影响。
- 本报告不包含修复后的验证；修复 `rebind_resources()` 后应重跑同一实验以确认 Cup_30 恢复到对照组水平。
