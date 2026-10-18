# Benchmark 静态视图、评测组合视图与双视频交付

状态：superseded
日期：2026-10-18
替代：[PNG 图片交付](2026-10-18-png.md)
关联：[需求](../../requirement/02-visualization-and-videos.md)、[运行指南](../../docs/运行指南.md)、[原始评测结果](../phase_01/2026-10-18-build.md)

> 本报告保留原 HTML 实施记录；当前已按用户要求改为 PNG，实际输出以图片交付报告和运行指南为准。

已在 `feat/lastmile-bench-v0.1` 工作区完成。Benchmark 和评测分别输出页面，已有两条 RBY-1 rollout 补齐 head / 第三人称共四个 MP4；未重新执行策略，原始轨迹与评分未改写。Task Success / Recovery 仍为原始 0/2。

## 实现与产物

| 部分 | 行为与入口 |
|---|---|
| Benchmark | `build-bench` 自动生成静态地图，展示 S0、高率站位、Recovery Region、最短路径和难度；无实际轨迹、视频或策略评分 |
| 评测 | `evaluate-bench` 默认实时记录两个原始视角，生成轨迹、视频与结果组合页，只读取 benchmark |
| 历史视频 | 原始 head PNG 编码；记录状态与冻结场景用于第三人称重建，标记来源，无策略调用、无物理推进 |
| 查看器 | 无外部脚本和网络依赖，可直接打开 HTML；episode 切换、图层开关、地图缩放平移、统一播放暂停、倍速、进度同步 |
| 时间 | 两路视频每个记录边界一帧，recording.json 保存实际时间；末尾不足一个周期时仍显示真实仿真时间 |

当前六组构建来源均已生成静态页面，其中空 benchmark 显示筛选汇总；两组已有评测均有组合入口。Panda 未支持的策略显示未知、无轨迹、无视频。

- [RBY-1 benchmark 页面](../../../../outputs/benchmarks/all-existing-v0.1/rby1-current/visualization/index.html)
- [Panda benchmark 页面](../../../../outputs/benchmarks/all-existing-v0.1/panda-current/visualization/index.html)
- [RBY-1 评测页面](../../../../outputs/benchmark_evaluations/rby1-full-v0.1/visualization/index.html)
- [Panda 未执行页面](../../../../outputs/benchmark_evaluations/panda-interface-v0.1/visualization/index.html)

已有 RBY-1 `e000000` 每视角 401 帧，`e000001` 每视角 15 帧。视频编码帧率为 10 Hz，对应播放器时长 40.1 / 1.5 秒（含末帧显示周期）；真实执行时间为 40.000 / 1.352 秒。页面按记录帧映射真实时间，不将视频编码时长当作任务执行时间。第三人称使用实际场景遮挡，不隐藏墙体或物件。

## 验证

- 回归测试：233 项，232 通过，1 项显式真实渲染测试按原有开关跳过。
- 新增录制验证：同一控制边界只记录一次，仅保存 head / third_person，两个视频时间一致。
- 评测隔离验证：执行评测前后，benchmark 文件集合及内容保持一致。
- 真实场景实时录制检查：GPU 1 上执行两个保持周期，初始状态与两次实际边界各记录一帧；两路各 3 帧，recording_complete=true，无逐帧 PNG。该检查不评估任务成功。
- Chromium 实际打开本地页面，地图绘制、H.264 解码、双路播放、拖动与 episode 切换通过，无 JavaScript 异常。
- 拖动到第 201 帧：两路均定位 20.000 秒；播放后两路偏差约 0.0003 秒，地图同步更新。短 episode 最后一帧显示真实 1.352 秒。
- `git diff --check` 通过。

浏览器检查记录和截图位于 `outputs/diagnostics/benchmark-viewer/`；实时录制检查位于 `outputs/diagnostics/benchmark-video-live/`。以下是浏览器实际截图，类别为实现证据：

![Benchmark 静态构建视图](../../../../outputs/diagnostics/benchmark-viewer/benchmark.png)

![评测轨迹与两个视频组合视图](../../../../outputs/diagnostics/benchmark-viewer/evaluation.png)

## 文件与边界

主要实现：`data/visualization/benchmark.py`、`benchmark.html`、`benchmark_replay.py`；录制接入 `data/recorder.py`，评测编排接入 `workflows/lastmilebench.py`。共享 CollectionConfig 新增可选 video_cameras 映射，其他流程保持原有默认行为。

地图底图是构建时的保守导航栅格，绿色是带朝向约束的离散区域近似，不能解释为任意朝向的连续可操作保证。未来评测录制不默认保存 PNG；已有 PNG 保留。重建第三人称提供该次运行的可视回放，不增加新的任务成功证据。

评测页面通过相对路径引用 benchmark 的 visualization/data.js；迁移目录时需保留两者相对关系，或根据新配置重新生成页面。没有提交、合并或发布站点。
