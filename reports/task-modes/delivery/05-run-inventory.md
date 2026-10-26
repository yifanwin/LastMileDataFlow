# task-modes 运行产物总览（outputs/diagnostics/task-modes）

- 状态：active
- 日期：2026-10-26
- 替代：10-run-inventory.md
- 关联：[标定交付](02-calibration-delivery.md)、[下游与验证](03-downstream-delivery.md)、[底盘修复](04-mobile-base-hold-fix.md)、[构建计划表](../plan/02-build-schedule.md)

## 目录

[1. 整理口径](#1-整理口径) · [2. 总览](#2-总览) · [3. 候选构建](#3-候选构建build-scenes) · [4. 完整标定](#4-完整标定evaluate-stations) · [5. 下游](#5-下游train-采集与-benchmark) · [6. 环境与单点验证](#6-环境策略与单点验证) · [7. 关键结论](#7-关键结论) · [8. 需复核项](#8-需复核项) · [9. 产物读取入口](#9-产物读取入口) · [10. run 清单](#10-附run-清单) · [11. 目录整理记录](#11-目录整理记录2026-10-26)

## 1. 整理口径

- 整理前共 **62 个 run**，另有一个空的 `dependencies/pi05-cache` 缓存目录；合计约 **11 GB**。本次清除了 2 个重复 run、1 个重复 build 目录和空缓存，当前为 **60 个 run**，详见第 11 节。
- 除第 11 节列出的删除项外，未移动或改写任何产物。数字直接取自各 run 的 `summary.json`、`trials.jsonl`、`diagnosis.jsonl`、`scenes.jsonl`、`episodes.jsonl`、`results.jsonl`，未从日志或平滑图反推。
- 目录级分类索引见 `outputs/diagnostics/task-modes/README.md`（该目录被 Git 忽略，长期文档以本文件为准）。
- "修复前 / 修复后"以 2026-10-26 12:27 的 [p4-evidence-20261026-r03](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r03) 为界；该轮验证了 RoboCasa 闭爪阶段底盘保持修复。修复前结果全部保留，但**不能作为修复后任务固有困难的证据**。
- "完整标定"指计划内全部 trial 均有记录；0 物理步的 `planning_no_solution` 对应 `task_outcome=unknown`、`execution_termination=not_executed`，**不并入任务失败**；另有少量 trial 在执行到后续阶段后规划无解，已有真实物理步且 `task_outcome=failure`，计入任务失败。
- 本文件补充了 02 号标定文档之后新产生的下游 run（p5/p6 系列），不改变用户"本轮止于标定"的范围决定。
- `p6-videos-20261026-r01/` 是从已有 rollout 离线回放的 10 段诊断视频，不是新的实验 run，不计入 60 个 run；清单见该目录 [README.md](../../../outputs/diagnostics/task-modes/p6-videos-20261026-r01/README.md)。

## 2. 总览

| 阶段 | run 数 | 主要内容 | 当前结论 |
|---|---:|---|---|
| P0–P2 基线/几何/构建主干 | 5 | 计时基线、资产索引、共享上下文、三相机 | 软件与 CPU/GPU 事务证据成立；无任务 rollout |
| P3 候选构建 | 6（整理前 8） | 八类 Builder、并行构建、公开编排 | 五类有接受正例；Visibility/Arm-path/Approach 为负结果 |
| P4 标定与诊断 | 19 | 16 个标定/诊断 run + IK probe + 2 个准备测量 | 修复后 4 批完整标定；六类标签真实正例 |
| P5 下游 | 8 | train 采集/物化、val benchmark | 训练数据 12 条 S1 直接示教；benchmark 仅 1 个可评测 episode |
| P6 扩展验证 | 22 | 补充构建、Visibility/Open 搜索、设备/策略/回放 smoke | 环境与策略接口通过；Visibility 仍无正例 |

修复后的核心数字：**222 条完整 trial，63 条真实成功**（34 + 12 + 14 + 3），invalid_trials=0；任务失败 77 条，其中 46 条为执行阶段失败、31 条为执行到后续阶段后规划无解；另有 82 条在 0 物理步时即规划无解（`not_executed`）。

## 3. 候选构建（build-scenes）

`accepted` 只表示合法候选构建完成，不是最终模式标签。全部 run 的主记录为 `scene_datasets/build/scenes.jsonl`。

| run | 来源 / 任务 | 模式尝试 | 接受/拒绝/跳过 | wall s | 接受的意图 |
|---|---|---:|---:|---:|---|
| [p3-cup30-20261026-r03](../../../outputs/diagnostics/task-modes/p3-cup30-20261026-r03) | val103 / Cup_30 | 8 | 4/3/1 | 6.3 | reachability、ground、narrow、orientation |
| [p3-open-20261026-r01](../../../outputs/diagnostics/task-modes/p3-open-20261026-r01) | train house0 / Safe Open | 8 | 2/6/0 | 25.1 | reachability、articulated |
| [p3-public-20261026-r02](../../../outputs/diagnostics/task-modes/p3-public-20261026-r02) | train house0 / Potato | 8 | 2/5/1 | 15.9 | reachability、orientation |
| [p3-spatial-20261026-r01](../../../outputs/diagnostics/task-modes/p3-spatial-20261026-r01) | train / Alarm_Clock | 8 | 2/5/1 | 74.4 | reachability（同支撑面 MoveTarget）、orientation |
| [p3-parallel-20261026-r01](../../../outputs/diagnostics/task-modes/p3-parallel-20261026-r01) | Cup_30 + Potato，双 worker、两变体 | 10 | 8/0/2 | 17.0 | reachability×4、orientation×4 |
| [p6-bottle-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-bottle-build-20261026-r01) | 4 个 bottle 任务 | 40 | 13/22/5 | 98.9 | reachability×5、orientation×5、arm_path×2、narrow×1 |
| [p6-high-open-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-high-open-build-20261026-r01) | val/train Dresser 抽屉 | 2 | 2/0/0 | 16.8 | articulated×2 |
| [p6-obstruction-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-obstruction-build-20261026-r01) | house 1、2 | 12 | 0/12/0 | 59.2 | — |
| [p6-obstruction-build-20261026-r02](../../../outputs/diagnostics/task-modes/p6-obstruction-build-20261026-r02) | house 3、4 | 18 | 1/17/0 | 18.9 | approach×1 |
| [p6-obstruction-build-20261026-r03](../../../outputs/diagnostics/task-modes/p6-obstruction-build-20261026-r03) | house 5–9 | 36 | 0/36/0 | 128.3 | — |
| [p6-soap-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-soap-build-20261026-r01) | 3 个 soap 任务 | 24 | 4/17/3 | 48.4 | reachability×3、orientation×1 |
| [p6-visibility-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-visibility-build-20261026-r01) | val103 / Cup_30 | 2 | 0/2/0 | 2.7 | — |

补充：

- `p3-cup30` 保留 r03（实际标定来源）；r01 与 r03 的 `scenes.jsonl` 字节相同，r02 是 orientation 站位不同的早期变体，二者均已按重复/被替代产物删除。
- 拒绝原因的集中项是 `no_station_pair_for_nominal_channels`、`no_navigable_valid_view_pair`、`no_navigable_station_pair`、`asset_pool_empty:<role>`，以及 Visibility 的 `no_aggregate_visibility_contrast`。
- Visibility 专项搜索：[p6-visibility-search-r01](../../../outputs/diagnostics/task-modes/p6-visibility-search-20261026-r01) 对 cup（10 个合法可导航视角）和 spray（8 个）均未找到自然低/高可见对照；[p6-visibility-search-r02](../../../outputs/diagnostics/task-modes/p6-visibility-search-20261026-r02) 检查 8 个候选，全部 `no_aggregate_visibility_contrast`（S0 1.0，S1 0.96875–1.0）。这是**工程负结果**，不是实现缺失。
- [p6-open-targets-r01](../../../outputs/diagnostics/task-modes/p6-open-targets-20261026-r01) 从 4 个 house 选出实际较高把手（Dresser_220_1 的显式把手、Microwave、Fridge 等），纠正了此前 Safe 锚点仅 0.153 m 的问题。

## 4. 完整标定（evaluate-stations）

### 4.1 全量汇总

| run | 修复 | 计划/记录 | 成功 | 执行后失败 | 未执行(规划无解) | 物理步 | wall s | 资格 / 标签 |
|---|---|:--:|---:|---:|---:|---:|---:|---|
| [p4-evidence-r01](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r01) | 前 | 3/3 | 0 | 0 | 0 | 0 | 5.3 | 3 条 CUDA infrastructure_error，incomplete |
| [p4-evidence-r02](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r02) | 前 | 3/3 | 0 | 3 | 0 | 1,128 | 70.8 | incomplete |
| [p4-evidence-r03](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r03) | 后 | 3/3 | **2** | 1 | 0 | 1,039 | 66.3 | incomplete（仅 3/60） |
| [p4-cup-full-r01](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r01) | 前 | 60/60 | 2 | 37 | 21 | 14,186 | 340.7 | no_high_rate_s1 |
| **[p4-cup-full-r02](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r02)** | 后 | 60/60 | **34** | 5 | 21 | 13,147 | 350.2 | eligible；arm_path×7、narrow×7；7 probe |
| [p4-open-full-r01](../../../outputs/diagnostics/task-modes/p4-open-full-20261026-r01) | 前 | 51/51 | 0 | 0 | 51 | 0 | 56.7 | no_success |
| [p4-potato-full-r01](../../../outputs/diagnostics/task-modes/p4-potato-full-20261026-r01) | 前 | 15/15 | 0 | 0 | 15 | 0 | 25.5 | no_success |
| [p4-clock-full-r01](../../../outputs/diagnostics/task-modes/p4-clock-full-20261026-r01) | 前 | 24/24 | 0 | 0 | 24 | 0 | 27.5 | no_success（2 任务） |
| [p4-safe-full-r01](../../../outputs/diagnostics/task-modes/p4-safe-full-20261026-r01) | 前 | 45/45 | 0 | 0 | 45 | 0 | 59.9 | no_success |
| [p4-bottle-full-r01](../../../outputs/diagnostics/task-modes/p4-bottle-full-20261026-r01) | 前 | 60/60 | 2 | 35 | 23 | 13,515 | 526.1 | 2×no_high_rate_s1、2×no_success；无标签 |
| [p4-soap-full-r01](../../../outputs/diagnostics/task-modes/p4-soap-full-20261026-r01) | 前 | 30/30 | 0 | 11 | 19 | 3,826 | 225.7 | 4×no_success；无标签 |
| [p4-high-open-full-r01](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r01) | 前 | 60/60 | 0 | 34 | 26 | 12,327 | 513.2 | 2×no_success |
| **[p4-high-open-full-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02)** | 后 | 60/60 | **14** | 20 | 26 | 17,548 | 1,267.2 | 2×eligible、intent_hit×2；15 S0；12 probe |
| **[p4-train-bottle-full-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02)** | 后 | 42/42 | **12** | 11 | 19 | 8,109 | 480.3 | atomizer reachability eligible + intent_hit；8 S0；8 probe |
| **[p4-yaw-full-r02](../../../outputs/diagnostics/task-modes/p4-yaw-full-20261026-r02)** | 后 | 60/60 | **3** | 41 | 16 | 13,874 | 536.8 | 1 eligible、1 no_high_rate_s1、1 no_success；无标签 |
| [p6-independent-calibration-r01](../../../outputs/diagnostics/task-modes/p6-independent-calibration-20261026-r01) | 后 | 3/60 | 3 | 0 | 0 | 1,311 | 181.1 | incomplete；S0013 3/3；`shared_station_preparation=false` |

口径说明：按 trial 的 `reason` 字段统计，`planning_no_solution` 共出现 **113** 次；其中 **82** 次为 0 物理步、未执行（表中"未执行"列），另 **31** 次发生在执行到 `open` / `lift_actual` 等后续阶段后（已计入"执行后失败"列）。四个修复后 run 的分项：Cup 0 条、Bottle 4 条、Open 4 条、Yaw 23 条。

Cup r01/r02、Open r01/r02、Bottle r02 的关键轨迹已渲染为 10 段对比视频，见 [4.5 已渲染视频](#45-已渲染视频10-段)；另 2 段成功代表见 [4.6](#46-补充成功片段2-段)。

[p4-classify-error-r01](../../../outputs/diagnostics/task-modes/p4-classify-error-20261026-r01) 是诊断-only run：直接引用旧 r01 的 3 条基础设施错误，输出 1 个 incomplete、无标签、probe_count=0，不复制 trial。

### 4.2 修复后四批的逐站成功率（成功/尝试）

| run | 逐站结果 |
|---|---|
| [p4-cup-full-r02](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r02) | S0001 3/3、S0006 2/3、S0007 2/3、S0013 2/3、S0014 0/3、S0020 3/3、S0021 3/3、S0022 0/3、S0029 3/3、S0030 0/3、S0036 3/3、S0037 2/3、S0038 0/3、S0040 0/3、S0041 0/3、S0042 0/3、S0043 3/3、S0044 3/3、S0045 2/3、S0048 3/3 |
| [p4-train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) | S0000 0/6、S0005 0/3、S0029 0/3、S0036 0/3、S0037 0/3、S0038 0/3、S0042 0/3、S0043 1/3、S0044 1/3、S0045 2/3、S0046 2/3、S0047 3/3、S0048 3/3 |
| [p4-high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) | S0002 0/3、S0003 0/3、S0004 0/3、S0005 2/3、S0009 0/3、S0010 0/3、S0011 1/3、S0012 1/3、S0013 0/3、S0014 0/3、S0020 1/3、S0021 2/3、S0022 0/3、S0029 2/3、S0030 0/3、S0036 2/3、S0037 1/3、S0043 2/3、S0048 0/6 |
| [p4-yaw-r02](../../../outputs/diagnostics/task-modes/p4-yaw-full-20261026-r02) | S0000 0/3、S0029 0/3、S0036 0/3、S0037 0/3、S0038 0/3、S0042 0/3、S0043 0/9、S0043-Y01 0/3、S0043-Y02 1/3、S0044 2/9、S0045 0/3、S0045-Y01 0/3、S0045-Y02 0/3、S0046 0/3、S0047 0/3、S0048 0/3 |

说明：同一 station ID 出现次数大于 3，表示多个任务或变体共用了该 ID（如 S0000 0/6、S0043 0/9、S0044 2/9），分母按实际 trial 合并。Cup 的低率站为 0/3 共 7 个、高率站为 2/3 或 3/3 共 13 个；Open 没有 3/3 站点，最高为 2/3。

### 4.3 诊断结果（diagnosis.jsonl）

| run / 任务 | S0（成功率） | 命中标签（S0 数） | 证据 S1 | intent_hit | probe |
|---|---|---|---:|---:|---:|
| [cup-r02](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r02) / ground_obstacle | 7（全部 0/3） | arm_path 7、narrow 7 | arm_path→S0001；narrow→S0020（S0042 为 S0001） | false | 7 |
| [train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) / atomizer reachability | 8（7×0/3 + S0044 1/3） | reachability 2、arm_path 3、narrow 4、ground 1、approach 1 | 全部→S0047（ground 仅 S0047） | **true** | 8 |
| [train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) / pepper arm_path | 0 | — | — | false | 0 |
| [high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) / val1 抽屉 | 7（5×0/3 + S0020、S0037 1/3） | articulated 7、arm_path 5、narrow 6、approach 1 | →S0021 | **true** | 7 |
| [high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) / train 抽屉 | 8（6×0/3 + S0011、S0012 1/3） | articulated 8、arm_path 4、reachability 1 | →S0005 | **true** | 5 |
| [yaw-r02](../../../outputs/diagnostics/task-modes/p4-yaw-full-20261026-r02) / reachability | 1（S0043 0/3） | — | — | false | 0 |
| [yaw-r02](../../../outputs/diagnostics/task-modes/p4-yaw-full-20261026-r02) / orientation ×2 | 0 | — | — | false | 0 |

- 六类真实正例：**reachability、ground_obstacle、arm_path_collision、narrow_space、approach、articulated_interaction**。
- **Orientation、Visibility 无正例**：yaw 对照未形成合格高低率关系；Visibility 搜索与构建均未通过代理对照。
- Cup 的构造意图是 Ground，但实际命中 Arm-path + Narrow，`intent_hit=false`；Train bottle 中 Pepper 编辑候选无成功（no_success），Spray/Atomizer 候选命中 Reachability 等多种标签；Open 的 Articulated 意图命中，S1 均有 `physical_open` 真实关节证据。

### 4.4 修复前后对比

| 场景 | 修复前 | 修复后 | 变化 |
|---|---:|---:|---|
| Cup_30 完整 60 条 | 2 成功 | 34 成功 | +32 |
| 显式把手 Open 完整 60 条 | 0 成功 | 14 成功 | +14 |
| 相同 S0013 / seed 42–44 | 0/3 | 2/3（r03） | +2 |
| Bottle 完整（60 vs 42 条，任务集不同） | 2 成功 | 12 成功 | 不能直接相减 |
| Yaw 对照 60 条 | —（修复前未跑） | 3 成功、无高率 S1 | Orientation 仍无正例 |

修复内容见 [04-mobile-base-hold-fix.md](04-mobile-base-hold-fix.md)：`runtime/execution/robocasa_tracking.py` 在闭爪阶段未同步最后规划底盘目标，导致底盘回退约 0.32 m；修复后闭爪阶段底盘漂移降到 ~1e-7 m。**修复前的失败率不能用作任务固有难度。**

### 4.5 已渲染视频（10 段）

从 Cup r01/r02、Open r01/r02、Bottle r02 的已有 rollout 离线回放；三宫格为 `robot0_agentview_left`、`robot0_eye_in_hand`、`robot0_agentview_right`（各 256×256 + 标签栏，768×278），20 fps，`new_physical_steps=0`。每段目录也保留三路单独 mp4。索引见[三相机视频 README](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/README.md)。

| # | 视频路径 | 角色 | 来源 run | rollout | 站点 | 结果 | 步数 |
|---:|---|---|---|---|---|---|---:|
| 1 | [fix-cup-pre/r000000](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/fix-cup-pre/r000000/triview.mp4) | Cup 修复前 | [p4-cup-full-r01](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r01) | r000000 | S0013 | failure `lift_contact_hold_or_slip` | 469 |
| 2 | [fix-cup-post/r000000](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/fix-cup-post/r000000/triview.mp4) | Cup 修复后 | [p4-cup-full-r02](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r02) | r000000 | S0013 | success `mobile_pick` | 421 |
| 3 | [fix-open-pre/r000015](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/fix-open-pre/r000015/triview.mp4) | Open 修复前 | [p4-high-open-r01](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r01) | r000015 | S0021 | failure `open_no_grip` | 545 |
| 4 | [fix-open-post/r000015](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/fix-open-post/r000015/triview.mp4) | Open 修复后 | [p4-high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) | r000015 | S0021 | success `physical_open` | 875 |
| 5 | [bottle-modes/r000020](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/bottle-modes/r000020/triview.mp4) | Ground S0 | [p4-train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) | r000020 | S0036 | failure `tracking_timeout` | 497 |
| 6 | [bottle-modes/r000014](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/bottle-modes/r000014/triview.mp4) | Approach S0 | [p4-train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) | r000014 | S0044 | failure `tracking_timeout` | 523 |
| 7 | [bottle-modes/r000038](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/bottle-modes/r000038/triview.mp4) | S1（5/6 的对照） | [p4-train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) | r000038 | S0047 | success `mobile_pick` | 350 |
| 8 | [open-modes/r000014](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/open-modes/r000014/triview.mp4) | Arm-path / Narrow / Articulated S0 | [p4-high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) | r000014 | S0014 | failure `tracking_timeout` | 627 |
| 9 | [open-modes/r000040](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/open-modes/r000040/triview.mp4) | Articulated 失败 | [p4-high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) | r000040 | S0004 | failure `open_contact_lost` | 594 |
| 10 | [open-train-success/r000046](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/open-train-success/r000046/triview.mp4) | Articulated S1（train） | [p4-high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) | r000046 | S0005 | success `physical_open` | 603 |

对比关系：1↔2 Cup 修复前后；3↔4 Open 修复前后；5、6→7 Ground/Approach；8→4 为 Open val1 的 Arm-path/Narrow/Articulated；9↔10 为 Open train Articulated。

未渲染 Reachability：所有 S0 都是 0 物理步的 `planning_no_solution`，没有可回放运动；Cup r02 的 7 个低率 S0 同样为 0 步，因此 Cup 失败片段只能使用修复前 r01。

并行会话另实现了 `--replay-layout grid`（三相机 + 俯视底盘轨迹，512×626），已有 `grid-cup`、`grid-open` 两条示例；本表为纯三相机版本。

### 4.6 补充成功片段（2 段）

用户要求为两个完整标定 run 各补一条成功代表，均未在前 10 段中重复；三相机布局同 4.5。

| # | 三相机视频 | 来源 run | rollout | 站点 | 结果 | 步数 |
|---:|---|---|---|---|---|---:|
| 11 | [high-open-success/r000016](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/high-open-success/r000016/triview.mp4) | [p4-high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02) | r000016 | S0021 | success `physical_open`；关节进展 0.10644 m，保持 1.458 s | 684 |
| 12 | [bottle-success/r000040](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/bottle-success/r000040/triview.mp4) | [p4-train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) | r000040 | S0048 | success `mobile_pick`；最大抬升 0.09943 m，保持 2.006 s | 381 |

## 5. 下游（train 采集与 benchmark）

### 5.1 train

| run | 输入 | 结果 |
|---|---|---|
| [p5-train-negative-r01](../../../outputs/diagnostics/task-modes/p5-train-negative-20261026-r01) | Potato reachability（no_success） | planned/processed/executed pairs = 0，空 train_index |
| [p5-train-collect-r01](../../../outputs/diagnostics/task-modes/p5-train-collect-20261026-r01) | p6-bottle-build + [p4-train-bottle-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02) | 13 对：10 对 `astar_no_path`、3 对实际执行均 `robocasa_reference_tracking_timeout`；train_index 12 条 |
| [p5-train-materialized-r01](../../../outputs/diagnostics/task-modes/p5-train-materialized-20261026-r01) | p5-train-collect 的 12 条 | 12 条全部物化：`observation_complete=true`、`measured_state_replay`、与源 attempt 一一对应 |

- 12 条训练条目全部是 `s1_direct_operation`（atomizer 任务 S0043–S0048 的成功操作），**没有成功的 S0→S1 连续配对**。
- 3 条实际执行的连续配对均为跟踪超时；10 条在 A* 阶段即无路。导航问题是当前下游覆盖率的主要限制。

### 5.2 val benchmark

| run | 标定来源 | 站点 | 低率/高率 | candidate → eligible → written | 排除原因 |
|---|---|---|---:|---:|---|
| [p5-negative-bench-r01](../../../outputs/diagnostics/task-modes/p5-negative-bench-20261026-r01) | p4-classify-error（incomplete） | 20 | — | 0 → 0 → 0 | 20 站 incomplete |
| [p5-full-negative-bench-r01](../../../outputs/diagnostics/task-modes/p5-full-negative-bench-20261026-r01) | [cup-r01](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r01)（修复前） | 20 | 18 / 0 | 0 → 0 → 0 | no_high_rate_s1=18；旧 20% 阈值汇总，保留 |
| [p5-full-negative-bench-r02](../../../outputs/diagnostics/task-modes/p5-full-negative-bench-20261026-r02) | [cup-r01](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r01)（修复前） | 20 | 20 / 0 | 0 → 0 → 0 | no_high_rate_s1=20；修正为 ≤1/3 汇总 |
| [p5-mode-bench-r01](../../../outputs/diagnostics/task-modes/p5-mode-bench-20261026-r01) | **[cup-r02](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r02)（修复后）** | 20 | 7 / 13 | 7 → 0 → 0 | **no_navigation_path=7** |
| [p5-open-bench-r01](../../../outputs/diagnostics/task-modes/p5-open-bench-20261026-r01) | **[high-open-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02)（修复后）** | 11 | 7 / 4 | 7 → 1 → 1 | no_navigation_path=6 |

- 修复后 Cup 已有 13 个高率站位。原 run 的 7 个低率 S0 均记录 `no_navigation_path`；[专项复核](../../navigation/delivery/06-cup-navigation-review.md)确认七个起点全部被整机圆柱近似的膨胀栅格屏蔽，并非已经搜索证明真实场景无路。七条通往合格 S1 的候选路线通过了 363 个真实模型静态位姿检查；尚未验证动态跟踪，原 `7 → 0 → 0` 不变。
- Open 只得到 1 个可评测 episode（articulated_interaction）。

上述表格保留原 run 数字。后续[导航修复交付](../../navigation/delivery/07-navigation-geometry-fix.md)已完成：新 Cup benchmark 为 **7 → 2 → 2**，另外 4 个端点净空不足 2 cm、1 个有限搜索耗尽；新 Open 为 **7 → 7 → 7**；Train 原 13 对中 **9 对**找到满足余量的参考路线。四条独立物理导航（Cup×2、Open×1、Train×1）均到达、记录完整且禁用接触计数为 0；尚未执行完整 S0→S1→操作采集或新策略评测。新产物位于 `outputs/diagnostics/navigation-geometry-fix-*`，不计入本文件原 `task-modes/` 的 60 个 run。

后续用户撤销 20 mm 硬门槛，已完成[软净空实现](../../navigation/delivery/08-navigation-soft-clearance.md)：新 Cup/Open 均 **7/7**，Train **11/13** 对找到路线，五条实际导航通过。两条剩余 Train 配对在预算内没有完整候选，不声称真实空间无路；原表格和旧记录不回写，当前导航结果以独立专题交付为准。

### 5.3 val 策略评测

| run | 输入 | 结果 |
|---|---|---|
| [p6-open-bench-eval-r01](../../../outputs/diagnostics/task-modes/p6-open-bench-eval-20261026-r01) | p5-open-bench 的 e000000；π0.5（panda_omron，replan=5，horizon=400） | 1/1 执行，400 步，`policy_step_budget_exhausted`，task_outcome=failure；recording_complete=true；coverage=1.0、TSR=0.0、observed TSR=0.0；base 路径 1.043 m、末位移 0.388 m、末 yaw 变化 −0.508 rad；`effective_adjustment=true` |

这是目前唯一的模式困难 S0 策略评测结果，**n=1，不能用于难度结论**。

## 6. 环境、策略与单点验证

| run | 检查 | 实际结果 |
|---|---|---|
| [p0-baseline-r01](../../../outputs/diagnostics/task-modes/p0-baseline-20261025-r01) | CPU 准备计时 | source load/compile/init 2.593 s；settle 4.621 s；station clone ~0.018 s×3；nbody 436 / ngeom 3571 |
| [p1-assets-geometry-r01](../../../outputs/diagnostics/task-modes/p1-assets-geometry-20261025-r01) | 6 个代表资产 | 索引重载尺寸一致、模型复用；首次加载 0.004–0.028 s |
| [p2-context-r01](../../../outputs/diagnostics/task-modes/p2-context-20261026-r01) | val0 Potato CPU 准备与事务 | parking/settle 4.727 s、geometry 5.914 s、lattice 49；no-edit/pose 冻结重载一致；AddObject 接受；2 cm MoveTarget 拒绝 |
| [p2-three-camera-r01](../../../outputs/diagnostics/task-modes/p2-three-camera-20261026-r01) | 三相机测量 | 分割 ID 越界，失败记录保留 |
| [p2-three-camera-r02](../../../outputs/diagnostics/task-modes/p2-three-camera-20261026-r02) | 三相机测量 | left 183/183、wrist 748/748、right 140/170（0.8235），聚合 1.0；停车姿态聚合 null、known_max=0 |
| [p4-ik-probe-r01](../../../outputs/diagnostics/task-modes/p4-ik-probe-20261026-r01) | 独立 IK（16 grasp×32 seed） | 覆盖 512、可行 167、ratio 0.3262；active 4.64 s；plan_count=0、无新 trial |
| [p6-device-r01](../../../outputs/diagnostics/task-modes/p6-device-20261026-r01) | torch / JAX | torch 2.7.1+cu128、CUDA 12.8、Blackwell [12,0]、matmul 首元素 10416 正确；JAX 0.5.3 GPU 通过 |
| [p6-policy-observation-r01](../../../outputs/diagnostics/task-modes/p6-policy-observation-20261026-r01) | 真实观测转策略输入 | 16D 状态、3×256×256 RGB、state 不变、`simulation_started=false`、0 物理步 |
| [p6-pi05-control-r01](../../../outputs/diagnostics/task-modes/p6-pi05-control-20261026-r01) | RoboCasa 保持与 TCP 小步 | 关节漂移 8.66e-11 rad；TCP delta 约 0.0101 m / 3.05 s |
| [p6-pi05-tracking-r01](../../../outputs/diagnostics/task-modes/p6-pi05-tracking-20261026-r01) | 移动底盘 + TCP 参考跟踪 | 底盘误差 ~2.8e-4；TCP 位置误差 6.82e-4 m、旋转 9.61e-4 rad / 5 s |
| [p6-pi05-model-r01](../../../outputs/diagnostics/task-modes/p6-pi05-model-20261026-r01) | π0.5 真权重 | 加载 6.04 s、首推 8.31 s；actions 50×12、raw 50×32；输入为真实 observation.npz |
| [p6-pi05-rollout-r01](../../../outputs/diagnostics/task-modes/p6-pi05-rollout-20261026-r01) | π0.5 固定站位闭环 | 60 步、记录/观测完整；failure `policy_step_budget_exhausted`；`uncalibrated_fixed_station=true`，不作困难评测 |
| [p6-real-observation-replay-r01](../../../outputs/diagnostics/task-modes/p6-real-observation-replay-20261026-r01) | 成功轨迹三相机回放 | Cup r000010/S0029，328 帧×3 相机，21.79 s；`new_physical_steps=0`；属 val，未建训练索引 |
| [p6-tracking-accuracy-r01](../../../outputs/diagnostics/task-modes/p6-tracking-accuracy-20261026-r01) | 8 条 rollouts 精度 | approach 末端位置误差 0.81–8.0 mm；目标位移 ~1e-7 m；未调整容差 |
| [p6-open-grasp-view-r01](../../../outputs/diagnostics/task-modes/p6-open-grasp-view-20261026-r01) | Open 闭爪近景 | 边界 273、phase=close、0 新物理步 |
| [p6-shared-preparation-r01](../../../outputs/diagnostics/task-modes/p6-shared-preparation-20261026-r01) | 共享准备对照 | shared 11.589 s vs fresh 22.014 s（1.90×）；初始化 49 vs 98；站位/锚点一致；无标定或执行 |

### 6.1 关键轨迹视频

| 视频 | 内容 |
|---|---|
| [三相机视频（4.5 + 4.6）](../../../outputs/diagnostics/task-modes/p6-videos-triview-20261026-r01/README.md) | 10 段对比 + 2 段成功代表，left/wrist/right 三宫格，768×278 |
| [单相机与 grid 索引](../../../outputs/diagnostics/task-modes/p6-videos-20261026-r01/README.md) | 10 段单相机回放与 2 段 grid 示例 |
| [grid-cup.mp4](../../../outputs/diagnostics/task-modes/p6-videos-20261026-r01/replays/grid-cup/r000000/grid.mp4) | Cup r02 S0013 success `mobile_pick`，421 步；三相机 + 俯视 |
| [grid-open.mp4](../../../outputs/diagnostics/task-modes/p6-videos-20261026-r01/replays/grid-open/r000015/grid.mp4) | Open r02 S0021 success `physical_open`，875 步；三相机 + 俯视 |

生成接口为 `evaluate-stations --replay-layout grid --rollouts <id>`，说明见[运行手册](../../../docs/运行手册.md#panda-数值标定与离线回放)。视频内标签全部为 ASCII，不依赖中文字体。

## 7. 关键结论

1. P0–P4 主链已打通：候选构建 → 真实标定 → S0/S1 → 诊断 → 多标签。
2. 修复闭爪底盘回退后，完整标定从"几乎全失败"变为可用的成功率分布：Cup 34/60、Open 14/60、Bottle 12/42、Yaw 3/60。
3. 六类模式有真实标签正例；Orientation、Visibility 是明确的负结果，不能写成已验证。
4. 下游已产生 12 条 S1 直接操作训练数据（含三相机物化观测）和 1 个可评测的 Open episode；**没有成功的 S0→S1 连续示教**。
5. 原下游覆盖率受到导航模型限制：train 10/13 对、Cup bench 7/7、Open bench 6/7 被记录为无路。后续已修复保守栅格误拒绝，新构建 Cup 2/7、Open 7/7，Train 9/13 对找到参考路线，四条物理导航通过；剩余端点净空和有限搜索负结果保留，详见[修复交付](../../navigation/delivery/07-navigation-geometry-fix.md)。

## 8. 需复核项

1. **[p5-mode-bench-r01](../../../outputs/diagnostics/task-modes/p5-mode-bench-20261026-r01) 与训练采集的导航负结果**：原因定位见[专项复核](../../navigation/delivery/06-cup-navigation-review.md)，代码修复、Train/Open 扩展复核和四条物理导航已完成，见[修复交付](../../navigation/delivery/07-navigation-geometry-fix.md)。Cup 仍有 4 个端点不满足 2 cm 余量、1 个有限搜索耗尽；Train 仍有 2 对端点余量不足、2 对有限搜索耗尽。完整连续操作仍未验证。
2. **[p4-yaw-full-r02](../../../outputs/diagnostics/task-modes/p4-yaw-full-20261026-r02) 的"eligible"**：calibration 资格为 eligible（1 个任务 2/6 成功），但 diagnosis 只有 1 个 0/3 S0 且无标签、无高率 S1；不能据此宣称 Orientation 或该任务模式成立。
3. **[p6-independent-calibration-r01](../../../outputs/diagnostics/task-modes/p6-independent-calibration-20261026-r01)**：S0013 3/3 成功，但只覆盖 3/60，且 `shared_station_preparation=false`，只能作为独立复核，不能替换完整标定或最终标签。
4. **[p6-open-bench-eval-r01](../../../outputs/diagnostics/task-modes/p6-open-bench-eval-20261026-r01)**：n=1、TRS=0，只能说明该 episode 上 π0.5 未完成，不能作为模式难度或策略能力结论。
5. **文档同步**：02 与 reports/README 已补充指向本文件的下游产物说明；P5/P6 在计划表中仍为"后续暂缓"，是否升级为进行中由用户决定。本文件只汇总已存在的产物事实。
6. **历史/失败 run**：[p2-three-camera-r01](../../../outputs/diagnostics/task-modes/p2-three-camera-20261026-r01)、[p4-evidence-r01](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r01)、[p4-prepared-measurement-r01](../../../outputs/diagnostics/task-modes/p4-prepared-measurement-20261026-r01)、[p5-full-negative-bench-r01](../../../outputs/diagnostics/task-modes/p5-full-negative-bench-20261026-r01) 分别是失败、环境错误或被修正前的记录，均被既有报告引用，本轮保留。p3-cup30 r01/r02 已作为重复/被替代产物删除，见第 11 节。

## 9. 产物读取入口

每个 run 的关键文件：

```
<run>/
├── scene_datasets/build/{config.json,summary.json,scenes.jsonl}   # 候选构建
├── calibration/{config.json,summary.json,trials.jsonl,diagnosis.jsonl}
├── collection/{summary.json,pair_index.jsonl,train_index.jsonl}   # train 采集
├── dataset/{summary.json,pair_index.jsonl,train_index.jsonl}      # train 物化
├── {summary.json,episodes.jsonl,oracle.json}                      # val benchmark 构建
├── {summary.json,results.jsonl,rollouts/}                         # val 策略评测
└── result.json                                                    # 单点 smoke / 专项检查
```

日志统一在 `outputs/logs/task-modes-*.log`。GB 级数据不做逐文件校验；实验结论以各 run 的主记录为准。

## 10. 附：run 清单

下表省略日期后缀：除 p0/p1 为 20261025 外，其余均为 20261026。

| 类别 | run |
|---|---|
| P0 | [p0-baseline-20261025-r01](../../../outputs/diagnostics/task-modes/p0-baseline-20261025-r01) |
| P1 | [p1-assets-geometry-20261025-r01](../../../outputs/diagnostics/task-modes/p1-assets-geometry-20261025-r01) |
| P2 | [p2-context-20261026-r01](../../../outputs/diagnostics/task-modes/p2-context-20261026-r01)；[p2-three-camera-20261026-r01](../../../outputs/diagnostics/task-modes/p2-three-camera-20261026-r01)；[p2-three-camera-20261026-r02](../../../outputs/diagnostics/task-modes/p2-three-camera-20261026-r02) |
| P3 构建 | [p3-cup30-20261026-r03](../../../outputs/diagnostics/task-modes/p3-cup30-20261026-r03)；[p3-open-20261026-r01](../../../outputs/diagnostics/task-modes/p3-open-20261026-r01)；[p3-parallel-20261026-r01](../../../outputs/diagnostics/task-modes/p3-parallel-20261026-r01)；[p3-public-20261026-r01](../../../outputs/diagnostics/task-modes/p3-public-20261026-r01)；[p3-public-20261026-r02](../../../outputs/diagnostics/task-modes/p3-public-20261026-r02)；[p3-spatial-20261026-r01](../../../outputs/diagnostics/task-modes/p3-spatial-20261026-r01) |
| P4 标定/诊断 | [p4-bottle-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-bottle-full-20261026-r01)；[p4-classify-error-20261026-r01](../../../outputs/diagnostics/task-modes/p4-classify-error-20261026-r01)；[p4-clock-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-clock-full-20261026-r01)；[p4-cup-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r01)；[p4-cup-full-20261026-r02](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r02)；[p4-evidence-20261026-r01](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r01)；[p4-evidence-20261026-r02](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r02)；[p4-evidence-20261026-r03](../../../outputs/diagnostics/task-modes/p4-evidence-20261026-r03)；[p4-high-open-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r01)；[p4-high-open-full-20261026-r02](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r02)；[p4-ik-probe-20261026-r01](../../../outputs/diagnostics/task-modes/p4-ik-probe-20261026-r01)；[p4-open-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-open-full-20261026-r01)；[p4-potato-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-potato-full-20261026-r01)；[p4-prepared-measurement-20261026-r01](../../../outputs/diagnostics/task-modes/p4-prepared-measurement-20261026-r01)；[p4-prepared-measurement-20261026-r02](../../../outputs/diagnostics/task-modes/p4-prepared-measurement-20261026-r02)；[p4-safe-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-safe-full-20261026-r01)；[p4-soap-full-20261026-r01](../../../outputs/diagnostics/task-modes/p4-soap-full-20261026-r01)；[p4-train-bottle-full-20261026-r02](../../../outputs/diagnostics/task-modes/p4-train-bottle-full-20261026-r02)；[p4-yaw-full-20261026-r02](../../../outputs/diagnostics/task-modes/p4-yaw-full-20261026-r02) |
| P5 下游 | [p5-full-negative-bench-20261026-r01](../../../outputs/diagnostics/task-modes/p5-full-negative-bench-20261026-r01)；[p5-full-negative-bench-20261026-r02](../../../outputs/diagnostics/task-modes/p5-full-negative-bench-20261026-r02)；[p5-mode-bench-20261026-r01](../../../outputs/diagnostics/task-modes/p5-mode-bench-20261026-r01)；[p5-negative-bench-20261026-r01](../../../outputs/diagnostics/task-modes/p5-negative-bench-20261026-r01)；[p5-open-bench-20261026-r01](../../../outputs/diagnostics/task-modes/p5-open-bench-20261026-r01)；[p5-train-collect-20261026-r01](../../../outputs/diagnostics/task-modes/p5-train-collect-20261026-r01)；[p5-train-materialized-20261026-r01](../../../outputs/diagnostics/task-modes/p5-train-materialized-20261026-r01)；[p5-train-negative-20261026-r01](../../../outputs/diagnostics/task-modes/p5-train-negative-20261026-r01) |
| P6 构建/搜索 | [p6-bottle-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-bottle-build-20261026-r01)；[p6-high-open-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-high-open-build-20261026-r01)；[p6-obstruction-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-obstruction-build-20261026-r01)；[p6-obstruction-build-20261026-r02](../../../outputs/diagnostics/task-modes/p6-obstruction-build-20261026-r02)；[p6-obstruction-build-20261026-r03](../../../outputs/diagnostics/task-modes/p6-obstruction-build-20261026-r03)；[p6-open-targets-20261026-r01](../../../outputs/diagnostics/task-modes/p6-open-targets-20261026-r01)；[p6-soap-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-soap-build-20261026-r01)；[p6-visibility-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-visibility-build-20261026-r01)；[p6-visibility-search-20261026-r01](../../../outputs/diagnostics/task-modes/p6-visibility-search-20261026-r01)；[p6-visibility-search-20261026-r02](../../../outputs/diagnostics/task-modes/p6-visibility-search-20261026-r02) |
| P6 设备/策略 | [p6-device-20261026-r01](../../../outputs/diagnostics/task-modes/p6-device-20261026-r01)；[p6-open-grasp-view-20261026-r01](../../../outputs/diagnostics/task-modes/p6-open-grasp-view-20261026-r01)；[p6-pi05-control-20261026-r01](../../../outputs/diagnostics/task-modes/p6-pi05-control-20261026-r01)；[p6-pi05-model-20261026-r01](../../../outputs/diagnostics/task-modes/p6-pi05-model-20261026-r01)；[p6-pi05-rollout-20261026-r01](../../../outputs/diagnostics/task-modes/p6-pi05-rollout-20261026-r01)；[p6-pi05-tracking-20261026-r01](../../../outputs/diagnostics/task-modes/p6-pi05-tracking-20261026-r01)；[p6-policy-observation-20261026-r01](../../../outputs/diagnostics/task-modes/p6-policy-observation-20261026-r01)；[p6-real-observation-replay-20261026-r01](../../../outputs/diagnostics/task-modes/p6-real-observation-replay-20261026-r01)；[p6-shared-preparation-20261026-r01](../../../outputs/diagnostics/task-modes/p6-shared-preparation-20261026-r01)；[p6-tracking-accuracy-20261026-r01](../../../outputs/diagnostics/task-modes/p6-tracking-accuracy-20261026-r01) |
| P6 评测 | [p6-open-bench-eval-20261026-r01](../../../outputs/diagnostics/task-modes/p6-open-bench-eval-20261026-r01) |
| P6 独立标定 | [p6-independent-calibration-20261026-r01](../../../outputs/diagnostics/task-modes/p6-independent-calibration-20261026-r01) |

## 11. 目录整理记录（2026-10-26）

### 已删除

| 路径 | 原因 | 释放 |
|---|---|---:|
| p3-cup30-20261026-r01 | `scenes.jsonl` 与 r03 字节相同（同 MD5），无外部引用 | 171M |
| p3-cup30-20261026-r02 | orientation 站位不同的早期变体，已被 r03 替代 | 171M |
| p3-public-20261026-r01/scene_datasets | 早期公开构建，被 r02 替代；保留该 run 的 `recipe.json` 与 `geometry-diagnostics.json` | 308M |
| dependencies/pi05-cache | 空安装缓存目录 | — |

整理后 run 数 62 → 60，释放约 650 MB。未移动任何 run 目录：config、diagnosis、train_index 中存在绝对路径引用，搬迁会破坏来源链。

### 保留原则

- 当前结论（修复后标定、下游、评测、环境验证）的原始记录全部保留。
- 修复前结果、失败记录、被修正的汇总均被报告引用，保留为历史证据，不改写为当前结论。
- 重复且无引用、已被完整替代的 build 目录删除；被引用但体量大的 run 不自动删除。

### 可进一步清理（需明确授权）

| run | 大小 | 说明 |
|---|---:|---|
| [p6-soap-build-20261026-r01](../../../outputs/diagnostics/task-modes/p6-soap-build-20261026-r01) | 926M | 仅构建演示，未被下游使用 |
| [p6-shared-preparation-20261026-r01](../../../outputs/diagnostics/task-modes/p6-shared-preparation-20261026-r01) | 920M | 共享准备性能对照 |
| [p2-context-20261026-r01](../../../outputs/diagnostics/task-modes/p2-context-20261026-r01) | 916M | CPU 事务/相机测试 |
| [p3-spatial-20261026-r01](../../../outputs/diagnostics/task-modes/p3-spatial-20261026-r01) | 613M | 构建演示 |
| [p3-parallel-20261026-r01](../../../outputs/diagnostics/task-modes/p3-parallel-20261026-r01) | 480M | 并行构建演示 |
| [p3-open-20261026-r01](../../../outputs/diagnostics/task-modes/p3-open-20261026-r01) | 310M | Safe 构建演示 |
| [p3-public-20261026-r02](../../../outputs/diagnostics/task-modes/p3-public-20261026-r02) | 308M | 公开构建演示 |
| [p4-cup-full-r01](../../../outputs/diagnostics/task-modes/p4-cup-full-20261026-r01) + [p4-bottle-full-r01](../../../outputs/diagnostics/task-modes/p4-bottle-full-20261026-r01) + [p4-high-open-full-r01](../../../outputs/diagnostics/task-modes/p4-high-open-full-20261026-r01) + [p4-soap-full-r01](../../../outputs/diagnostics/task-modes/p4-soap-full-20261026-r01) | 634M | 修复前完整标定 |

合计约 5.1 GB。删除后对应报告条目将失去本地原始记录，仅保留汇总数字。
