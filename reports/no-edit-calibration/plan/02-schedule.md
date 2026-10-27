# 无编辑全体 val 直接标定计划表

- 状态：active
- 日期：2026-10-26
- 替代：无；将实验方案拆为可跟踪任务
- 关联：[需求](../requirement/01-requirement.md)、[实验方案](01-experiment-plan.md)、[运行手册](../../../docs/运行手册.md)

## 1. 执行约定与当前进度

本表是本次实验的唯一进度表；算法、字段和命令细节查[实验方案](01-experiment-plan.md)，不在这里复制第二份设计。

固定口径：

- 1000 个来源：`val_0`–`val_999`，全部记为 `val`；
- 无编辑：不加障碍、不移目标、不改初始姿态；
- 每任务最多 20 个合法站位，每站 3 次；
- task-mode 资格：S0 0/3→1/3，S1 3/3→2/3；
- 直接标定：`evaluate-stations --recipe`，不产出独立 build run；
- 8 卡并行；CPU trial 60 起步，按实测提高；
- 标定期间 `numerical`，全部完成后统一回放视频。

当前状态：

| 已完成 | 结果 |
|---|---|
| 需求定稿 | 1000 场景、无编辑、task-mode 资格、失败原因归纳口径、目标类别排除 |
| 实验方案 | 直接标定架构、流水线、最小代码改动、配方、批次、8 卡和 CPU 规则 |
| 计划表 | 本文件 |
| 代码主体 | P1.1–P1.11、P2.1–P2.9 已实现；全套回归 384 项通过、2 跳过 |
| P0 试点 | 已完成：5 房、209 accepted、1,429 站位、3,651 有效 trial、115 成功、163 全失败、11 有效场景、31 incomplete、53 episode_error；209 张热图已导出；11 个有效场景 19 条视频（8 个 S0+S1，3 个仅 S1） |
| 试点排除 | 17 个零成功类别：AlarmClock、Apple、Book、CD、CellPhone、Cup、Egg、Mug、Newspaper、Pen、Pencil、Plate、RemoteControl、Safe、SprayBottle、Statue、TissueBox |
| 试点交付 | [01-pilot.md](../delivery/01-pilot.md)：规模、失败主表、类别成功率、异常与全量交接 |
| 全量 | c000–c005 已完成；c006 曾启动 1,430 trial，因排除策略变更归档；主驱动暂停，先补跑 c000–c005 的排除类别 |
| GPU 并发 | 当前 8 卡按负载分配：仅使用 GPU 6–7，各 2 个 planning worker；补跑和主链均用该配置 |
| 视频 | 已渲染 265 条（试点 19、c000 4、c001–c004 178、c005 64）；8 卡并行约 16 条/min；补跑/主链恢复后继续由监视器增量渲染 |
| 实时状态 | `reports/no-edit-calibration/delivery/status.md` 每 30 min 覆盖更新；含墙钟、各 chunk 准备/标定/trial、失败状态、热图/视频和 ETA |
| 输出整理 | `figures/preparations/planning/rollouts` 按 `<pick|open>/<类别>/<valid_task|no_valid_task>/` 建立符号链接视图；回放直接到 `outputs/replays/<pick|open>/<类别>/<valid_task|no_valid_task>/<success|failure>/<scene_id>`，不再保留 replay-run 层；每 30 min 与状态一起重建，详见 [03-output-layout.md](../delivery/03-output-layout.md) |
| 收尾器 | 旧收尾器已停止；补跑完成、主驱动 c006 恢复后重启 |
| 排除策略 | 累计 `trials >= 1000` 且 success rate < 0.1% 才自动剔除，不设房屋数门槛；永久白名单 Cup、Mug、Bowl、SoapBottle、SprayBottle、Vase 永不剔除；手动排除难抓类别 Pen、Pencil、CellPhone、CD、Newspaper、RemoteControl |
| 补跑 | c000–c005 使用新目标白名单（pick 18 类、open 12 类；Vase/Statue 按资产前缀）；每 chunk 按 `房屋:task_id` 精确排除已跑任务，只补缺失白名单任务；6 个独立 run 顺序执行中 |

## 2. 阶段总表

| 阶段 | 目标 | 主要交付 | 阶段完成条件 | 预计 | 状态 |
|---|---|---|---|---|---|
| P0 需求与方案 | 固定范围、口径、直接标定架构 | 需求、方案、计划表 | 口径无待确认项 | 已完成 | 已完成 |
| P1 代码实现 | 打通 `evaluate-stations --recipe` | 配方、natural 构建、内部准备、直接标定入口、trial 归因、CPU 上限、流水线、测试 | 离线测试通过，命令可跑 | 2–4 个工作日 | 已完成 |
| P2 失败分析 | 资格、S0/S1、规划/执行失败明细、目标成功率 | `failure_analysis.py`、分析脚本、回放脚本、测试 | 合成数据覆盖四类失败输出与类别排除 | 1–2 个工作日 | 已完成 |
| P3 P0 试点 | 验证真实链路与证据 | `val_0`–`val_4` 标定 run + 分析 | 完成：无共享故障，209 热图，排除列表生成 | 1–3 h | 已完成 |
| P4 10% 验证 | 验证批次规模与 8 卡吞吐 | `val_5`–`val_104` 前 4 个 chunk | 与全量流水线合并，前 4 个 chunk 作为 10% 验证 | 8–18 h | 进行中 |
| P5 剩余全量 | 完成 105–999 | 37 个 chunk + 每 chunk 分析/热图 | 全部 chunk 完成，差异有解释 | 54–135 h | 进行中 |
| P6 汇总与视频 | 全量统计与代表视频 | 汇总表、最终报告、视频清单 | 补跑与主链 c006+ 完成后重启收尾器 | 6–14 h | 等待补跑/主链 |
| P7 收尾 | 文档、缓存清理、复核 | 交付报告、运行清单 | 收尾器生成报告/视频后执行清理与复核 | 1–2 h | 进行中（等待全量） |

## 3. 详细任务表

文件路径相对 `src/lastmile_dataflow/`；`configs/`、`scripts/`、`tests/`、`reports/` 为仓库根目录。

### P0 需求与方案

| 编号 | 任务 | 交付/文件 | 前置 | 验收条件 | 状态 |
|---|---|---|---|---|---|
| P0.1 | 需求定稿 | `reports/no-edit-calibration/requirement/01-requirement.md` | — | 1000 场景、无编辑、task-mode 资格、失败表口径固定 | 已完成 |
| P0.2 | 实验方案 | `reports/no-edit-calibration/plan/01-experiment-plan.md` | P0.1 | 直接标定、内部缓存、8 卡、CPU 规则明确 | 已完成 |
| P0.3 | 计划表 | 本文件 | P0.1、P0.2 | 任务、依赖、验收、状态可跟踪 | 已完成 |

### P1 代码实现

| 编号 | 任务 | 涉及文件 | 前置 | 验收条件 | 预计 | 状态 |
|---|---|---|---|---|---|---|
| P1.1 | 配方允许无编辑与全 val | `scenes/dataset_config.py` | P0.1 | `modes=[]` 通过；非空仍限八类；`train_fraction=0.0` 全部记为 val；`exclude_categories` 校验 | 1–2 h | 已完成 |
| P1.2 | 实现无编辑任务构建 | `scenes/build/builder.py` | P1.1 | `build_natural_task` 不产生编辑；最多 20 合法站位；无站位写 rejected；同房屋复用基线；不测可见性 | 2–4 h | 已完成 |
| P1.3 | 内部输入准备 | `workflows/scene_datasets.py` | P1.2 | 复用 `_build_house`；输出 `outputs/_calibration_inputs/<run-id>/`；不写 `outputs/scene_datasets`；单 house 错误隔离 | 3–6 h | 已完成 |
| P1.4 | 直接标定入口 | `workflows/collection.py`、`cli.py` | P1.3 | `evaluate-stations --recipe` 一条命令完成准备和标定；仍只有五个 CLI 命令 | 2–4 h | 已完成 |
| P1.5 | trial 记录补执行归因 | `workflows/collection.py` | — | `trials.jsonl` 含 `attribution`、`retry_history`；原有判定不变 | 0.5–1 h | 已完成 |
| P1.6 | 放开 CPU/准备/构建/规划 worker 上限 | `runtime/resources.py`、`planning/pool.py`、`cli.py` | P1.4 | CPU 按机器核数；build/preparation 上限 64；每卡规划 worker 1–4；配置写入 run | 1–2 h | 已完成 |
| P1.7 | 配方与批次文件 | `configs/scene_datasets/no_edit_val/`、`scripts/make_no_edit_recipes.py` | P1.1 | 覆盖 0–999 无缺口；pilot 与 10 批文件；全部 val | 1–2 h | 已完成 |
| P1.8 | 代码测试 | `tests/scenes/`、`tests/workflows/` | P1.1–P1.7 | 配方、natural 构建、prepare 输出、CLI 参数、trial 字段测试通过 | 4–8 h | 已完成 |
| P1.9 | 相关领域与全套回归 | 现有 `tests/` | P1.8 | 全套 384 项通过、2 跳过；失败项修复，不为旧路径保留回归 | 2–4 h | 已完成 |
| P1.10 | 流水线驱动 | `scripts/run_no_edit_pipeline.py`、`scripts/prepare_no_edit_inputs.py` | P1.4 | 下一 chunk 准备与当前 chunk 8 卡标定重叠；每 chunk 分析并更新排除 | 3–5 h | 已完成 |
| P1.11 | 目标类别排除 | `scenes/dataset_config.py`、`workflows/scene_datasets.py`、分析脚本 | P1.10 | 旧规则已完成；当前规则改为累计 ≥1000 trial 且 0 success，永久白名单除外 | 2–4 h | 已完成 |

### P2 失败分析与视频工具

| 编号 | 任务 | 涉及文件 | 前置 | 验收条件 | 预计 | 状态 |
|---|---|---|---|---|---|---|
| P2.1 | 资格与 S0/S1 分析 | `data/evaluation/failure_analysis.py` | P1.5 | 3 次完整性；`incomplete/no_success/no_high_rate_s1/eligible`；S0 0/3→1/3，S1 3/3→2/3 | 3–5 h | 已完成 |
| P2.2 | 规划失败明细 | 同上 | P2.1 | 从 `planning.json`/`solver_trace` 归因 goalset/approach/grasp × IK 不可达 / IK 约束不可行 / TrajOpt；含 arm、seed、grasp_index、误差、约束项 | 6–10 h | 已完成 |
| P2.3 | 执行失败明细 | 同上 | P2.1 | 输出 phase、原始 reason、attribution、grasp_row/source、retry；基础设施/未知单列 | 3–5 h | 已完成 |
| P2.4 | 汇总表与 Markdown | 同上 | P2.1–P2.3 | 主表（pick/open/合计/占比）、分组表、类别交叉表、执行细分表、约束细分表 | 3–5 h | 已完成 |
| P2.5 | 分析脚本 | `scripts/analyze_no_edit_calibration.py` | P2.4 | 支持单 run 和多 run；只读，不改 `trials.jsonl` | 1–2 h | 已完成 |
| P2.6 | 回放脚本 | `scripts/render_no_edit_replays.py` | P2.5 | 按 run 分组回放代表 rollout，合并 manifest | 2–4 h | 已完成 |
| P2.7 | 分析测试 | `tests/data/` | P2.1–P2.4 | 合成 trial/planning 覆盖 IK 不可达、约束不可行、TrajOpt、执行失败 | 3–6 h | 已完成 |
| P2.8 | 目标成功率与排除建议 | `failure_analysis.py`、`target_success_rates.json` | P2.4 | 类别/task/station 三层成功率；≥30 trial、≥2 house、0 成功输出排除列表 | 2–4 h | 已完成 |
| P2.9 | 热图离线导出 | `scripts/export_no_edit_heatmaps.py` | P1.9、P2.5 | 每批标定后导出 `figures/`，不新增 rollout；与下一批并行 | 2–4 h | 已完成 |

### P3 P0 试点（val_0–val_4）

| 编号 | 任务 | 命令/产物 | 前置 | 验收条件 | 预计 | 状态 |
|---|---|---|---|---|---|---|
| P3.1 | 跑试点 | `scripts/run_no_edit_pipeline.py --recipe pilot_000_004.json`，8 卡 | P1.9、P2.7 | 完成：5 房、209 accepted、1,429 站位、4,287 trial；无共享故障 | 0.5–1 h | 已完成 |
| P3.2 | 试点分析 | `analyze_no_edit_calibration.py` | P3.1 | 完成：3,651 有效 trial；163 全失败、11 有效场景、31 incomplete；主表与类别成功率生成 | 0.5–1 h | 已完成 |
| P3.3 | 试点热图与视频 | `export_no_edit_heatmaps.py`、`render_valid_videos.py` | P3.2 | 完成：209 张热图；11 个有效场景 19 条视频（8 个 S0+S1，3 个仅 S1，S0 全为 0 步规划失败）；视频链接见 [01-pilot.md](../delivery/01-pilot.md#valid-videos) | 0.5–1 h | 已完成 |
| P3.4 | 试点复盘与调参 | 运行记录 | P3.2、P3.3 | 完成：确认 8 卡/32 build/128 CPU/32 prep/16 heatmap 可用；启动全量 | 0.5–1 h | 已完成 |

**P3 出口条件**：无共享故障；失败证据字段完整；单房耗时和 trial 规模可外推；否则不启动 P4。

### P4 10% 验证（val_5–val_104，前 4 个 chunk）

| 编号 | 任务 | 命令/产物 | 前置 | 验收条件 | 预计 | 状态 |
|---|---|---|---|---|---|---|
| P4.1 | 前 4 个 chunk | `run_no_edit_pipeline.py --recipe remaining_005_999.json`，8 卡 | P3.4 | 5–104 完成；无共享故障；热图与排除更新正常 | 8–18 h | 进行中 |
| P4.2 | 批分析 | 分析脚本 | P4.1 | 任务/house 覆盖、S0/S1、失败主表和细分表、类别成功率可生成 | 1–2 h | 未开始 |
| P4.3 | 规模复核 | 运行记录 | P4.2 | 外推全量 trial、存储、墙钟；确定剩余 chunk 配置 | 1 h | 未开始 |

**P4 出口条件**：覆盖率与差异解释可接受；存储余量 ≥ 全量预计；否则调整批次或资源配置后再跑。

### P5 全量剩余（val_105–val_999）

| 编号 | 任务 | 来源 | 产物 | 前置 | 预计 | 状态 |
|---|---|---|---|---|---|---|
| P5.1 | 全量剩余 chunk | 105–999 | 37 个标定 run + 每 chunk 分析 + 热图 | P4 | 54–135 h | 进行中 |

每批用流水线驱动，批内按 25 房切 chunk；每 chunk 完成即分析并更新 `exclude_categories`，热图离线导出与下一 chunk 并行。共享故障停该批，换新 run-id 重跑；不覆盖前一批。

### P6 汇总、报告与视频

| 编号 | 任务 | 产物 | 前置 | 验收条件 | 预计 | 状态 |
|---|---|---|---|---|---|---|
| P6.1 | 全量汇总 | 多 run 分析 | P5.9 | 1000 来源 house 表、task 分类、S0/S1、失败主表和细分表 | 2–4 h | 未开始 |
| P6.2 | 最终报告 | `reports/no-edit-calibration/delivery/` | P6.1 | 覆盖需求验收项；unknown/未执行单独说明 | 2–4 h | 未开始 |
| P6.3 | 代表视频 | `outputs/replays/` + manifest | P6.1 | 有效场景 201 条已完成（试点 19、c000 4、c001–c004 178，8 卡约 11 min）；c005 起完成一个 chunk 渲染一个 | 2–6 h | 进行中 |

### P7 收尾

| 编号 | 任务 | 产物 | 前置 | 验收条件 | 状态 |
|---|---|---|---|---|---|
| P7.1 | 文档同步 | `docs/`、`reports/` | P6.2 | 命令、配置、数据格式与实现一致 | 未开始 |
| P7.2 | 缓存清理 | `outputs/_calibration_inputs/` | P6.3 | 回放完成且无待用 rollout 后清理；不删标定 run | 未开始 |
| P7.3 | 最终复核 | 交付报告 | P6.2、P7.1 | 不把 unknown/未执行写成失败；不补造结果 | 未开始 |

## 4. 依赖与推进顺序

```text
P0.1–P0.3
  → P1.1 → P1.2 → P1.3 → P1.4 → P1.6
  → P1.5、P1.7 可与 P1.1–P1.4 并行
  → P1.8 → P1.9
  → P2.1 → P2.2/P2.3 → P2.4 → P2.5 → P2.6 → P2.7
  → P3.1 → P3.2 → P3.3
  → P4.1 → P4.2 → P4.3
  → P5.1–P5.9
  → P6.1 → P6.2/P6.3
  → P7.1–P7.3
```

P1.5 和 P1.7 不阻塞主链，可提前完成；P2 的分析模块可以在 P1 后期并行开发，但必须在 P3 试点前完成最小可用版本。

## 5. 里程碑

| 里程碑 | 条件 | 预计 |
|---|---|---|
| M1 代码可用 | P1.9、P2.7 通过，`evaluate-stations --recipe` 可跑 | 第 3–6 个工作日 |
| M2 试点通过 | P3 出口条件满足 | 第 4–7 个工作日 |
| M3 10% 通过 | P4 出口条件满足 | 第 5–9 个工作日 |
| M4 全量完成 | P5.9 完成，10 个批次有分析 | 第 8–14 个工作日 |
| M5 交付完成 | P6、P7 完成 | 第 9–16 个工作日 |

以上是串行上限；P1/P2 可部分并行，实际以依赖和实测为准。1000 来源的 60–150 h 标定是墙钟主项，8 卡和高 CPU 并行只压缩可并行段。

## 6. 每批验收记录表

| 批次 | run-id | 计划/记录 trial | 成功 | 规划失败 | 执行失败 | 不完整 | 共享故障 | 状态 |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| P0 0–4 | 待填 | | | | | | | 未开始 |
| 0–99 | 待填 | | | | | | | 未开始 |
| 100–199 | 待填 | | | | | | | 未开始 |
| 200–299 | 待填 | | | | | | | 未开始 |
| 300–399 | 待填 | | | | | | | 未开始 |
| 400–499 | 待填 | | | | | | | 未开始 |
| 500–599 | 待填 | | | | | | | 未开始 |
| 600–699 | 待填 | | | | | | | 未开始 |
| 700–799 | 待填 | | | | | | | 未开始 |
| 800–899 | 待填 | | | | | | | 未开始 |
| 900–999 | 待填 | | | | | | | 未开始 |

只填真实 run 的数字；计划/记录差异必须在备注中说明原因，不补造 trial。

## 7. 停批与异常处理

| 情况 | 处理 | 是否继续 |
|---|---|---|
| 共享 CUDA/模型/GPU/记录故障 | 停当前批，保已完成记录；新 run-id 重试 | 否 |
| 单 house 加载/准备失败 | 记录 rejected 和原因；按 house 隔离 | 是 |
| 站位准备失败 | 不生成该站 3 次 trial，任务记 incomplete | 是 |
| 规划无解 | `planning_no_solution`，`task_outcome=unknown` | 是 |
| 写入失败/run-id 冲突 | 换新 run-id，不覆盖 | 是 |
| GPU OOM/不稳定 | 降低 CPU/准备并发，新 run-id 重跑该批 | 否，先修配置 |

## 8. 计划表维护

- 每完成一项代码或验证，更新对应行状态和验收记录表；
- 阶段结果写入 `reports/no-edit-calibration/delivery/`，不为每小步增设附件；
- 运行配置、seed 和真实结果保存在标定 run，不另建重复索引；
- 需求或方案变更时，先更新需求和方案，再同步本表的任务与依赖；
- 本表编写阶段未修改源码、未执行测试或真实实验，代码与实验项保持未开始。
