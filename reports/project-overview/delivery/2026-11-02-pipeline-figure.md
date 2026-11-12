# 项目整体流程图

- 状态：done
- 日期：2026-11-02
- 替代：无
- 关联：README.md、reports/archives/task-modes/delivery/build-scenes-previews.md、reports/navigation/delivery/08-navigation-soft-clearance.md

## 交付

- 当前整体图：`2026-11-02-pipeline-merged-hull.png`，按用户指定改为 merged_hull 46/60 版本。
- 上一版整体图：`2026-11-02-pipeline-heatmap.png`；其中独立热图为 `2026-11-02-station-heatmap.png`，已被本次指定来源替代，不代表 merged_hull 46/60。
- 图像工具：内置 imagegen；真实标定热图使用 Matplotlib 从已有 NPZ 直接绘制。
- 未修改源码，未启动新标定或训练实验，未运行软件回归。
- 用户已有 `reports/README.md` 修改和两份未跟踪报告保留。

## 执行与内容

### 用户指定版本替换

完成检查：中央已显示蓝色成功率热图与 `merged_hull · 46/60`，右侧 40/41/42 区域为高成功率深蓝，低率区域仍为浅蓝；保留 20 站 × 3 次说明。使用内置 imagegen 替换，最终文件已复制到本仓库，未覆盖上一版。

本次最终提示词：

```text
Use case: infographic-diagram.
Image 1: EDIT TARGET. Existing final five-stage LastMileDataFlow pipeline paper figure.
Image 2: SCIENTIFIC DATA INSERT. Real comparison of THREE versions of the val103/Cup_30 station heatmap. Select ONLY THE MIDDLE PANEL labeled "Current default (merged hull + new spheres)", "task success 46/60". The left 37/60 and right 44/60 are other versions; do not use or average them.

Make one focused replacement: replace ONLY the rainbow heatmap graphic and its colorbar in central stage (c) of image 1 with the MIDDLE BLUE HEATMAP PANEL of image 2, using its shared blue colorbar at the far right of image 2. Treat it as a pasted scientific figure: preserve the exact middle-panel station distribution, white-ring station circles, small station ID labels, black star for target cup, gray support-table rectangle, and spatial pattern of dark blue high-success and pale blue low-success areas. In particular the right-hand lobe of stations 40/41/42 is DARK BLUE for the correct 46/60 version; the 37/60 version has that region pale, so do not confuse them. Preserve middle-panel main geometry. Blue colorbar 0 = light blue, 1 = dark blue. Keep unknown regions white and obstacle/table pale gray. NO rainbow, NO invented data or new station positions. Crop out neighboring panels and their titles entirely. Fit the middle panel with the real blue colorbar prominently in the current center image region. It is fine to omit world-coordinate axis ticks to ensure clarity but keep the station IDs as supplied if readable.
Keep the existing central heading exactly "Station success heatmap". Above the graphic, add only a small concise line "merged_hull · 46/60". Change the caption below heatmap to exactly "20 stations × 3 trials · Gaussian smoothing". Keep S0/S1 definition chips and "Pose = position + heading" footer. All five stage headings, arrows, scene screenshots, edit/no-edit panels, dataset and benchmark cards, and downstream use cards MUST REMAIN UNCHANGED. Preserve the overall figure dimensions/layout/typography and all other content in image 1. This is an accurate scientific source replacement, not a redesign. Final wide paper figure.
```

核对 `.worktrees/collision-handling/reports/collision-handling/delivery/02-structured-environment.md` 第 7 节，用户表格对应 `outputs/experiments/collision-handling/runs/b6-merged-cup30-20261029`：20 站 × 3 trial、seed 42、任务成功 46/60、进入执行 51、规划无解 9、执行失败 5、回放无接触。该路径位于 collision-handling worktree。

采用该 worktree 已有 `outputs/experiments/collision-handling/analysis/b6-cup30-heatmaps-20261029/spatial_heatmaps.png` 的中间面板及共享蓝色色条；左侧基线 37/60、右侧 cuboid 44/60 均不采用。保留实际站位分布与高低率空间格局，图中标注 `merged_hull · 46/60`。未重跑实验，未修改来源 worktree。

按用户草图保留主流程：场景输入 → 编辑 / 不编辑 → 站位图构建 → 数据集 / benchmark → 数据利用。核对当前 README 和真实场景预览，采用英文论文图标注。中央突出候选位姿采样、cuRobo V2 执行标定、逐点结果汇总；S0 是困难起点，S1 是参考操作站位。策略学习作为下游用途，不表示已经完成模型训练。

使用内置 imagegen，以实际仿真截图为素材进行生成式排版。原始截图来源如下；生成图用于方法流程说明，精确坐标、数值和实验结论应以原始产物为准。

1. `/home/wenyifan/.codex/attachments/20c11f09-eea7-42f5-abb6-906b3ad5ee0f/codex-clipboard-6cfee4da-168d-4979-8969-d5b6123b234e.png`
2. `/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000.png`
3. `/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-arm_path_collision-v000.png`
4. `/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/outputs/diagnostics/navigation/navigation-soft-cup-bench-20261026-r02/visualization/e000000.png`
5. `/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/outputs/diagnostics/task-modes/p5/p5-train-collect-20261026-r01/collection/observations/r000036/images/robot0_agentview_right/000160.png`

## 最终提示词

最终检查：中央已替换为成功率热力图，带 0–1 色条；移除旧站位散点图及跨场景 S0/S1 图片卡。保留编辑 / 不编辑的汇合关系、数据集 / benchmark 分支与下游学习用途虚线。整体图为生成式排版，局部图像可能发生重绘；独立热图为直接读取真实数组的确定性绘图，科研定量引用使用独立热图或原始 NPZ。

原始热图输入：`outputs/diagnostics/task-modes/p4/p4-cup-full-20261026-r02/calibration/figures/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-ground_obstacle-v000/success_heatmap.npz` 及同目录 `heatmap.json`。

用户补充要求中央站位图使用热力图。找到真实 Cup 标定 `p4-cup-full-20261026-r02` 的 `success_heatmap.npz`，从其中的 `success_rate/free/origin/resolution_m` 重新绘图，使用同目录 `heatmap.json` 的桌面包围盒；有效栅格 1,134 个，成功率范围 0–1。保留障碍与未知区域，不补造未测值。原始热图保存在 `2026-11-02-station-heatmap.png`。总图由 imagegen 排版，精确空间数据请使用这份独立热图及原 NPZ。

下方为初始提示词，末尾记录修订提示词。

```text
Use case: infographic-diagram
Asset type: robotics research paper pipeline overview, final wide landscape figure, approximately 2.3:1 aspect ratio, high resolution.
Primary request: Redesign the supplied rough pipeline as a clean publication-quality LastMileDataFlow overview. Use the actual supplied project simulation screenshots as photographic inserts. White background, flat muted colors, crisp dark text, fine borders, generous whitespace, aligned panels, restrained academic design. No decorative title banner.
Input images: Image 1 is the user's OLD DIAGRAM, reference for logical flow ONLY; replace all its embedded imagery and wording. Image 2 is an ACTUAL original ProcTHOR Cup scene preview, use cropped local scene view at right and top-down view at left as inserts, remove console/header/footer and long IDs. Image 3 is an ACTUAL edited scene preview: upper row before, lower row after adding an obstacle. Crop the before/after right-hand scene views for editing examples, keep real geometry and objects. Image 4 is an ACTUAL Cup benchmark navigation and station map: crop the map itself, keep station coordinates/layout and recovery-route relationship; don't reproduce the long experiment strings. Image 5 is an ACTUAL robot camera observation from a recorded collection, use it as an observation thumbnail. The render images are simulation evidence, do not replace them with invented photorealistic rooms or imaginary robots. Keep reference inserts recognizable, do not embellish the measured data.

Composition: left-to-right five-region pipeline. Stage A at left, stage B next with two vertically stacked parallel choices, stage C LARGE dominant center (about 30% of total width), stage D two stacked output cards, stage E two downstream-use cards. Roughly x proportions 0.14 / 0.18 / 0.30 / 0.19 / 0.13 with gutters. Diagram must read cleanly at journal two-column width. Main panels share a consistent overall vertical envelope. Use straight orthogonal arrows only in gutters, never through photos or labels. All labels English. Stage C strongest visual weight with muted terracotta edge and very pale warm fill; A pale slate blue; B pale sage; D pale lavender; E white/gray with subtle blue accent. No heavy gradients, no shadows, no oversized rounded PowerPoint boxes.

Exact structure and text:
A. heading "(a) Scene input"
one or two clean cropped actual room views from supplied images 2 and 3, stacked.
labels "ProcTHOR" and "Scenes + task targets".

B. heading "(b) Scene preparation".
two stacked branches with equal visual importance:
upper card "Edit" with side-by-side actual BEFORE and AFTER views from image 3, small "Before" / "After" labels; one concise line "Object / obstacle edits".
lower card "No edit" with actual original local scene from image 2; concise line "Original scene".
One arrow from A forks into BOTH branches, BOTH converge to C. No arrow between the two branches.

C. heading "(c) Station-map construction".
Inside the dominant center panel, top a compact 3-step strip:
"Sample poses" → "Execute trials" → "Aggregate outcomes".
Under strip small line "cuRobo V2".
A large ACTUAL map crop from image 4 as centerpiece, preserving its meaningful occupied/free-space and station pattern. Include a small actual top-down room inset from image 2 to ground the map in the scene. Title over map "Task-conditioned station outcomes".
The map and top-down inset are separate clearly bounded images, NOT spatially warped into each other. Preserve the map's true geometry. Do not invent a smooth rainbow heatmap or new numerical trial scores. The source map already shows S0 and high-success recovery stations; emphasize those features with clear callout labels "S0" and "S1" while retaining real spatial arrangement. Bottom two small chips "S0: difficult start" and "S1: reference station".
Small footer inside C "Pose = position + heading".
Keep C concise and dominant.

D. heading "(d) Data products".
Two output cards receiving separate arrows from C:
upper "Last-mile dataset"
a horizontal sequence with three short labels "S0" → "S1" → "Manipulate", plus actual image-5 observation thumbnail and a minimal trajectory/observation strip.
one bottom line "Observations · actions · trajectories".
lower "Last-mile benchmark"
a cropped actual benchmark map thumbnail from image 4
one bottom line "Difficult S0 · reference recovery".
Do not claim all recorded tasks succeeded.

E. heading "(e) Data use".
Upper card "Policy learning", below "Imitation / fine-tuning", small subtle label "Downstream use", dashed border. Arrow from dataset to this card, dashed to indicate intended downstream use rather than completed training.
Lower card "Policy evaluation", below "Task success · recovery", solid border. Solid arrow from benchmark into evaluation. Dashed arrow from learning down to evaluation is optional ONLY if there is ample clear space. Do not name any trained model or add quantitative performance claims.

Typography: consistent professional sans-serif, near-black. Each heading one or two clean lines, labels distinctly readable. No bullets, ellipses, paragraphs, logo, watermark, unnecessary footer, model training success claim, or room-structure editing claim. Do not copy misspellings like 'affordace'. Produce the final composed figure, not a photographed page or mockup.
```

## 版式修订提示词

```text
Use case: infographic-diagram. Edit the supplied generated paper overview. Preserve all five stages, every flow arrow, concise English headings, supplied real simulation screenshot content in stages a, b, d and e, and the dataset/benchmark branching logic. Make one focused correction to stage (c): REMOVE the two photographic cards below the main map currently labeled S0: difficult start and S1: reference station. These are different scenes and must not imply a real matched S0/S1 image pair. Replace them with two small TEXT-ONLY chips reading exactly "S0: difficult start" and "S1: reference station". Use the recovered vertical space to enlarge the actual central map, with the separate top-down example inset remaining smaller. The map is explanatory: remove all numeric axis tick labels and world-meter axes in central map AND its thumbnails at stages d/e, because generated numeric coordinates must not be presented as exact experimental measurements. Keep the map geometry, orange S0, green S1 stations, target and reference recovery route readable. Replace any tiny map legend with just three readable items "S0", "S1", "Reference route". Keep footer "Pose = position + heading". Keep stage e Policy learning dashed as Downstream use. Style-only adjustment across whole figure: replace all gradient fills with flat very pale tints / mostly white panels, no shadows, restrained muted borders instead of bright saturated lines, less rounded corners. Preserve alignment and legibility, remove the decorative ellipsis beside the dataset filmstrip. Everything else unchanged. Final research paper figure, wide landscape.
```

## 热力图修订提示词（最终）

```text
Use case: infographic-diagram. Asset: final robotics research paper pipeline overview.
Image 1: EDIT TARGET, the current five-column pipeline figure.
Image 2: SUPPORTING INSERT, a REAL heatmap rendered directly from this project's saved Cup-scene calibration success_heatmap.npz. This is scientific data, preserve the insert's spatial shape and color distribution, table footprint and success-rate colorbar faithfully.

Required focused edit: in central stage (c), REPLACE the gray discrete station map and the small separate top-down example inset with ONE LARGE DOMINANT actual HEATMAP from image 2. Fit the heatmap into the entire central visualization region below cuRobo V2 and above the two S0/S1 text chips. Enlarge it so it is the hero image. Use the complete colored field, dark gray blocked area, white unobserved free space, beige table rectangle, and vertical rainbow operation-success-rate colorbar with 0, 0.5, 1. The field should look like the data supplied: a C-shaped warm red/orange band on the upper-left and lower-left of the table, transitioning through green/cyan to dark purple on the near-table right side. DO NOT invent or redraw a different distribution. Do not place the color overlay on table or walls. Do not add fictitious sampled station dots or numeric success scores. Remove the central-map old S0/S1 location arrows, reference-route line and discrete station legend entirely.
Replace the visualization header text "Task-conditioned station outcomes" with exactly "Station success heatmap". Keep a small caption exactly "Measured trials · Gaussian smoothing" below the map. Keep the two text-only chips exactly "S0: difficult start" and "S1: reference station", and the footer "Pose = position + heading".
Everything else in image 1 must remain unchanged: stages a/b real scene screenshots, before/after object edit, no-edit branch, flow arrows, the top 3-step strip and cuRobo V2, dataset sequence S0→S1→Manipulate, benchmark reference-route mini-map, policy learning/evaluation cards. Benchmark can retain a route map because it is a different data product; ONLY the central station construction visual must be a heatmap. No synthetic images or dramatic style changes. Clean legible English type, research paper composition, white background. The real heatmap must be clearly recognizable as a heatmap and visually dominant. Final wide landscape figure.
```
