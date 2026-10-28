# 修复版全量标定：进度记录（暂停）

- 状态：paused（全部进程已停，2026-10-28 22:00 核对）
- 日期：2026-10-28
- 替代：无
- 关联：[修复摘要](05-repair-summary.md)、[实时状态](status.md)、[阶段交付](04-calibration-repair.md)

## 1. 进程

| 进程 | 结束时间 | 结束方式 |
|---|---|---|
| 标定流水线（`run_no_edit_pipeline.py`，GPU 6、7） | 21:19:47 | c002 标定以退出码 1 失败后，流水线随之退出 |
| 视频监视（`watch_valid_videos.py`，GPU 7） | 21:2x | 渲染完 c001（129 段）后，读 `state.json` 报 `FileNotFoundError` 退出 |
| 状态报告与目录整理循环（CPU） | 21:45:44 | 检测到流水线已退出，做完最后一次刷新后正常退出 |

22:00 核对：没有残留进程，GPU 6、7 显存为 0。

## 2. 数据进度

全量共 41 个 chunk。房号 0–4 为第一个 chunk，之后每 25 房一个 chunk，覆盖 val_0–999。

| chunk | 房号 | 状态 | trial | 标定用时 | 热图 | 视频 |
|---|---|---|---:|---:|---:|---:|
| c000 | 0–4 | ✅ 完成 | 2,934 | 183 min | 132 | 43 |
| c001 | 5–29 | ✅ 完成 | 8,838 | 493 min | 414 | 129 |
| c002 | 30–54 | ❌ 失败（见第 4 节） | 168（全部为错误） | 66 min | — | — |
| c003 | 55–79 | 输入已准备 | — | — | — | — |

c000 + c001 合计：

| 指标 | pick | open | 合计 | 旧版主 cohort |
|---|---:|---:|---:|---:|
| trial 成功率 | 21.1%（1,042/4,929） | 5.3%（366/6,843） | **12.0%**（1,408/11,772） | 2.88% |
| 执行后成功率 | 52.2% | 16.4% | **33.4%** | 10.90% |
| 有效任务率 | 41.3%（109/264） | 20.6%（58/282） | **30.6%**（167/546） | 12.46% |
| 预算超时 | — | — | 297（2.5%） | 0.8% |

- 动态排除尚未触发，目前只排除手动指定的 6 类。
- pick 提升明显；open 和旧版基本持平，open 超时的原因见[修复摘要](05-repair-summary.md)和超时分析。

## 3. 位置

```text
outputs/no-edit-full-20261028/
├── src/          冻结代码快照（修复版，含分组后的调度脚本）
├── state.json    completed = [c000, c001]
├── runs/c000-c009/   c000、c001（完成），c002（不完整）
├── inputs/c000-c009/ c000–c003；inputs/recipes/
├── replays/<run>/    视频；replays_by_task/ 分类视图
└── logs/         launch.sh、video-watch.sh、status-organize.sh 及各 chunk 日志
```

旧版基线在 `outputs/v1-no-edit/`，修复实验在 `outputs/experiments/`，两次搬家的路径对照表在 `outputs/reorg/`。

## 4. c002 失败

- **现象**：重启后，c002 写入的 168 条 trial 全部是错误：`initialization_error` 154 条，`episode_error` 14 条，只涉及 val45、val52 两栋房。21:19:47 主进程读取 `inputs/.../c002/scene-files/val47-pick-mug_…/baseline/config.json` 时报 `FileNotFoundError`，整个 run 中止，「已处理场景 = 0/516」。部分 trial 的报错是 `panda_omron/model.urdf is not a file`。同一时段，视频监视也报了找不到 `state.json`。
- **已确认**：这些文件现在都存在，而且自生成以来没有被改动过（mtime、ctime 都是 11:58）。用同一份代码在单个进程里加载 val45 的场景，可以正常加载。
- **同时段的外部操作**：仓库在这段时间被其他会话改动过。
  - `.worktrees/` 被重新组织：删除了 case3、exception-cleanup，`curobo-time-optimization` 改名为 `collision-handling`；
  - 本 worktree 目录的 ctime 为 21:35；
  - 21:50 本 worktree 有新提交 `040bd1c`（提交内容包含本轮的脚本和文档改动）。
- **判断**：文件很可能是在这些操作期间被暂时移走或不可见。但 ctime 只保留最后一次的时间，**还不能确认就是这个原因**。重启前需要先确认已经没有其他会话在操作这个 worktree。

## 5. 重启步骤

1. 确认没有其他进程或会话在移动、清理本 worktree 或 `outputs/`。
2. 把不完整的 `runs/c000-c009/no-edit-full-20261028-c002` 移走或删除：`create_run` 不允许目标目录已存在，否则会报错。
3. 依次启动：
   - `logs/no-edit-full-20261028-launch.sh`（GPU 6、7；如果 GPU 0–5 已空闲，可以改成 8 卡）；
   - `logs/no-edit-full-20261028-video-watch.sh <driver-pid>`；
   - `logs/no-edit-full-20261028-status-organize.sh <driver-pid>`。
4. 流水线会跳过已完成的 c000、c001 和已准备好的 c002、c003 输入，从 c002 标定开始。按 2 卡的速度，剩余约 39 个 chunk 需要约 2 周；8 卡约 4 天。

## 6. 待决事项

- **open 预算超时**（2.5%）：主要原因是 2 卡下 GPU 争用让单次求解变慢约 2 倍，新几何本身约 1.2 倍。切到 8 卡可以缓解；也可以把 open 预算提到 360 s。需要用户决定。
- **立柱碰撞球偏粗**：val1 碗任务的近桌站位（S0008、S0010、S0011、S0012、S0022）起点被判碰撞，固定立柱和升降立柱的球侵入桌面 1.3–5.7 cm，而真实立柱离桌面 5.8–10.3 cm。其中 S0008、S0012 的底盘离桌子下部确实只有 1.4–1.6 cm。诊断结果在 `outputs/experiments/diagnostics/near-stations-val1-bowl-20261028/`。建议统计全量里这类起点碰撞的占比，再决定是否重做立柱球。
- 机械臂碰撞球没有核对过覆盖；凸包重叠处可能把障碍内部判成自由空间。
