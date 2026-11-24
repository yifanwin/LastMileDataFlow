# RoboCasa cup_5 示教初态与障碍场景

- 状态：done
- 日期：2026-11-24 22:09 +08:00
- 关联：[cup_3 场景生成和筛选](03-pick-scene-selection.md)、[cup_5 官方示教回放](../../../outputs/experiments/robocasa-cup5-demo/runs/20261124-episode-000098/cup5-episode-000098.mp4)

## 父场景

父场景来自 RoboCasa365 `PickPlaceCounterToCabinet` 的官方人类示教 `target/episode_000098`，layout 1 / style 1。目标 `obj` 是 `objaverse/cup/cup_5/model.xml`，台面干扰物是玉米 `corn_3`，柜内干扰物是 `Paprika008`。保存官方 MJCF、episode metadata 和第一帧的 197 维仿真状态，冻结状态与官方逐元素相同。[官方数据包](https://utexas.box.com/s/ersnvqs6rcy6yge2fy5hmna7d954v9ty) · [冻结父场景](../../../outputs/experiments/robocasa-cup5-demo/runs/20261124-episode-000098/scene/env.json)

## 构建

沿用 cup_3 的 Kettle001、等外廓可移动方块和物理验收参数。首轮直接移植了 cup_3 的偏移，右侧多数提案越出台面或撞冰箱，远置水壶与烤面包机穿插。正式轮将远置移到左前方，左右两档沿台面前侧布置，左右只偏移 2.5 cm，向前分别偏移 14 和 20 cm。方块直左 12.5、16 cm 与刀具架穿插，被筛除。

正式轮 16 个障碍提案中通过 14 个（水壶 8 个、方块 6 个），另有原场景。静置期间通过案例的杯子峰值平移小于 0.0001 mm。实际几何净距与接触证据见[提案记录](../../../outputs/experiments/robocasa-cup5-demo/runs/20261124-cup5-variants-r03/scenes.jsonl)和[可复跑配方](../../../outputs/experiments/robocasa-cup5-demo/runs/20261124-cup5-variants-r02/recipe.yaml)。

官方示教模型只从第 0 组 geom 推导惯性。为可见方块显式写入均匀长方体惯性。该模型的两条底盘滑动轴顺序也与 cup_3 父模型不同，落位按 MJCF 实际世界轴求解。cup_3 原场景复测仍为 233 个名义点、40 个合法点。局部配置、分层和视野测试共 10 项通过。

## 人工选择

场景筛选入口为 `scripts/station_map/review_robocasa_pick.py`。重载审阅、相机证据和选集图代码位于 `station_map/`，审阅流程调用 `station_map/sampling/` 的站位生成与检查。

父场景有 233 个名义站、26 个初态合法站、25 个机身相机可见站。14 个变体均保留同一批 26 个合法站。每组包含同种障碍的远置、左置、右置三个条件。

| 前移档 | 水壶共同可见站 | 方块共同可见站 |
| --- | ---: | ---: |
| 14 cm | 8 | 0 |
| 20 cm | 15 | 5 |

推荐先看水壶 20 cm 组。方块 14 cm 组只适合初态几何对照，左侧近置单例没有达到可见门槛的站位。视野门槛沿用 cup_3，目标参考投影至少 64 像素、完整入画且至少一路相机保留 80% 的目标像素。本批完成物理筛选、独立重载、同站视野检查和可视化。抓取执行仍待验证。

抽查水壶与方块各一例，同站重载左右机身相机最大 8 位 RGB 通道差分别为 1 和 0。保留静置末姿与冻结姿态，并以实际冻结姿态核查回放。

[水壶总览](../../../outputs/experiments/robocasa-cup5-demo/analysis/20261124-cup5-variants-r03/kettle-overview.png) · [方块总览](../../../outputs/experiments/robocasa-cup5-demo/analysis/20261124-cup5-variants-r03/box-overview.png) · [逐例图](../../../outputs/experiments/robocasa-cup5-demo/analysis/20261124-cup5-variants-r03/details/) · [逐组站位统计](../../../outputs/experiments/robocasa-cup5-demo/analysis/20261124-cup5-variants-r03/selection.json) · [同站 RGB 重载核查](../../../outputs/experiments/robocasa-cup5-demo/analysis/20261124-cup5-variants-r03/independent_rgb_reload.json)
