> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# S0034 timeout 复核

## 结论

用户观察有依据：t0、t1 已满足现有物理抓取判据，但成功判定尚未执行就触发墙钟超时。不是这些视频都显示了失败，也不能把四条 timeout 一律改为成功。

只读检查了 S0034 的 outcome、trajectory、逐物理 tick 的 physics trace、源码与 t0/t4 末尾视频帧。使用现有 `evaluate_mobile_pick` 对已有 trace 离线重算，初值由 `initial_state.json` 与配置重建，指尖 ID 由实际记录复核（152/153）。不重新仿真、不修改源 attempt/封存率表。

| trial | 原标签 | 截断阶段 | 最大抬升 | 尾部合格保持 | 离线现有判定器 |
|---|---|---|---|---|---|
| t0 | timeout | hold，23/50 控制区间 | 9.80cm | 2.092s | success |
| t1 | timeout | hold，35/50 控制区间 | 10.09cm | 2.732s | success |
| t2 | timeout | hold，17/50 控制区间 | 10.11cm | 1.864s | failure：保持不足 |
| t3 | success | hold，50/50 控制区间 | 10.48cm | 3.108s | success（对照） |
| t4 | timeout | close，未进入 lift 执行 | 0.046cm | 0s | 未满足抓取判据 |

尾部合格保持指双指真实承力、抬升至少 5cm、无桌面支撑、滑移在阈值内的连续仿真时长。完整判定器还检查全部 trace 的约束和证据；t0/t1 离线完整判定确为 success，不仅是视觉判断。

## 为什么被标成 timeout

- `ContinuousContext.deadline()` 按真实 `time.monotonic()` 从 context 创建计时，预算 300 秒，不是仿真 300 秒。
- `tick()` 每次先检查 deadline；规划、仿真、渲染、Raw RGB/depth、视频及证据写盘耗时都处在这个墙钟窗口内。此处没有性能 profile，不能断言具体哪项占比最大。
- `no_edit_v2.py:102–105` 完成固定 50 次 hold tick 后才调用 `evaluate_mobile_pick`。t0/t1 已在 lift+hold trace 满足 2 秒标准，但额外 hold 未跑完，下一次 tick 的 deadline 抛异常。
- `no_edit_execution.py:510–512` 直接将 TimeoutError 写为 timeout，没有对已有物理 trace 再检查已成立的成功。
- 这些 episode 到截断时只前进约 10–12 秒仿真，墙钟已到 300 秒；视频播放的是仿真时间，无法凭视频长度判断墙钟预算。

因此现有 20% 的 S0034 成功率是“300 秒机器墙钟预算 + 当前结束时序”下的完成率，不纯粹是物理抓取能力。只读复核发现另外 2 条已达到物理成功：按该证据计算为 3/5（60%），仍低于原 S1 的 80%。这不是对封存率表的正式改写；其它站点尚未逐条复核，不能推出全包仍无高率点。

## 建议修复（本次未改代码、未启动新运行）

1. 抓取成功条件应由真实仿真证据驱动：满足完整成功判据即可结束，不必等额外固定 hold 全部跑完。
2. timeout 收尾时保留执行终止原因，同时对已有 trace 做完整判定，分别记录 `execution_termination` 和 `task_outcome`，避免物理成功被墙钟状态遮蔽。
3. 为墙钟/仿真时间及规划、渲染、记录开销增加诊断；严格物理判据不放宽，改变预算应新 run/显式版本。
4. 全包 timeout 再做同样只读复核，派生新审计结果，不原地重写已封存 publication。修复后补充“已经成功但 hold 尚未结束时 deadline 到达”的回归。

## 证据

- 逐 trial 抓握窗口
- 离线完整判定
- 源码：`src/lastmile_dataflow/runtime/no_edit_v2.py:99–105`、`runtime/no_edit_execution.py:63–65, 510–512`、`validation/pick.py`。

本报告不将 posthoc 成功冒充原在线执行成功，不把独立标定 trial 当作 S0→S1 连续恢复训练数据。
