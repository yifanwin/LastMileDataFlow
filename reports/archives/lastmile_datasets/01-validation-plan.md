> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# 首版真实验收计划

本计划在完整包执行前记录。目标是验收工程链路，不估计泛化成功率。

- 输入：`scenes-datasets-design/outputs/scene_datasets/scene-val103-cup30-minimal-v3-20261012-r2`，选择 `val_103-case1-f2871d23d0f128da` 的已封存 position 配置包。其余包不在本次完整运行范围。
- 固定范围：源 selected 的 20 个点，每点 5 次有效独立试验，共 100 次；不按诊断结果挑点，不重建障碍物、不改采样点。
- 配置：`configs/lastmile_datasets/v0.json`。S0 ≤0.2，S1 ≥0.8，邻近距离 ≤1m；最多 3 个 S0，连续恢复结果全部记录。
- 资源：串行一个 worker；CUDA 0–7 实时择优，利用率 ≤50%、空闲显存 ≥10000MB；不停止其它任务。
- 验收：完整有效标定 → 可追溯率表/选择 → 实际连续恢复 → canonical audit → 成功专用 train index → 独立 state/images adapter。
- 无合格点对、通路不存在、操作失败均保留，不当作成功数据；真实闭环成功必须以审计通过的 train episode 为证。零成功时不放松阈值。
- 单 attempt 超时 300s，单场景 86400s。基础设施错误不是普通失败，保留并可在同代码/配置下 resume；预算耗尽仍 incomplete。
- pilot：一条实际 trial 已执行，timeout，canonical audit valid；仅用于记录和导出链路 smoke，不代表完成标定。pilot 与正式 run 分开，正式 run 不重复计入 pilot。
- 未承诺：native motor action、native benchmark 同配置标定、MolmoBot 真训练/推理、其它场景泛化。

运行命令与 PID 在 `outputs/logs/lastmile-v0-case1-full-20261013-launch.json`；正式 run `lastmile-v0-case1-full-20261013`。时间以日志和 run.json 为准。
