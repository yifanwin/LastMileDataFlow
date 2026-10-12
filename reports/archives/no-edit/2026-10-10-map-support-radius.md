# 可调热图范围与支撑物避障交付

## 执行过程与结果

- 修改位置：`LastMileDataFlow/.worktrees/no-edit-lastmile-val`；保留该工作树原有改动，不修改旧工程或既有采集结果。
- 对照旧工程的支撑物 footprint 遮罩思路，实现独立的几何识别：parent 只作提示，目标底部附近的重叠碰撞几何作为保守候选证据；open 任务保留所属家具。
- 热图屏蔽完整支撑物投影，A* 额外按底盘半径和余量膨胀；提前输出 `navigation_grid.png/svg`。分析视频导航小图同步标注支撑物。
- `smoothing_sigma_m` 控制 Gaussian 带宽；新增 `smoothing_support_distance_m` 控制最大支持距离，默认 0.15 m。实际截断为两者 `min(3σ, support_distance)`，同时限制到实测坐标的直线距离。
- 两套 no_edit 配置默认显式使用 `0.05 / 0.15`。配置自动传入全量采集的初始、中间、最终导出，写入冻结配置和热图元数据。
- 新增 `scripts/replot_no_edit_maps.py`：只重绘已有记录到新目录，不执行机器人、不覆盖旧证据。

## 验证

- 全套单元回归：215 项，8 项跳过，其余通过；`git diff --check` 通过。
- 增加可调截断、非法参数、高架桌面 footprint、底盘膨胀、A* 绕行、视频支撑物像素和 SVG/HTML 回归。
- 使用既有 val103 / Cup30 冻结场景及实测统计重绘，识别支撑餐桌并核对 PNG；HTML 包含热图与导航障碍图两张内嵌图片。
- 预览：`outputs/diagnostics/support-map-config-final-val103-20261010/index.html`；输入来源和统计摘要记录于同目录 `replot_source.json`。
- 未重新执行真实导航/抓取、未启动全量采集；测试中的 A* 绕行是简化模型软件回归，不是新物理成功证据。

## 使用方式

见 [运行说明：调整扩散范围、测试与全量应用](../../docs/no-edit-collection.md#调整扩散范围测试与全量应用)。
配置变化后使用新 run-id，不对旧冻结 run 使用 `--resume`。旧视频不因重绘更新。

## 后续修正：圆形叠加，默认半径 0.30 m

按用户反馈，热图改为世界 XY 欧氏距离的圆形 Gaussian 核，不使用 A* 最短路作为核距离。重叠区域累加所有点的加权成功数与加权终局次数后归一化，保留原有插值聚合，不取最近点、不覆盖前一个点。支撑物仍作为遮罩，A* 的避障膨胀没有改动。

默认配置最终为 `smoothing_sigma_m=0.10`、`smoothing_support_distance_m=0.30`，覆盖前文初次交付的 0.15 m 默认值；初始、中间及最终导出均使用此配置。

新增圆形边界与不同标签/样本数重叠累加回归。现有 val103 实测数据重绘预览位于 `outputs/diagnostics/circular-gaussian-overlap-val103-20261010/index.html`，展示 0.30 m 半径（为显示圆边界，预览栅格为 0.025 m，生产配置仍为 0.05 m）。没有新物理执行，不覆盖旧结果。

## 显示修正：实测点中心浓，边缘浅，重叠叠加

用户确认“最深”指颜色浓度，不移动实测坐标。原有高斯加权成功率不变，新增单独的显示支持场 `Σw`；透明度采用 `0.20 + 0.75 × clip(Σw, 0, 1)`，未观测/障碍处透明度为 0。这样每个实测点中心浓、圆形边缘保留浅色，重叠核继续累加，且边缘变浅不会篡改成功率。每个站位的显示核不因试验次数而加深。

NPZ 新增 `kernel_strength` / `display_alpha`，元数据和图注区分色相（插值率）与浓度（高斯支持）。默认半径仍为 0.30 m；A* 和支撑物逻辑不变。

新增中心/边缘浓度、重叠支持累加、试验次数独立性及遮罩回归。已用旧 val103 记录独立重绘并目视检查 PNG，HTML 包含两张内嵌图；预览为 `outputs/diagnostics/circular-gaussian-pale-edge-val103-20261010/index.html`。仍未运行新的真实导航或全量采集。

## 最终显示调整：0.35 m、无渐淡、只突出核

按用户最新要求，两套默认配置改为 `smoothing_support_distance_m=0.35` 和 `smoothing_sigma_m=0.12`，确保实际截断半径为 0.35 m（不被 3σ 限制在旧 0.30 m）。

显示不再采用连续 Gaussian 透明度衰减：每个圆域固定贡献 0.25，中心 0.5σ（默认 0.06 m）额外贡献 0.65，重叠颜色贡献累加后在 0.95 限幅。原有 Gaussian 加权插值率不变，障碍遮罩与 A* 不变。此段覆盖前文渐淡显示方案。

定向回归 7 项通过，包含圆域浓度一致、核中心突出、重叠显示累加、限幅、数据值不变和 0.35 m 边界检查。独立重绘输出为 `outputs/diagnostics/flat-core-radius035-val103-20261010/index.html`，只使用已有实测数据；未新增机器人执行或覆盖历史结果。

## 用户最终要求：恢复最初构建，仅改扩散半径

已对照 Git HEAD 中原始 `no_edit_heatmap.py` 恢复最初的障碍感知栅格最短路 Gaussian 核、成功数/终局次数加权归一化、viridis 统一着色和全不透明显示。撤掉后来增加的渐淡、核心圈、平面圆域颜色浓度累加、`kernel_strength` / `display_alpha` 等显示字段。重叠区域仍使用原始 Gaussian 插值聚合。

仅保留可调支持距离上限 `smoothing_support_distance_m=0.35`；`smoothing_sigma_m` 恢复初始 `null`，默认仍取采样间距，不再使用 0.12 m 的人为默认。原始 3σ 截断保留，实际为 `min(3σ, 0.35 m)`。此前单独要求的支撑物遮罩、导航图标注与 A* 避障不撤回。

定向回归 5 项通过，包括对原始算法仅加入 0.35 m 截断后的逐栅格数值比对。新预览：`outputs/diagnostics/original-gaussian-radius035-val103-20261010/index.html`。仅重绘已有 val103 实测记录，不覆盖旧产物，不启动新物理执行。此段覆盖前面所有中间显示方案。
