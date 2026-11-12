# 无编辑标定实时状态

- 状态：active（自动生成，覆盖更新）
- 更新时间：2026-10-28 21:45:10
- 驱动：已退出（PID 4044810）

## 1. 总体

| 指标 | 数值 |
|---|---:|
| 墙钟（首个 chunk 准备起） | 12.97 h |
| 已完成标定 | 11.26 h |
| 当前 chunk 已跑 | 1.56 h |
| 标定累计 | 12.83 h |
| 非标定墙钟 | 0.15 h |
| 累计准备（与标定重叠） | 1.01 h |
| 已完成 chunk | 2/41 |
| 当前 chunk | no-edit-full-20261028-c002 |
| 已渲染 S0/S1 视频（task-mode） | 172（S0 63 / S1 109） |
| 排除目标类别 | 6 |

## 2. 各 chunk 时间与产出

| chunk | houses | 准备 min | 标定 min | trial | trial/min | success | success/min | success rate | 任务数 | 有效任务 | 有效任务/min | 有效任务率 | 热图 | 状态 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| no-edit-full-20261028-c000 | 0-4 | 8.6 | 183.3 | 2,934 | 16.0 | 365 | 1.99 | 12.44% | 132 | 40 | 0.22 | 30.30% | 132 | done |
| no-edit-full-20261028-c001 | 5-29 | 20.0 | 492.5 | 8,838 | 17.9 | 1,043 | 2.12 | 11.80% | 414 | 127 | 0.26 | 30.68% | 414 | done |
| no-edit-full-20261028-c002 | 30-54 | 20.0 | 93.8 | 168 | 1.8 | 0 | 0.00 | 0.00% | 516 | 0 | 0.00 | 0.00% | 0 | running |
| no-edit-full-20261028-c003 | 55-79 | 12.2 | — | 0 | — | 0 | — | — | 536 | 0 | — | — | 0 | prepared |
| **总体** | 0-54 | — | 769.6 | 11,940 | 15.5 | 1,408 | 1.83 | 11.79% | 1,062 | 167 | 0.22 | 15.73% | 546 | — |

> `prepared` 表示输入已准备、尚未开始标定；准备自 c001 起与上一批标定重叠。
> 任务 = 房间 + 目标物体；有效任务 = 至少 1 条 success trial；总体行的准备时间不求和不重复计。

### 2.1 总体任务（pick / open）

| 操作 | 总任务数 | 累计有效任务数 | 每分钟有效任务数 | 有效任务率 |
|---|---:|---:|---:|---:|
| pick | 527 | 109 | 0.14 | 20.68% |
| open | 535 | 58 | 0.08 | 10.84% |
| **合计** | **1,062** | **167** | **0.22** | **15.73%** |

### 2.2 总体 trial（pick / open）

| 操作 | 总 trial | 累计成功 trial | 每分钟成功 trial 数 | success rate |
|---|---:|---:|---:|---:|
| pick | 4,953 | 1,042 | 1.35 | 21.04% |
| open | 6,987 | 366 | 0.48 | 5.24% |
| **合计** | **11,940** | **1,408** | **1.83** | **11.79%** |

> 总任务数/总 trial 为已完成 + 当前 chunk；分母为标定累计分钟。

## 3. Trial 状态分布（已完成 + 当前）

| 状态 | 数量 | 占比 |
|---|---:|---:|
| planning_no_solution | 7,551 | 63.24% |
| failure | 2,516 | 21.07% |
| success | 1,408 | 11.79% |
| timeout | 297 | 2.49% |
| initialization_error | 154 | 1.29% |
| episode_error | 14 | 0.12% |
| **合计** | **11,940** | **100%** |

> 规划无解、未执行和 unknown 不是任务失败；`episode_error`/`timeout` 与任务失败分开。

## 4. 视频与热图

- 视频状态文件：`/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/no-edit-calibration-val0-99/outputs/no-edit-full-20261028/replays/incremental_valid_state.json`；清单：`/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/no-edit-calibration-val0-99/outputs/no-edit-full-20261028/replays/incremental_valid_manifest.jsonl`。
- 已渲染 172 条；S0 63 条、S1 109 条。
- 单卡约 19.5 s/条；8 卡并行实测约 16.3 条/min（约 5.3×单卡）。
- 热图由 16 workers 离线导出，单批约 1–2 min。

| 标定 run | S0 | S1 | 热图 |
|---|---:|---:|---:|
| no-edit-full-20261028-c000 | 17 | 26 | 132 |
| no-edit-full-20261028-c001 | 46 | 83 | 414 |
| no-edit-full-20261028-c002 | 0 | 0 | 0 |

> S0 全部为 0 步规划失败的场景没有轨迹，不生成 S0 视频，也不补造。

## 5. 目录视图

- `figures/`、`preparations/`、`planning/`、`rollouts/`：`<pick|open>/<类别>/<valid_task|no_valid_task>/<条目>`。
- `replays/`：`<pick|open>/<类别>/<valid_task|no_valid_task>/<success|failure>/<scene_id>`，不再经过 replay-run 层。
- 分类视图是符号链接，已有结果不移动、不复制；每 30 min 自动重建。
- 目录中的 `valid_task` 与第 1/2 节一致：任务至少 1 条 success trial。

## 6. ETA

- 当前 chunk 剩余：6088.7 min。
- 已完成 25 房 chunk 平均标定：492.5 min。
- chunk 进度：已完成 2，当前 1，总 41。
- 剩余 38 个 chunk，预计标定 311.90 h；含当前 chunk 共 413.38 h。
- 不含最终全量失败分析和代表视频；排除类别继续生效后实际可能更短。

## 7. 口径

- 准备、标定、热图、视频均来自真实 `summary.json`、日志时间戳和产物计数。
- 视频与热图常与标定并行，不能与标定时间直接相加。
- 剔除规则：累计 ≥1000 trial 且 success rate < 0.1%；Cup、Mug、Bowl、SoapBottle、SprayBottle、Vase 永久保留。
- 状态文件由 `scripts/report_no_edit_status.py` 覆盖生成，不写回任何标定记录。
