# 目标物体与支撑物的选定规则及 procthor-10k-val 分布

- 状态：active
- 日期：2026-10-26
- 替代：无（首次按当前源码统计）
- 关联：[build-scenes 构造说明](../info/01-build-scenes-modes.md)、[任务模式需求](../requirement/01-task-mode-construction.md)、[主配置](../../../configs/task_modes/panda_omron_task_modes.json)

## 1. 结论摘要

按当前主配置（`max_pick_targets_per_house=1`、`max_open_targets_per_house=1`、`max_pick_object_size_m=0.2`、`gripper_width_m=0.08`）对 `procthor-10k-val` 全部 1000 个 house 做静态复刻：

- 98.4% 的 house 至少能选出 1 个 Pick 目标，89.3% 能选出 1 个 Open 目标，89.1% 两者都有；14 个 house（1.4%）两者都没有，全部是只有一间 Bathroom 的户型。
- 候选池每 house 平均 26.4 个合格 Pick、10.3 个合格 Open。最终选定的 Pick 目标里 **Bed 作为支撑物占 53.4%**，因为 `spatial_select` 的首选规则是“支撑面面积最大”。
- 配置里的 `pick_target_types`（cup/mug/wine bottle/atomizer/salt shaker/pepper shaker/soap dispenser）和 `case1_support_types` **当前代码完全未读取**（全仓库无引用）。按这两组意图类别筛选，只占候选池的 14.9%，占最终选定 Pick 的 13.6%。
- 四种房间类型（Bedroom / Kitchen / LivingRoom / Bathroom）都能产生候选，不存在零候选的房间类型；最弱的是 Bathroom，19.4% 的房间实例没有任何合格目标。

## 2. 当前选择规则

### 2.1 Pick 目标

`discover_tasks()`（`src/lastmile_dataflow/scenes/tasks/discovery.py:45`）枚举 `instance_catalog` 中所有非 `robot_0/` 实例，逐项要求：

1. 实例自身有合格碰撞几何；
2. body 有 `mjJNT_FREE` 自由关节（静态家具上的摆件也算，只要实例本身自由）；
3. 局部 AABB 三边中的**最小边** ≤ `max_pick_object_size_m`（主配置 0.2 m，未配置时退化为 `gripper_width_m`）；
4. `_select_tasks()` 要求存在 droid 数据抓取：`assets/grasps/droid/<asset_id>_grasps_filtered.npz` 非空且变换合法；`allow_fallback=False`，不生成 bbox pinch 兜底；
5. 目标锚点正下方 5 cm 内存在静止支撑体的**水平碰撞面**（水平 box 顶面或 mesh 水平三角面），锚点 XY 落在该面范围内，面两向尺寸均 ≥ 5 cm；
6. 以目标为中心、半径 1 m / 间距 0.25 m 的采样栅格中，至少 1 个点落在来源房间的 `room_*` 地面 mesh 内。

没有类别白名单。任何满足上述几何与抓取条件的自由小物体都可成为 Pick 目标，因此细长物体（BaseballBat、TennisRacket）会因“最小边”通过尺寸检查。

### 2.2 Open 目标

同一函数继续处理 `category` 属于 `OPEN_TYPES` 的实例：

```text
Doorway, Door, Fridge, Microwave, Dresser, Desk,
SideTable, Cabinet, Drawer, Safe, WashingMachine, ClothesDryer
```

要求 hinge/slide 关节、非把手关节、有限行程、初始开度 < 30%（`open_fraction`），并生成至少一个目标开度；有显式 handle body 时优先用把手作为接触点。Open 不需要抓取数据文件，也不需要支撑面，但同样要求采样圆内有来源地面。

### 2.3 支撑物

支撑物不是预定义类别，而是“恰好托住目标的静止水平面”：

`task_support_obstacles()` 先找锚点下方、与目标底面高度差 ≤ 5 cm 的重叠碰撞面；`support_regions()` 取其中**最高**的实际支撑面，允许同一支撑体同高度的共面分块，排除更低层板与桌腿顶面。支撑体必须无关节（动态家具不参与），水平 box 或水平 mesh 顶面，面积两向 ≥ 5 cm。

因此支撑物类别等于场景中实际出现的家具类别，主配置没有任何过滤。

### 2.4 每房屋最终选几个

`spatial_select()`（`src/lastmile_dataflow/workflows/scene_datasets.py:45`）按操作类型分别选入：

- 主配置每 house 最多 **1 个 Pick + 1 个 Open**；`-1` 表示全选。
- 无已选目标时：取支撑面面积最大者，其次 `prefilter_count`，最后 `task_id`。
- 有已选目标时按优先级：**不同房间 > 不同支撑面 > 离已选目标更远 > 支撑面面积 > 预筛点数 > task_id**。
- 全选模式（`-1`）下距离只影响顺序，不丢弃静态合格目标。

## 3. procthor-10k-val 分布

### 3.1 方法

对 `val_0` … `val_999` 全部 1000 个场景，用与 `build-scenes` 相同的参数离线复刻 `_select_tasks()`：编译场景 MJCF、`discover_tasks`、抓取文件检查、支撑面提取、地面采样、`spatial_select`。未 attach 机器人、未执行物理步；机器人 body 被 catalog 排除，地面/支撑几何不依赖机器人，因此任务预筛结果与真实构建一致。

house 覆盖：

| 项 | house 数 | 比例 |
|---|---:|---:|
| 至少 1 个合格 Pick | 984 | 98.4% |
| 至少 1 个合格 Open | 893 | 89.3% |
| Pick 与 Open 都有 | 891 | 89.1% |
| 两者都没有 | 14 | 1.4% |

14 个无目标 house：130、366、465、479、619、625、670、687、710、713、810、870、950、990，全部为单房间 Bathroom 场景。无 Open 的 house 有 107 个（10.7%），多为 Bathroom 单间或没有白名单家具。

### 3.2 Pick 目标类别

候选池共 26,426 个合格实例（每 house 平均 26.4 个）：

| 类别 | 候选数 | 候选占比 | 最终选定 | 选定占比 |
|---|---:|---:|---:|---:|
| Book | 1,819 | 6.9% | 134 | 13.6% |
| Bowl | 1,657 | 6.3% | 19 | 1.9% |
| CellPhone | 1,625 | 6.1% | 99 | 10.1% |
| Pencil | 1,624 | 6.1% | 13 | 1.3% |
| RemoteControl | 1,540 | 5.8% | 99 | 10.1% |
| Statue | 1,347 | 5.1% | 34 | 3.5% |
| Vase | 1,301 | 4.9% | 49 | 5.0% |
| Pen | 1,300 | 4.9% | 16 | 1.6% |
| SprayBottle | 936 | 3.5% | 27 | 2.7% |
| Egg | 913 | 3.5% | — | — |
| Mug | 910 | 3.4% | 15 | 1.5% |
| Plate | 863 | 3.3% | 36 | 3.7% |
| Cup | 455 | 1.7% | 10 | 1.0% |
| WineBottle | 264 | 1.0% | 22 | 2.2% |

最终选定数最多的其余类别：TennisRacket 72（7.3%）、BaseballBat 67（6.8%）、SoapBottle 40（4.1%）、AlarmClock 38（3.9%）、Laptop 37（3.8%）。

配置意图的 7 类（Cup、Mug、WineBottle、SprayBottle、SaltShaker、PepperShaker、SoapBottle）候选 3,949 个（14.9%），最终选定 134 个（13.6%）。

### 3.3 Pick 支撑物类别

| 支撑物 | 候选数 | 候选占比 | 最终选定 | 选定占比 |
|---|---:|---:|---:|---:|
| DiningTable | 8,289 | 31.4% | 188 | 19.1% |
| CounterTop | 6,235 | 23.6% | 190 | 19.3% |
| Dresser | 3,219 | 12.2% | 20 | 2.0% |
| TVStand | 2,178 | 8.2% | 19 | 1.9% |
| Desk | 1,284 | 4.9% | 3 | 0.3% |
| Bed | 1,249 | 4.7% | **525** | **53.4%** |
| Fridge | 1,198 | 4.5% | 1 | 0.1% |
| Toilet | 869 | 3.3% | 31 | 3.2% |
| SideTable | 850 | 3.2% | 6 | 0.6% |
| ShelvingUnit | 521 | 2.0% | — | — |
| Chair | 302 | 1.1% | — | — |
| Sink | 71 | 0.3% | — | — |
| Sofa | 62 | 0.2% | 1 | 0.1% |
| ArmChair | 56 | 0.2% | — | — |
| CoffeeTable | 43 | 0.2% | — | — |

Bed 在候选池只占 4.7%，但最终选定占 53.4%：每 house 只选 1 个 Pick 时，第一目标按支撑面面积取最大，床面几乎总是最大平面。

### 3.4 Open 目标类别

候选池 10,262 个（每 house 平均 10.3 个），最终选定 893 个：

| 类别 | 候选数 | 候选占比 | 最终选定 | 选定占比 |
|---|---:|---:|---:|---:|
| Dresser | 6,342 | 61.8% | 288 | 32.3% |
| Fridge | 2,806 | 27.3% | 400 | 44.8% |
| SideTable | 474 | 4.6% | 83 | 9.3% |
| Desk | 438 | 4.3% | 37 | 4.1% |
| Safe | 114 | 1.1% | 48 | 5.4% |
| Microwave | 88 | 0.9% | 37 | 4.1% |

Doorway / Door / Cabinet / Drawer / WashingMachine / ClothesDryer 在 val 集中没有产生合格候选（Door 多为已开或行程不满足）。

### 3.5 房间类型分布

全 val 共 3,534 个房间实例：

| 房间类型 | 房间实例 | 含该类房间的 house | 有合格 Pick 的房间 | 比例 | 最终选定 Pick | 选定占比 |
|---|---:|---:|---:|---:|---:|---:|
| Bedroom | 1,101 | 720 | 1,087 | 98.7% | 596 | 60.6% |
| Kitchen | 727 | 727 | 725 | 99.7% | 249 | 25.3% |
| LivingRoom | 869 | 739 | 855 | 98.4% | 100 | 10.2% |
| Bathroom | 837 | 617 | 663 | 79.2% | 39 | 4.0% |

- 不存在“零候选”的房间类型。
- 按房间实例，184/3,534（5.2%）没有任何合格目标：Bathroom 162（占 Bathroom 的 19.4%）、LivingRoom 14（1.6%）、Bedroom 6（0.5%）、Kitchen 2（0.3%）。Bathroom 是唯一明显“经常没用”的类型。
- 按最终选定，只有 984 个房间实例承载 Pick（27.8%）、893 个承载 Open（25.3%）；每 house 上限为 1+1，这个比例反映选择上限，不反映场景能力。
- 房间—支撑物交叉（最终选定 Pick）：Bedroom×Bed 525、Kitchen×CounterTop 190、LivingRoom×DiningTable 78、Kitchen×DiningTable 58、Bedroom×DiningTable 52、Bathroom×Toilet 31。
- 房间内候选目标类别：Bedroom 以 Book/RemoteControl/CellPhone 为主；Kitchen 以 Egg/Potato/ButterKnife/SprayBottle/Cup 为主；LivingRoom 以 RemoteControl/Bowl/Pencil/Candle/Plate 为主；Bathroom 以 SprayBottle/Candle/SoapBottle/PaperTowelRoll 为主。

### 3.6 Open 目标的房间分布（最终选定）

Kitchen 459（Fridge 400、Microwave 37、Safe 19、SideTable 3）、Bedroom 333（Dresser 241、SideTable 40、Desk 37、Safe 15）、LivingRoom 68（Dresser 30、SideTable 24、Safe 14）、Bathroom 33（Dresser 17、SideTable 16）。

## 4. 需要注意的问题

1. **意图类别未生效**：`pick_target_types` / `case1_support_types` 只存在于配置 JSON，代码无读取点。若希望只选杯/瓶/罐类，需要在 `_select_tasks()` 增加类别过滤，而不是改配置。
2. **Bed 偏置**：`spatial_select` 的首选目标是最大支撑面，导致过半 Pick 落在床上，且目标多为书、手机、遥控器、球拍。若目标是“桌面小物体操作”，需要调整评分顺序或对支撑物加类别约束。
3. **Bathroom 覆盖弱**：19.4% 的 Bathroom 没有合格目标，主要缺少抓取数据或水平支撑面；14 个只有 Bathroom 的 house 完全无法构建。
4. **Open 候选集中**：Dresser + Fridge 占候选 89.1%、选定 77.1%，其他可开家具很少；门类基本不可用。

## 5. 复现

统计为离线只读扫描，不修改场景、不写 `outputs/`。关键参数与主配置一致；扫描输出为每 house 一行 JSON，含 `eligible_pick`、`eligible_open`、`selected` 与排除原因。若需复跑，用同一流程对 `val_<house>.xml` 编译场景、调用 `discover_tasks` / `grasp_candidates` / `task_support_obstacles` / `support_regions` / `spatial_select` 即可，不 attach 机器人。
