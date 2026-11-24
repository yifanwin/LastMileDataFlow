# RoboCasa 台面取杯场景构建与人工选集

- 状态：done
- 日期：2026-11-23 22:28 +08:00
- 关联：[场景搭建方案](../plan/03-pick-scene-construction.md)、[固定底盘站位方案](../plan/02-fixed-base-station-evaluation.md)

同一冻结厨房（layout 1 / style 1 / seed 42，目标 `cup_3`）生成了 26 个单障碍提案。14 个变体通过物理和独立重载检查，与原厨房一起形成 15 个看图候选。两种障碍都做出了远置、左右 12.5 cm、左右斜前约 16 cm。下面的组保留给人工决定。

## 人工选择清单

总览按行显示相同的站位网格、台面 RGB 和相同左／正／右站位的机身画面。点进具体场景可同时查看每个站位的左右机身相机、碰撞几何俯视图以及杯与障碍的水平净距。[水壶总览][kettle] · [方块总览][box] · [原厨房详情][original]

| 建议成组选片 | 远置 | 左置 | 右置 | 三者共同合法 | 三者共同可见 | 入选理由 |
| --- | --- | --- | --- | ---: | ---: | --- |
| ☐ 水壶 · 直侧 12.5 cm | [远置][kf] | [左][kl125] | [右][kr125] | 40 | 39 | 相同朝向下比较受限方向，左右净距约 4 mm |
| ☐ 方块 · 直侧 12.5 cm | [远置][bf] | [左][bl125] | [右][br125] | 40 | 33 | 与水壶使用相同位置，左右净距各 3.7 mm |
| ☐ 水壶 · 斜前 16 cm | [远置][kf] | [左][klf] | [右][krf] | 40 | 35 | 台面前沿两侧的镜像，净距约 49／44 mm |
| ☐ 方块 · 斜前 16 cm | [远置][bf] | [左][blf] | [右][brf] | 40 | 24 | 比较前侧遮挡，净距约 12 mm |

同一档跨两种障碍比较时，直侧档有 33 个共同可见站，斜前档有 24 个，共同合法站均为 40 个。方块斜前左、右单例分别有 29、34 个可见站。水壶斜前左、右分别有 36、38 个。

- ☐ **水壶直侧 12.0 cm**是额外镜像备选，远置、[左][kl12]、[右][kr12] 共有 40 个合法站、39 个可见站。两侧碰撞轮廓的 XY 投影都有重叠，静置期间两物体之间未检测到接触。
- ☐ **直左 16 cm**是单侧备选，[水壶][kl16]和[方块][bl16]各与自身远置组共有 40 个合法站、39 个可见站。右侧对照超出台面并碰到冰箱。
- ☐ **原厨房**可随任意组保留作为无新增障碍的共同参照。[原厨房详情][original]

人工决定及理由留空。可按表选择整组，或指定场景 ID 与需要退回的理由。

## 筛选依据与结果

- **相同冻结初态。** 每个变体只增加一个自由关节障碍。原生路径注册 `Kettle001` 的物体配置，规则路径直接加入一个可见且可接触的 MJCF box。方块匹配壶的碰撞包围尺寸 **15.61 × 14.14 × 19.86 cm**，质量 **0.30 kg**，摩擦 `[0.95, 0.3, 0.1]`。所有提案固定同一世界 yaw。公用模型 XML 继承父场景，杯、苹果、柜内物体、柜门和机器人各关节的冻结位置与速度均一致。
- **物理筛选。** 台面内缩 1 mm 后覆盖障碍包围盒，以名义支撑面上方 1 mm 放置，原生环境静置 1 s。检查非支撑穿插、实际承载、位姿漂移以及最后 0.25 s 的稳定性。通过案例的杯子最大平移小于 0.007 mm，冻结前恢复共有状态并重查接触。10 cm 左右两档均与杯穿插，12.0 cm 的方块两档仍与杯穿插。原始 16 cm 直右越界并碰冰箱，两种斜后摆法均碰烤面包机。详见[全部提案与拒绝记录][manifest]。
- **共用站位与视野。** 233 个名义站，全场景共享 `S0019-y00`、`S0022-y00`、`S0025-y00` 三个代表站。每个变体重查初始机器人接触和地板覆盖，合法点均与原厨房的 40 个一致。视野采用本批确定的门槛，同一站的两路 256 × 256 机身相机至少一路能完整投影杯子，杯子单独渲染参考面积至少 64 px，实际可见像素至少为参考的 80%。逐站保存出画、投影边界、参考像素、可见像素和比例。[站位与视野记录][selection]
- **独立重载。** 候选快照的共享关节状态与父场景逐项相同，障碍位姿及碰撞 geom 名恢复一致。抽查 `kettle-left-16cm` 和 `box-left-16cm`，在同一正面代表站再次加载并调用原生落位和渲染入口，两相机的最大 RGB 像素差均为 0。[双相机重载证据][rgb]
- **软件回归。** 相关的 13 项测试通过。主环境全量回归 339 项通过、16 项跳过，3 个 RL 测试模块因主环境缺少 `mjlab` 或 `torch_geometric` 无法导入，在已有 actor-critic 环境补跑 6 项并全部通过。
- 本轮验收范围是场景、冻结回放、初态和视野。cuRobo 与 π0.5 的固定底盘抓取仍待实际执行。


## 官方人类示教核查

2026-11-24 21:56 +08:00，逐条核对官方 `PickPlaceCounterToCabinet` 的 `extras/episode_*/ep_meta.json`，以目标 `obj` 的 `info.mjcf_path` 判定资产。人类示教 `pretrain` 108 条、`target` 502 条，均无以 `objaverse/cup/cup_3/model.xml` 为目标的回合。布局 1 / 风格 1 的 40 条回合都在 `target`，其中唯一以杯类为目标的是 `episode_000098`，使用 `cup_5`。台面和柜体引用相同，记录的机器人初始 XY 相差约 12.9 cm。该回合适合在其原始初态回放。范围限于公开的同任务人类示教两套数据。[pretrain 官方包](https://utexas.box.com/s/myhvbjtfii7at6itjepbivcuo1y8krbp) · [target 官方包](https://utexas.box.com/s/ersnvqs6rcy6yge2fy5hmna7d954v9ty)

2026-11-24 21:58 +08:00，以官方模型和记录状态逐帧回放 `episode_000098`，按原速 20 fps 导出左、右和腕部三路相机拼接视频。共 449 帧，完整解码通过，末帧通过 RoboCasa 原生放置判定。[回放视频](../../../outputs/experiments/robocasa-cup5-demo/runs/20261124-episode-000098/cup5-episode-000098.mp4)

## 复跑

在 `robocasa-station-eval` worktree 根目录执行。复用相邻 RoboCasa 原生运行环境，显式将当前 worktree 的源码置于导入路径最前。复跑时给构建和分析各传一个新目录。

```bash
source ../robocasa-native/.venv-robocasa/bin/activate
export PYTHONPATH="$PWD/src"
parent=../robocasa-native/outputs/experiments/robocasa-native-pick/runs/20261113-scenes-r01/scenes/robocasa365__PickPlaceCounterToCabinet__layout1-style1-seed42
unit=outputs/experiments/robocasa-pick-scene-selection

python scripts/scenes/build_robocasa_pick_variants.py \
  --parent "$parent" --config configs/scenes/robocasa_pick_variants.yaml \
  --output "$unit/runs/20261123-layout1-new" --gpu 0
python scripts/station_map/review_robocasa_pick.py \
  --run "$unit/runs/20261123-layout1-new" --output "$unit/analysis/20261123-layout1-new" \
  --robot configs/robots/panda_omron_robocasa.yaml --gpu 2
```

[kettle]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/kettle-overview.png
[box]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/box-overview.png
[original]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/original.png
[kf]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-far.png
[kl125]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-left-125mm.png
[kr125]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-right-125mm.png
[klf]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-left-front-16cm.png
[krf]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-right-front-16cm.png
[kl12]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-left-12cm.png
[kr12]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-right-12cm.png
[kl16]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/kettle-left-16cm.png
[bf]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/box-far.png
[bl125]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/box-left-125mm.png
[br125]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/box-right-125mm.png
[blf]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/box-left-front-16cm.png
[brf]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/box-right-front-16cm.png
[bl16]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/details/box-left-16cm.png
[manifest]: ../../../outputs/experiments/robocasa-pick-scene-selection/runs/20261123-layout1-r05/scenes.jsonl
[selection]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/selection.json
[rgb]: ../../../outputs/experiments/robocasa-pick-scene-selection/analysis/20261123-layout1-r05-final/independent_rgb_reload.json
