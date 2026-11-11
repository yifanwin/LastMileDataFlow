# outputs 目录规则

- 状态：active
- 日期：2026-11-11
- 替代：outputs_rules.md
- 关联：../AGENTS.md

本规则适用于本仓库及各 worktree。只有 `RULES.md` 入 Git，运行产物和本地索引不入 Git。

## 1. 一个单元回答一个问题

```text
outputs/
├── RULES.md
├── README.md                         本地索引：单元及用途
├── batches/<名称>/                   批量运行、批量评测、数据采集
│   ├── README.md
│   ├── state.json                    调度需要时保存
│   ├── runs/<run>/                    原始运行结果
│   └── analysis/                     跨 run 的分析、统计表、可视化
└── experiments/
    ├── <问题>/                       复现、A/B 对照、参数实验
    │   ├── README.md
    │   ├── runs/<run>/
    │   └── analysis/
    └── scratch/<日期>-<说明>/         临时测试，无需完整实验管理
```

单元名称描述问题，run 名称描述条件，例如 `upd1`、`upd8`。名称使用小写字母、数字、连字符，不复用已有 run。每个目录的直接子目录不超过 20 个；数量多时按批次或条件分组，少量 run 不强制分组。

## 2. run 是最小归档单位

配置、seed、输入引用、日志、指标、checkpoint、轨迹、视频及本 run 生成的场景输入都保存到对应 run。内部目录按需创建，不强制 `inputs/`、`logs/`、`replays/` 等固定结构，不另建分类链接视图。

共享资产、模型和已有数据集保存引用，不重复复制。直接从配方标定时，本 run 新生成的场景放在 `scene-inputs/`。离线回放视频保存在来源 run 的 `videos/<回放名称>/`。

`build-scenes`、`evaluate-stations`、`collect-lastmile` 使用 `--output-dir <单元>/runs --run-id <名称>`；benchmark 命令使用 `--output <单元>/runs/<名称>`。批量流水线的 `--output-dir` 指向单元，自动管理其 `runs/` 和 `analysis/`。

## 3. 记录代码版本，不复制源码

单元 README 记录 Git commit、启动时工作区状态、启动命令和配置文件。不同 run 使用不同版本或命令时，在 Runs 表补充差异。不复制源码，不生成文件指纹。

长期任务从固定 commit 的 worktree 启动，README 记录其路径，运行期间不修改该 worktree。需要变更代码时结束当前运行，使用新 commit 和新 run。普通临时测试如使用未提交修改，如实写明 `dirty` 及改动内容，不宣称 commit 已完整描述运行代码。

## 4. 结论和产物分离

- `runs/`：原始运行结果及其附属文件。
- `analysis/`：分析数据、统计表和可视化。
- 仓库 `reports/<主题>/`：最终结论，引用 run 和 analysis 中的结果，不重复保存附件。

读取已有批次回答新问题时，分析写入新实验单元的 `analysis/`，引用原 run。

## 5. 单元 README

```markdown
# 实验名称

- 状态：active
- 目的：验证什么问题
- Git commit：3f2e235
- 工作区：clean
- 启动命令：...
- 配置文件：...

## Runs

| Run | 条件 | 状态 |
| --- | --- | --- |
| upd1 | 每步更新 1 次 | done |
| upd8 | 每步更新 8 次 | active |

## 结论

详见 reports/xxx.md
```

状态使用 `active`、`done`、`baseline`、`superseded`，仅在单元 README 维护；结束后原地保留。顶层 `outputs/README.md` 只列出单元及用途，不重复维护状态、版本和结论。scratch 无需套用上述模板。

## 6. 历史产物、搬迁和删除

新运行使用本规则；已有产物不自动迁移。旧报告中的路径和历史事实保持原样。

有进程读写时，不移动、改名或删除单元、所属 worktree 或 outputs。删除 worktree 前处理其中的产物。确需搬迁时先停止相关运行，修改受影响的配置、记录和链接，并检查实际使用的输入路径；不强制生成迁移清单或派生索引。

删除运行结果前须经用户确认；保留报告引用的结果、对照基线和已有 run 依赖的输入。scratch 按需清理，不设置自动删除期限。
