# 输出目录分类视图

- 状态：active
- 日期：2026-10-27
- 替代：无
- 关联：[计划表](../plan/02-schedule.md)、[实时状态](status.md)、[试点交付](01-pilot.md)

## 1. 结构

对每个标定 run：

```text
outputs/<run>/
├── figures/<pick|open>/<类别>/<valid_task|no_valid_task>/<scene>
├── preparations/<pick|open>/<类别>/<valid_task|no_valid_task>/<scene>
├── planning/<pick|open>/<类别>/<valid_task|no_valid_task>/<plan>
└── rollouts/<pick|open>/<类别>/<valid_task|no_valid_task>/<rollout>
```

回放不再保留 replay-run 层，直接以任务类型和任务为入口；叶子名称为任务标识（房间 + 目标物体，当前使用 scene_id）：

```text
outputs/replays/
└── <pick|open>/<类别>/<valid_task|no_valid_task>/<success|failure>/<scene_id>
```

原始 `outputs/replays/valid-...` 目录仍保留为视频数据源，不再出现在分类视图路径里。

`valid_task` 表示该任务（房间 + 目标物体）至少有 1 条 success trial；否则为 `no_valid_task`。视频的 `success`/`failure` 取自源 rollout 的 `status == success`，其余一律归入 `failure`。

> 实时状态文件里的“有效任务/min”与本目录的 `valid_task` 口径一致：任务至少 1 条 success trial。

## 2. 实现方式

- 已有结果不移动、不复制；分类目录全部是指向原始条目的相对符号链接。
- 原始 `rollouts/r000001`、`planning/r000001-q1` 等路径保持只读，`planning_ref`、`model_dir`、回放和失败分析无需改写。
- 生成脚本：`scripts/organize_outputs_by_task.py`，幂等。
- 有效性变化（当前 chunk 从未完成变为有效）时，脚本删除错误分类下的旧链接并建立新链接。
- 未被 trial 记录引用的在建 rollout/planning 会暂时留在 `unmapped`；后续运行自动补齐。
- 每 30 min 与实时状态一起重建；回放视频监视器另行每 5 min 检查新 chunk。

## 3. 当前快照（2026-10-27 10:20）

| 项目 | 数量 |
|---|---:|
| 分类链接总数 | 111,795 |
| figures | 2,286 |
| preparations | 2,315 |
| planning | 55,982 |
| rollouts | 50,948 |
| replays | 264（success 140 / failure 124） |
| 断链 | 0 |
| 冲突 | 0 |

c006 标定仍在进行，新建 planning/rollout 会在下一次整理时补齐。
