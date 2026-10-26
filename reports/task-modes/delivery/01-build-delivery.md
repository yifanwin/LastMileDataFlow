# P0–P3 构建交付

- 状态：done
- 日期：2026-10-25（P0–P2）、2026-10-26（P3）
- 替代：01-p0-foundations.md、02-assets-and-geometry.md、03-build-context-and-transactions.md、04-eight-builders-and-dispatch.md
- 关联：[需求](../requirement/01-task-mode-construction.md)、[方案](../plan/01-task-mode-implementation.md)、[计划表](../plan/02-build-schedule.md)、[标定交付](02-calibration-delivery.md)、[运行清单](05-run-inventory.md)

## 1. 范围与结论

P0–P3 完成候选构建主链：固定配方与范围、离线资产索引、任务级 BuildContext、真实编辑事务与三相机测量、八类 Builder 和公开 `build-scenes` 编排。旧 Case 构造入口与在线资产检索退出，不保留兼容壳。

`accepted` 只表示合法候选构建完成，不是最终模式标签。P0–P2 没有任务 rollout；P3 也没有 GPU 标定或成功示教。

## 2. P0 配置、身份与计时基线

- 固定 PandaOmron `robocasa-v1`；采样半径 1 m、XY lattice 0.25 m；采样范围不覆盖执行工作区，导航使用完整来源房间地面并可绕出采样圆。
- Pick/Open 任务 ID 基于实际实例名和关节名；同 XY 多 yaw 使用独立 `station_id`；显式 yaw 不被 `aim_station` 改回。
- 完整房间导航要求来源地面。已删除物体的 task-local 模型明确拒绝并提示 `local_task_models=false`，不从缺失家具推断通路。

基线 run `p0-baseline-20261025-r01`（CPU 准备链，非任务成功）：

| 测量 | 实测 |
|---|---:|
| source 解析、组合编译、初始化 | 2.593 s |
| settle_scene | 4.621 s |
| station clone ×3 | 0.0155–0.0190 s |
| ThorLibrary 分类发现 | 0.0105 s |
| 单个 Vase 适配、compile、测量 | 0.0187 s |
| 返回 catalog 后首次 load | 0.0025 s |

场景 436 bodies / 3,571 geoms；静置检查 `valid=true`，使用 `require_source_stability=false`。历史百来源计时属于旧 Case/position-v1，不用于计算新流程加速倍率。

## 3. P1 资产索引与共用几何

离线索引输出唯一 `assets.jsonl`：**1,695 条，1,154 可适配，541 不支持**，耗时 22.21 s。不支持项主要为 articulated 361、静态融合惯量无正特征值 127、无有效碰撞几何 30。

| role | 可适配 profile 数（允许重叠） |
|---|---:|
| floor_obstacle | 174 |
| visual_occluder | 84 |
| arm_obstacle | 114 |
| approach_obstacle | 114 |
| small_clutter | 105 |
| support_furniture | 73 |

代表资产各 compile 一次，重载尺寸与索引一致、模型复用；首次 runtime 加载 0.004–0.028 s。完整 val0 房间栅格 173×344、0.05 m：原始 free 35,721 cells，whole-body 导航 free 10,936 cells；底盘半径 0.438292 m、导航轮廓半径 0.480970 m、margin 0.02 m，最大底盘 clearance 0.927245 m。

索引不是资产可搬迁承诺；派生 XML 仍引用只读来源 mesh/texture。本阶段未运行 GPU、rollout 或模式构建验收。

## 4. P2 构建上下文、事务与三相机

每个 task 只准备一次停车/静置、49 点 lattice、支撑、原始占据、导航/clearance、role Selector 和孤立资产缓存；编辑后重新派生几何，原基线只读。`SceneEdit`、`AddObject`、`MoveTargetOnSupport` 走统一 runtime 请求，实际实例化、步进和关闭由构建会话完成；静置后检查碰撞、支撑、源场景影响，最多一次局部 repair，候选间不累积编辑，最终冻结并可重载。

| 真实路径 | 结果 |
|---|---|
| no-edit | 冻结与重载状态完全一致 |
| base-pose | S0000 初始化合法并冻结；重载 base 一致 |
| AddObject | RoboTHOR garbage bin 接受；静置、承重、碰撞、源影响通过 |
| MoveTarget 2 cm | 拒绝：`placement_did_not_fit_same_support`，未伪造成功 |

三相机绑定/外参/FOV 已核对。同一 S0000 初态、256×256、参考像素门槛 24：

| 相机 | 观测/参考像素 | 可见比例 |
|---|---:|---:|
| left | 183/183 | 1.0 |
| wrist | 748/748 | 1.0 |
| right | 140/170 | 0.823529 |
| 聚合 | — | 1.0 |

停车姿态 left 19/19 低于门槛，wrist/right 不可见；聚合 `value=null`、`known_max=0`，不能宣称全部低可见。分割诊断使用无 MSAA 的临时 render-model 副本，源模型、物理参数和快照不变。

## 5. P3 八类 Builder 与公开编排

| 构造器 | 实际行为 |
|---|---|
| Reachability | 边缘站位优先；天然对照不足时同支撑面平移 target，重新派生 anchor/lattice |
| Orientation | 同 XY 默认 yaw/±45° 对照；计入 20 姿态 |
| Articulated | 只消费有效 Open，不编辑；Pick 记 skipped，不增加先抓把手门槛 |
| Ground | 天然遮挡或一个 floor_obstacle；直线阻挡且 A* detour ≥1.2 |
| Narrow | 低底盘余量优先；必要时 1→2 个地面障碍，保留更宽可导航站位 |
| Visibility | 三相机低可见与更好视角；定向 ray band 放一个遮挡物；未知不当低可见 |
| Arm-path | 名义肩/肘/pregrasp capsule 通道；1–3 个 blocker；不冒充实际关节轨迹 |
| Approach | 复用实际控制 pregrasp→grasp 方向与距离；天然限制优先，至多一个 blocker |

统一预算：32 个提案、3 个物理原候选、每候选最多一次 repair、最多 20 个合法姿态。提案阶段不执行 IK/TrajOpt，通过廉价代理后才解析少量真实资产。

P3 公开构建结果（完整 run 表见[运行清单](05-run-inventory.md)第 3 节）：

| run | 接受/拒绝/跳过 | 接受的意图 |
|---|---|---|
| p3-cup30-20261026-r03 | 4/3/1 | reachability、ground、narrow、orientation |
| p3-open-20261026-r01 | 2/6/0 | reachability、articulated |
| p3-public-20261026-r02 | 2/5/1 | reachability、orientation |
| p3-spatial-20261026-r01 | 2/5/1 | reachability（MoveTarget）、orientation |
| p3-parallel-20261026-r01 | 8/0/2 | reachability×4、orientation×4 |

拒绝集中在 `no_station_pair_for_nominal_channels`、`no_navigable_station_pair`、`no_navigable_valid_view_pair`、`asset_pool_empty:<role>` 和 `no_aggregate_visibility_contrast`。P3 阶段接受五类构造意图；Visibility/Arm-path/Approach 为负结果，后续 P6 补充构建另有 Arm-path/Approach 接受，见[下游与验证](03-downstream-delivery.md)。

### 5.1 场景预览

2026-10-26 已用 `build-scenes --preview-dataset` 为 12 个 build run 的 **38 个 accepted 场景**生成 `previews/<scene_id>.png` 和 `index.html`：

- 4 个编辑候选输出“编辑前/编辑后”双栏：p6-bottle 的 Arm-path（Vase）与 Narrow（Footstool）、p3-spatial 与 p6-soap 的 MoveTarget。
- 每张图保留站位指示（蓝点/箭头）、目标框、新增物体框，并在头部标注 `proposal_intent`。
- 编辑前站位未写入 v2 构建记录，前栏显示 `not recorded`，不推测补造。
- 无 CJK 字体时图像标签回退为英文；HTML 仍为中文。
- 0 accepted 的 run 只生成 rejected/skipped 卡片，不生成图片。

实现上 `scene_preview` 现在读取 v2 的 `edits.placements` / `edits.target_moves`，并从同 task 的 `scene-files/<task>/baseline` 定位编辑前场景；`scenes.jsonl` 和原始快照不改写。逐图汇总见[场景预览](build-scenes-previews.md)，预览入口与逐 run 结果见 `outputs/diagnostics/task-modes/README.md`。

## 6. 验证与边界

| 阶段 | 全套回归 | 真实检查 |
|---|---|---|
| P0 | 281 项：279 通过、2 跳过 | val0 CPU 准备链、配置路径 |
| P1 | 293 项：291 通过、2 跳过 | 1,695 条离线索引、6 代表资产重载 |
| P2 | 308 项：306 通过、2 跳过 | 真实事务、冻结/重载、三相机 GPU 渲染 |
| P3 | 299 项：297 通过、2 跳过 | 双 house 并行构建、父进程唯一 scenes.jsonl |

修复底盘缺陷后的标定交付回归为 **354 项：352 通过、2 跳过**；场景预览改动后最新全量回归为 **355 项：353 通过、2 跳过**，见[标定交付](02-calibration-delivery.md)。阶段回归与真实 GPU 渲染分开报告；P0–P2 的简化模型、mock 与 import 检查不代替任务 rollout。旧 Case 测试与内部旧路径已删除。
