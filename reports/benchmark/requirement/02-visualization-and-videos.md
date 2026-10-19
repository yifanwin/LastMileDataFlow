# Benchmark 与评测可视化、双视角视频

状态：done
日期：2026-10-18
替代：无
关联：[基础需求](01-lastmile-bench-v0.1.md)、[运行指南](../docs/运行指南.md)、[图片交付报告](../delivery/phase_02/2026-10-18-png.md)

## 数据边界

Benchmark 基于已有 scene-datasets 和标定结果独立构建，标定允许底盘移动。公开 episode 包含任务、S0、场景引用和种子；私有 oracle 保存高成功率站位、Recovery Region、最短恢复路径和难度。策略不接收 oracle。

Benchmark 输出不得包含策略实际执行轨迹、执行视频或策略评分。每次评测独立引用 benchmark，通过 episode_id 关联执行记录，不向 benchmark 回写评测信息。

## 两种图片输出

Benchmark 构建视图：同一地图展示导航自由空间、S0 位置与朝向、高成功率站位及原始成功计数、Recovery Region 近似、最短恢复路径和目标物体；展示 Path Length、Recovery Ratio、Detour Factor 和筛选汇总。

评测组合图片：读取 benchmark 静态图层，叠加本次策略完整实际底盘轨迹，标出中间和最终位置；展示 Task Success、Recovery Success、运动指标、终止原因，以及同一最终记录时刻的 head、第三人称截图。

按用户最新要求，输出为 PNG，不生成 HTML 或 JavaScript。每个 episode 一张图片，另生成 summary.png；完整视频继续单独保存。图片不承担交互播放、拖动或同步回放。

Recovery 的 XY 投影不代表任意朝向都可操作，必须标明朝向容差；地图使用构建时的保守导航栅格。

## 录制要求

后续 benchmark 评测每个真实 rollout 默认记录两个原始视角：videos/head.mp4 与 videos/third_person.mp4。第三人称覆盖机器人整体及目标操作区域。腕部相机继续供策略输入使用，不作为默认回放视频，也不默认保存逐帧图片。

轨迹和视频共享实际执行状态边界。recording.json 保存帧数、帧率、实际仿真时间及视频来源。遇到不足一个控制周期的末步，截图标注真实时间。

已有 rollout 的 head 视频由原始图片编码；第三人称可由完整记录状态和冻结场景逐帧重建。重建必须标明来源，不运行策略、不推进物理、不改变原始轨迹与评分。已有原始图片保留。

## 验收

1. 未执行策略也能独立查看 benchmark 静态图层，图片无实际执行数据。
2. 评测后可查看实际轨迹、结果及同一最终记录帧的双视角截图，两个完整视频单独保留。
3. 多次评测分别保存，benchmark 构建内容不因评测而改变。
4. 不支持的策略/机器人保持未执行、未知与无视频，不生成伪造执行记录。
