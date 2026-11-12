# build-scenes 阶段：八类任务模式的具体构造方法

- 状态：active
- 日期：2026-10-26
- 替代：无（首次按当前源码整理）
- 关联：[需求](../requirement/01-task-mode-construction.md)、[实施方案](../plan/01-task-mode-implementation.md)、[构建交付](../delivery/01-build-delivery.md)、[运行清单](../delivery/05-run-inventory.md)、[任务模式配置](../../../configs/task_modes/README.md)

本文说明 `build-scenes` 在当前实现中**具体怎么为八类模式制造候选困难**，以源码为准，不重复需求文档的验收口径。文中阈值来自 `configs/task_modes/mode_defaults.json`，可整体替换但不在单次运行中逐样本调整。

## 1. 阶段定位

`build-scenes` 只产出**物理合法、几何上具备目标对比的候选场景**：

- 输出 `proposal_intent`（构造意图），不输出 `final_labels`、`intent_hit` 或成功率。
- 代理（cheap probe）通过、物理检查通过、场景冻结，都**不等于**该模式成立；模式只能由 `evaluate-stations` 的真实 trial 判定。
- 每个请求单元 `(task, intent, variant)` 都写一行记录，`accepted/rejected/skipped` 都保留真实原因。
- 构建阶段不跑 IK、TrajOpt 或任务 rollout；只有廉价几何和必要的一次真实初始化。

## 2. 入口与输入

```bash
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/task_modes/panda_omron_task_modes.json \
  --output-dir outputs --run-id <run-id> --gpu <idle-gpu> [--build-workers N]
```

- 配方 `schema_version` 必须是 `scene-dataset-recipe-v2`；固定 PandaOmron `robocasa-v1` 与三相机，采样圆固定 1.0 m / 0.25 m（`scenes/dataset_config.py:98`）。
- 配方给出：来源房屋、`operations`、`modes`、每意图 variant 数、每任务最多 20 个最终姿态、资产索引、执行配置与模式配置。
- `--preview-dataset` 是另一条互斥路径，只给已有 run 补渲染，不重新构建。
- 父进程唯一写 `scenes.jsonl`；房屋 worker 只返回小结果，活跃 Simulation 不跨进程传递（`workflows/scene_datasets.py:192`）。

## 3. 执行总流程

```text
房屋 worker：加载来源模型
  → 发现 Pick/Open 任务并预筛
  → 每个选中任务建一次 BuildContext
      → 按 modes 顺序 × variants_per_intent
          → 提案 → 廉价代理 → 可选真实编辑事务 → 沉降后复查
          → 最终站位选择 + 最终代理复查 → 冻结
  → 父进程逐行写 scenes.jsonl / summary.json
```

### 3.1 任务发现与预筛

`_select_tasks()`（`workflows/scene_datasets.py:104`）基于真实加载模型做四件事：

1. `discover_tasks()` 产生 Pick/Open 任务。Pick 要求自由关节且最小边不超过 `max_pick_object_size_m`；Open 要求类别在白名单内、关节为 hinge/slide、有可用把手与未完全打开的行程（`scenes/tasks/discovery.py:45`）。
2. 抓取必须是有效数据抓取：`grasp_candidates(..., allow_fallback=False)`，缺失即 `missing_valid_data_grasps`，不用 bbox pinch 补齐。
3. Pick 必须有合法支撑面（`support_regions`）；否则 `missing_valid_support`。
4. 采样圆内必须有来源房间地面三角形，否则 `no_source_floor_in_sampling_disk`。

通过预筛的任务由 `spatial_select()` 按操作类型选入，上限为 `max_pick_targets_per_house` / `max_open_targets_per_house`；评分优先覆盖不同房间和支撑面，再最大化目标间距。`-1` 表示全选。

**目标在 builder 之前就已固定。** `_select_tasks()` 返回的每个 task 自带 `target_body`、`asset_id`、`category`、`anchor_world`、抓取引用和 instruction；`BuildContext.prepare()` 再把它解析成 body id、锚点、抓取集合和共享几何。`build_task()` 之后才按 `modes` 创建 builder，builder 只能读 `ctx.task`、`ctx.geometry`、`ctx.grasps`，没有重新选目标或换类别的接口。

唯一例外是 Reachability 的 `MoveTargetOnSupport`：它在同一支撑面上平移**同一个** target body 0.25 m，`_check_edit_intent()` 强制 `move.body == ctx.task['target_body']`、支撑面属于本任务、且只允许一个移动；不允许换实例、换类别或换支撑面。其他模式新增的资产只能是障碍物，并由 `AssetSelector` 排除 target 同类。

| 阶段 | 目标相关的决定 |
|---|---|
| `discover_tasks()` | 枚举候选 task：Pick 要求自由关节且尺寸 ≤ `max_pick_object_size_m`；Open 要求白名单类别、hinge/slide 关节、未完全打开且有把手。task ID 绑定实例名与关节名 |
| `_select_tasks()` | 配方 `targets` 选择器 → 有效数据抓取 → Pick 支撑面 → 采样圆内有来源地面；不满足的写 `exclusions` |
| `spatial_select()` | 按 `max_pick_targets_per_house` / `max_open_targets_per_house` 选入，优先覆盖不同房间和支撑面，再最大化目标间距；`-1` 表示全选 |
| `BuildContext.prepare()` | 固定该 task 的目标、锚点、49 点 lattice、抓取、支撑与导航几何 |
| `ModeBuilder` | 只在该固定目标上制造困难；除 Reachability 的同支撑面平移外不改目标 |

同一房屋可产生多个 task（多个 Pick 目标、多个 Open 关节），每个 task 是独立构建 job、可换目标；但同一个 S0/probe/S1 对照始终固定目标与初始场景，跨样本才允许换目标或类别。

**每个房屋默认选几个目标**：配方 dataclass 默认 `max_pick_targets_per_house=3`、`max_open_targets_per_house=1`；当前主配置 `configs/task_modes/panda_omron_task_modes.json` 显式覆盖为 Pick 1、Open 1。设为 `-1` 表示该操作类型全部合格目标都选。数量是“该房屋最终进入构建的 task 上限”，不是场景中所有候选目标。

**每个 task 尝试几个 builder**：`build_task()` 按 `recipe.modes` 顺序遍历。Pick 任务对 `articulated_interaction` 直接写 `skipped`，其余 7 类都会尝试；Open 任务 8 类都会尝试。每类再按 `variants_per_intent`（默认 1）重复。因此：

| 配方 | 每房屋最多 variant 单元 |
|---|---|
| 默认 3 Pick / 1 Open | 3×7 + 1×8 = 29 |
| 当前主配置 1 Pick / 1 Open | 1×7 + 1×8 = 15 |

“尝试”指该 builder 进入 `search_variant` 搜索，不等于一定产生物理候选：可能因 `asset_pool_empty:<role>`、`no_navigable_station_pair`、`no_navigable_valid_view_pair` 等在提案或廉价代理阶段快速拒绝。

### 3.2 每任务共享准备（BuildContext）

`BuildContext.prepare()`（`scenes/build/context.py:75`）对同一 `(scene, task)` 只做一次：

| 步骤 | 实际内容 |
|---|---|
| 基线 | `clone_preparation(source_sim)`，之后只读；候选编辑从不叠加在上一 variant 上 |
| 停机器人 + 静置 | `park_robot()` 把机器人放到采样圆外的合法停车位，`settle_scene()` 静置基线 |
| 目标几何 | Pick 的锚点为目标点云包围盒中心；Open 为把手局部中心经把手位姿变换 |
| 支撑面 | `support_regions()`；Pick 无支撑直接拒绝该任务 |
| 站位 lattice | `disk_samples(anchor, 1.0, 0.25)` 生成 49 个 XY 点，默认朝向目标 |
| 占据/导航/余量 | `scene_occupancy` → `navigation(footprint_radius, margin)`、`clearance_base(chassis_radius)` |
| 相机 | `robocasa-v1` 必须通过编译后绑定/外参/FOV 校验，否则该任务准备失败 |
| 资产 | `AssetSelector` 读离线索引；`RuntimeGeometryCache` 延迟加载真实几何；`ObstacleEditSession` 统一执行编辑 |

准备失败时，该任务的每个 `(mode, variant)` 都写一条 `task_preparation_failed` 拒绝（`workflows/scene_datasets.py:137`），不伪造 accepted。

### 3.3 单个 variant 的搜索循环

`variant` 是构建请求单元 `(task, proposal_intent, variant_index)`。同一 task 的同一意图请求 `variants_per_intent` 个 variant（默认 1），每个 variant：

- 都从同一个只读 `BuildContext` baseline 出发，**不叠加**其他 variant 已应用的编辑；
- 拥有独立的搜索预算（32 提案 / 3 物理候选 / 1 repair / 1800 s）；
- 有独立的 `scene_id` 后缀 `-v000`、`-v001`……，最终最多接受一个 proposal；
- 随机数由同 task 的同一个 `default_rng(seed)` 按遍历顺序连续消耗，`begin_variant()` 只重置 builder 的提案迭代器和 `exhausted_reason`，不重置 rng。

`proposal` 是 variant 内部的候选方案（站位、资产、编辑、修复建议）。一个 variant 最多尝试 32 个 proposal，但只保留第一个通过全部检查的结果。因此“7 个 builder × 1 variant”得到 7 个 variant 请求、最多 7 个 accepted 场景；把 `variants_per_intent` 调到 2 则同一意图再独立搜索一次，可能得到另一套站位/编辑。

旧 Case1/2/3 已退出；`variant` 只是同意图的重复构建次数，不携带新的任务语义。

`search_variant()`（`scenes/build/builder.py:14`）的执行顺序：

```text
最多 32 次 proposal
  1. builder.propose() 取一个候选（迭代器耗尽则停止）
  2. builder.cheap_validate() 纯几何代理；不通过则记 cheap_rejected 继续
  3. 无编辑候选：直接使用共享 baseline
     有编辑候选：占用一个物理候选额度（最多 3 个），先加载真实资产
  4. 在真实仿真中实例化 → 静置 → 碰撞/支撑/源场景影响检查
  5. 沉降后用新几何重跑一次 cheap_validate
  6. 选择最多 20 个真实合法站位，用这些站位重跑最终 cheap_validate
  7. 全部通过 → 冻结并写 accepted；否则最多 repair 一次后重试
```

关键的“两道代理”含义：

- 第 2 步是**放置前代理**：用索引尺寸、AABB、栅格、射线，不 compile 资产。
- 第 5 步是**沉降后代理**：物体真实落下后的位姿和几何重新判定，避免“编辑前代理通过”冒充最终构造。
- 第 6 步是**站位级代理**：只有真实初始化成功的站位参与，防止用不可达点满足对比条件。

### 3.4 物理编辑、复查与 repair

`proposal_scene()`（`scenes/build/validation.py:53`）统一走现有构建会话：

1. `_check_edit_intent()` 先做结构约束：每个意图的资产 role 与数量上限（见第 6 节表）、排除目标同类、地面资产必须 floor-compatible、台面资产必须属于本任务支撑面、换目标只允许 Reachability 且同一支撑面。
2. `session.candidate(...)` 应用 `AddObject` / `MoveTargetOnSupport`，位姿在第一次 forward/settle 之前生效。
3. 初始碰撞检查 → `session.settle()` → 与基线做同时长 matched control 对照 → 静置后逐 geom 碰撞、支撑接触、源场景影响、求解健康检查。
4. 通过后用 `ctx.derive(sim)` 重新生成目标几何、支撑、49 点 lattice、占据、导航与 clearance；**编辑前的几何结论不再沿用**。
5. `finally: ctx.session.reject()`，候选状态不累积。

`repair_allowed()`（`scenes/build/repair.py:8`）允许的修复很窄：

- 意图、编辑数量、`pose_edit` 不变；`body/kind/support` 不变。
- 平移不超过 0.05 m，yaw 不超过 15°，且保持局部 z 轴向上。
- 替换资产必须 role 有交集，且宽/深/高各维变化不超过 20%。
- 超出即 `repair_outside_allowed_bounds`，不继续试错。

### 3.5 最终站位与冻结

**proposal 与站位不是一一对应。** `station_hints` 是 `station_id` 字符串列表（如 `S0042`、`S0043-Y01`），不是坐标、不是最终站点全集，也不是机器人执行位置；builder 用它声明“这个构造针对哪些站位”。它是 `BuildProposal` 的字段，由 `select_task_stations()` 解析成真实 lattice 或 `pose_edit` 站点后做初始化和代理复算，并原样写入 accepted 记录的 `station_hints`。各模式的约束不同：

| 意图 | proposal 携带的站位信息 | accepted 对提示站的约束 |
|---|---|---|
| reachability | 自然候选 hint=边缘点+最近点；MoveTarget 无 hint | 只要求最终站点中存在距锚点 ≥0.75 m 的点，hint 不强制 |
| ground_obstacle | hint=[S0, S1] | 两个 hint 必须出现在最终合法站点中，否则最终代理失败 |
| visibility | hint=[S0, S1] | S0 必须被选中且三相机测量完整低可见；S1 必须被选中 |
| arm_path_collision / approach | hint=[S0, S1] | 两个 hint 必须有效，通道判据在最终站点上复算 |
| narrow_space | 天然候选无 hint；编辑候选先 hint=[S0] | 最终在全部 stations 上重新找低/高余量对，并改写 `station_hints` |
| orientation | hint + `pose_edit` 覆盖同 XY 的全部 yaw | 至少两个同 XY、不同 yaw 的 station 必须被选中 |
| articulated_interaction | hint=首尾两点 | 只要求最终 stations 非空 |

提示站未通过真实初始化时，Ground/Visibility/Arm 类不会用别的站悄悄满足条件，而是让该 proposal 失败；未写入 hint 的站点仍按 coverage 顺序补齐。

`select_task_stations()`（`scenes/stations/sampling.py:227`）的优先级是：

```text
station_hints → pose_edit 显式站位 → coverage_select 覆盖顺序 → 全部 lattice
```

- 每个候选在独立 clone 中做真实机器人初始化；碰撞、无地面、求解告警都记 `geometry_filtered`。
- 最多保留 20 个合法姿态；Orientation 的每个 yaw 都占一个姿态名额。
- 显式 yaw 在 `initialize_station()` 中原样保留，不再调用 `aim_station()` 自动朝向目标（`scenes/stations/sampling.py:66`）。
- 无编辑候选共享冻结 `scene-files/<prefix>/baseline`；有编辑候选冻结到 `scene-files/<scene_id>`。
- accepted 记录中 `task_completion` 固定为 `unknown`——构建通过不代表任务可完成。

### 3.6 机器人初始化的边界：测量态，不是执行放置

`build-scenes` 不把机器人放进场景执行任务：不 `Simulation.begin()`、不 stepping、不生成 rollout，冻结的 `model.mjb + initial.npz` 里机器人停留在停车位。

但它**会**在未开始仿真的独立 clone 中初始化机器人，用于三类测量：

| 用途 | 发生在哪里 | 是否写入冻结场景 |
|---|---|---|
| 停车并静置基线 | `park_robot()` / `settle_scene()` | 停车位随准备态快照保存 |
| 判断站位初始化是否合法 | `nominal()`、`select_task_stations()` 的独立 clone | 否；只把合法站位的 `initial` 写进 `scenes.jsonl` |
| 读手臂 body 位置、手指点云、三相机可见性 | `arm_channels()`、`ray_visibility()` 的独立 clone | 否；只保存代理指标 |

这些初始化都受 `preparation_guard()` 约束（未开始、未关闭），测量完立即关闭 clone，不改变 `ctx.baseline`，也不产生任何“任务已执行”的证据。`scenes.jsonl` 中的 `stations[].initial` 是**待标定使用的初始姿态**，由 `evaluate-stations` 在独立 trial 中重新初始化，不是已经放置并运行过的状态。

因此：“build-scenes 不涉及机器人放置”在**执行语义**上成立；但它在**准备态测量**上依赖机器人初始化。Arm-path/Approach 所需的站位相关手臂几何正来自这类测量，详见 5.4、5.5。

## 4. 八类模式共用的代理工具

| 工具 | 位置 | 作用 |
|---|---|---|
| `lattice_points(navigable=)` | `scenes/modes/geometry.py:23` | 过滤占据/无栅格格点；`navigable=True` 再用导航 free 过滤 |
| `nominal()` | `geometry.py:33` | 在 clone 中真实初始化该站位，缓存结果；有效时用三相机射线算 `visibility_proxy` |
| `proxy_visibility()` | `geometry.py:61` | 用相机→ROI 射线再扣除新增 AABB，得到三相机聚合可见性 |
| `segment_boxes()` | `geometry.py:78` | 线段对膨胀 AABB 的 capsule/AABB 代理 |
| `navigation()` | `geometry.py:119` | 有编辑且未实例化时，按新增 AABB 在导航格上膨胀，并同步更新 clearance |
| `role_profiles()` | `geometry.py:144` | 按 role、支撑类型、可用尺寸、排除目标同类选 3–5 个资产 profile |
| `asset_candidates()` | `geometry.py:159` | 用索引尺寸直接生成放置代理；跳过与 target 包围盒相交的放置 |
| `resolve_assets()` | `geometry.py:196` | 廉价代理通过后才加载真实几何并精确放置 |
| `shift_repair()` | `geometry.py:217` | 沿指定方向平移 0.05 m 的一次修复 |
| `arm_channels()` | `geometry.py:233` | 由真实初始化的手臂/手指几何构造名义通道 capsule，不含 IK |
| `channel_blocked()` | `geometry.py:275` | 判断通道是否被 AABB 膨胀后阻挡 |

## 5. 八个模式的具体做法

### 5.1 Reachability / `reachability`

**目标**：优先选择目标周围工作空间边缘的站位，必要时把目标移到支撑面边缘。

**候选生成**（`scenes/modes/reachability.py:12`）：

1. 取全部非占据 lattice 点，按到锚点距离降序。
2. 距离小于 `radius - spacing = 0.75 m` 时停止——只保留外圈。
3. 每个外圈点生成一个无编辑提案，hint 为 `[该点, 最近点]`。
4. 仅 Pick 任务追加 `MoveTargetOnSupport`：沿 ±x/±y 平移一个 `spacing = 0.25 m`，要求目标点全部落在支撑面边界内（留 0.01 m）且支撑射线检查通过；顺序由 rng 打乱。

**廉价代理**（`reachability.py:43`）：

- 有目标移动且未实例化时，锚点和 lattice 按新目标位置重算，并做占据过滤。
- 判据只有一个：存在站位到锚点距离 ≥ `0.75 m`（`workspace_edge`）。否则 `workspace_interior_only`。
- metrics 记录站点距离范围、`arm_working_reach_m`、`local_base_radius_m`，并明确 `scope` 是“允许移动底盘的粗边缘代理，不是运动学不可达证明”。

**编辑/修复**：无编辑或一次同支撑面 MoveTarget；该类没有 `repair()`。

**不宣称**：边缘站位本身不是困难证据；Reachability 标签要等标定后 IK 可行比例近零且有高率 S1。

### 5.2 Ground Obstacle / `ground_obstacle`

**目标**：制造“S0→操作区的直线被地面障碍挡住、但 A* 可绕行”的走廊。

**候选生成**（`scenes/modes/ground_obstacle.py:11`）：

1. 可导航 lattice 按到锚点距离排序，最近 5 点为 goals，最远 8 点为 starts，笛卡尔积中距离 ≥ 2 格（0.1 m）的对。
2. 先发前 8 对**无编辑**提案：既有的家具如果已经挡住直线且能绕行，可以直接成为天然候选。
3. 请求 `floor_obstacle` role。对随机排列的走廊对，在起点—目标连线的 0.5、0.35、0.65 三个位置生成放置代理。

**廉价代理**（`ground_obstacle.py:44`）：

- 两个 hint 必须仍是可初始化 lattice 点。
- 在（可能的）新增障碍膨胀后的导航格上：`segment_free(start, goal)` 必须为 **False**（直线被挡）。
- `search(start, goal)` 必须有路径，且 `detour_ratio = path_length / direct_length ≥ 1.20`（`construction_detour_min`）。否则 `no_sufficient_detour`。
- 沉降后会再算一次，确保物体落定后仍成立。

**编辑/修复**：一个 `floor_obstacle`，数量上限 1。修复沿走廊垂直方向平移 0.05 m（向边缘释放空间）。

**记录**：`proposal_metadata.corridor_xy`、`s0_hint`、`s1_hint`；accepted 的 `station_hints` 只表示构造走廊两端，不是已测成功率。

### 5.3 Visibility / `visibility`

**目标**：制造“至少一个站位聚合可见性低、另一个可导航站位明显更好”的对照。

**候选生成**（`scenes/modes/visibility.py:11`）：

1. 遍历可导航 lattice，用 `nominal()` 做真实初始化，收集前 8 个合法站位及其三相机 `visibility_proxy`。
2. 先发前 8 个站位对的无编辑提案（天然遮挡也可能成立）。
3. 不足 2 个合法站位 → `no_navigable_valid_view_pair`。
4. 请求 `visual_occluder`，最小高度 = 目标点云 z 极差 + `gripper_width_m/2`；没有支撑面时放地面。
5. 对每个起始站位的三相机位置，在“锚点→各相机位置”连线的 0.25、0.5 处放置遮挡物，再与任意其他站位组成 S0/S1 提案。`metadata.ray_band_origins` 保存相机位置依据。

**廉价代理**（`visibility.py:53`）：

- S0（起始站位）必须三相机测量完整（`complete=True`）且 `V < visibility_low = 0.20`；S0 **不要求操作成功率低**。
- S1（另一站位）的已知最大可见性 `known_max > visibility_good = 0.60`，且 `V(S1) - V(S0) > visibility_gain = 0.30`。
- 两者之间必须直线可达或 A* 可导航，否则 `better_view_not_reachable`。
- 任一测量缺失返回 `unknown`，不当成低可见；`unknown` 也不会进入 accepted。

**编辑/修复**：最多一个 `visual_occluder`；修复时把遮挡物朝锚点方向平移，即在“相机→目标”遮挡带上微调。

**注意**：代理用 `max_over_cameras`；S1 允许部分相机未知，只要一个已知相机达到高可见；S0 则要求三路全部已知且聚合低。

### 5.4 Arm-path Collision / `arm_path_collision`

**目标**：阻挡名义手臂通道，但保留另一站位/另一条通道的可行性。

**候选生成**（`scenes/modes/arm_path.py:17`）：

1. 收集前 6 个 `nominal` 合法的站位；不足 2 个 → `no_station_pair_for_nominal_channels`。
2. 前 8 个站位对先发无编辑提案。
3. 请求 `arm_obstacle`；没有支撑面时放地面。
4. 对每个站位对计算 `arm_channels()`：
   - 通道点为手臂前段 body 位置（`arm` 组中第 1、第 4 个 body 的世界坐标）与 grasp 的 pregrasp 点（名义关节姿态，不求解轨迹）；
   - 半径取各手臂物理 geom 最小跨度一半的中位数；
   - grasp 来自真实数据抓取，按距 TCP 排序，最多取执行配置的 `max_grasp_candidates` 个。
5. 在这些通道段的 0.4、0.7 处放置资产；先以 1 个物体提案，代理不通过则从未被挡的通道里挑“覆盖增益最大且与已选物体不重叠”的第二个，最多到 3 个。达到代理要求立即停止增加。

**廉价代理**（`arm_path.py:68`）：

- S0 的**所有**名义通道都必须被挡（`all(blocked)`）。
- S1 至少有一条通道不被挡（`alternate`）。
- 非 Approach 模式还要求 grasp 目标邻域不被物体覆盖（`goal_clear`），避免把目标整个埋住。
- 通道为空返回 `unknown`（`nominal_channels_unavailable`）。
- `scope` 明确为 capsule 几何，不含 IK/TrajOpt，也不是已验证的参考轨迹。

**自适应增加依据**：每增加一个资产，都只用三样信息重算——已选资产的 `world_bounds`、baseline 场景障碍 AABB、两个站位下的名义通道。从未被挡的通道里按“多挡几条通道”排序，排除与已选 AABB 重叠的资产；达到 pass 或 3 个上限即停。真实资产几何要到 `resolve_assets()` 才加载，沉降后还会用新几何复测一次。

**build 阶段看得到/看不到**：看得到抓取位姿、TCP、夹爪与手臂几何、站位初始化后的手臂位置；看不到真实关节轨迹、IK/TrajOpt 可行性和精确碰撞形状。

**编辑/修复**：1→2→3 递增；修复朝远离锚点方向平移，避免越修越埋住目标。

### 5.5 Approach / `approach`

**目标**：限制真实数据给出的 `pregrasp → grasp` 接近走廊。

**实现**：复用 `ArmPathCollisionBuilder`，只改三个参数（`scenes/modes/approach.py:5`）：

```python
mode = 'approach'
role = 'approach_obstacle'
maximum = 1
approach = True
```

差异：

- 通道只有 `pregrasp → end` 两点，半径使用夹爪点云包围半径，而不是整条手臂。
- 最多一个 `approach_obstacle`。
- 代理跳过 `goal_clear` 检查，因为要求就是限制接近方向。
- `metadata.channel_type = 'pregrasp_to_grasp'`。

**build 阶段可拿到的信息**：真实数据抓取的 grasp 位姿、TCP、夹爪几何、各站位初始化后的手臂位置，以及由 grasp z 轴与 `GRASP_APPROACH_DISTANCE_M` 定义的接近方向。够用来判断“这条 corridor 在几何上被 AABB 挡住、另一站位仍有可接近方向”；拿不到求解器在这条 corridor 上的 IK/TrajOpt 结果，因此不能提前宣称 Approach 失败。`resolve_assets` 后的沉降复测和最终站位复测仍会执行。

**不宣称**：构建阶段不调用完整 TrajOpt；Approach 标签需要标定后 goalset 通过、pregrasp/grasp 阶段失败的真实证据。

### 5.6 Narrow Space / `narrow_space`

**目标**：制造底盘边界余量很小的站位，同时保留一个更宽、可导航的对照站位。

**候选生成**（`scenes/modes/narrow_space.py:11`）：

1. 可导航 lattice 少于 2 点 → `no_navigable_station_pair`。
2. 先发一个 `natural-narrow` 无编辑提案，由代理自行寻找天然低余量对照。
3. 请求 `floor_obstacle`。按 `clearance_base` 升序取前 8 个站位。
4. 障碍中心距站位 = `chassis_radius + desired + half_width`，其中：

   ```text
   desired = (max(0, navigation_radius - chassis_radius) + clearance_low_m) / 2
   ```

   在 ±x/±y 四个方向尝试。
5. 若单个物体代理不通过，在相反方向再放第二个，最多两个。

**廉价代理**（`narrow_space.py:43`）：

- 在导航格上重算 clearance；只在 free 且 `0 < clearance < 0.08 m`（`clearance_low_m`）的站位中找 S0。
- 找一个 clearance 更大、差值 ≥ `0.10 m`（`clearance_gain_m`）且可达（直线或 A*）的站位作为 S1。
- 通过时就地写入 `station_hints = [s0, s1]`，记录实测余量与增益。
- 找不到合法窄—宽对照 → `no_legal_narrow_and_wide_contrast`。

**编辑/修复**：1→2 个地面障碍；修复沿 `release_direction` 向外释放 0.05 m。

**说明**：这里用的是站位处**底盘物理边界余量**，不是导航膨胀后的 free 值；导航 margin 不重复扣除。

### 5.7 Orientation / `orientation`

**目标**：在同一 XY 上保留默认朝向与 ±45° 显式朝向的对照。

**候选生成**（`scenes/modes/orientation.py:11`）：

1. 随机打乱 lattice 顺序，逐个 XY 尝试 `nominal` 合法点；对每个合法点生成一组 yaw 对照。
2. `yaw_variants()` 生成默认 yaw 与 `yaw ± orientation_delta_deg = 45°`，每个 yaw 有独立 `station_id`（如 `S0043-Y01`）。
3. 每个 yaw 再做 `nominal` 检查：初始化合法且 `visibility_proxy.known_max > visibility_low`。
4. 至少 2 个合法 yaw 才生成提案；`pose_edit=BasePoseEdit(valid)`，hint 为全部 yaw 站位。

**廉价代理**（`orientation.py:28`）：

- 同一 `xy_id`、至少两个不同 yaw、且（有真实站位时）每个站位可见性高于 0.20。
- `scope` 明确为“初始位姿对照，不含成功率结论”。

**编辑/修复**：不增加资产、不改目标；`pose_edit` 只改待冻结的初始姿态。每个 yaw 计入 20 个姿态预算。

**不宣称**：构建只保证显式 yaw 未被 `aim_station` 改回；高低成功率差由标定决定。

### 5.8 Articulated Interaction / `articulated_interaction`

**目标**：为天然 Open 任务准备站位，不编辑资产。

**候选生成**（`scenes/modes/articulated.py:8`）：

- 仅 `operation == 'open'` 生成提案；非 Open 任务在 `build_task()` 中直接写 `skipped`（`mode_not_applicable_to_operation`）。
- hint 取 lattice 首尾两点，实际站位仍按 hint→覆盖顺序选择。
- `metadata` 记录 `joint_name` 与 `post_contact_gate=False`。

**廉价代理**（`articulated.py:16`）：

- 任务为 Open、关节目标与当前初值差 > 1e-4（即准备后未完全打开）、且最终至少有一个合法站位。
- `scope` 明确为“关节与初始化合法，不是完整任务成功”。

**编辑/修复**：无。S0 只要求标定后真实任务失败/低率，不要求先接触把手；S1 必须完整关节任务成功。

## 6. 预算、阈值与结构上限

模式配置（`configs/task_modes/mode_defaults.json`）：

| 项目 | 值 | 作用 |
|---|---:|---|
| `max_proposals` | 32 | 每 variant 提案上限 |
| `max_physical_candidates` | 3 | 每 variant 进入真实编辑事务的原候选上限 |
| `max_repairs` | 1 | 每个物理候选最多一次修复 |
| `variant_timeout_s` | 1800 | 每 variant 墙钟预算 |
| `asset_shortlist` | 5 | 每 role 返回的候选资产数 |
| `construction_detour_min` | 1.20 | Ground 构造绕行比门槛 |
| `visibility_low / good / gain` | 0.20 / 0.60 / 0.30 | Visibility 代理阈值 |
| `clearance_low_m / gain_m` | 0.08 / 0.10 | Narrow 代理阈值 |
| `orientation_delta_deg` | 45 | yaw 对照偏移 |
| `repair_translation_m / yaw_deg` | 0.05 / 15 | 一次 repair 的位姿上限 |

结构上限（`scenes/build/validation.py:22`）：

| 意图 | 资产 role | 数量上限 | 目标移动 |
|---|---|---:|---|
| reachability | — | 0 | 允许一次同支撑面 `MoveTargetOnSupport` |
| orientation | — | 0 | 不允许 |
| articulated_interaction | — | 0 | 不允许 |
| ground_obstacle | `floor_obstacle` | 1 | 不允许 |
| narrow_space | `floor_obstacle` | 2 | 不允许 |
| visibility | `visual_occluder` | 1 | 不允许 |
| arm_path_collision | `arm_obstacle` | 3 | 不允许 |
| approach | `approach_obstacle` | 1 | 不允许 |

## 7. 记录内容与常见拒绝原因

`scenes.jsonl` 每行至少包含：

- 公共：`scene_id`、`proposal_intent`、`status`、`house`、`task_id`、`seed`、`source_split`、`dataset_split`、`build_metrics`。
- accepted：`scene`（冻结路径）、`task`、`stations`（含真实 `initial`、三相机 `visibility`、`clearance_base_m`）、`station_hints`、`edits`（`placements/target_moves`）、`proposal_metadata`、`construction_checks`、`station_sampling`、`task_completion=unknown`。
- rejected：`reason`、`last_checks`（最近一次廉价或物理检查证据）。
- skipped：非 Open 任务的 Articulated。

常见拒绝原因按阶段：

| 阶段 | 典型 reason |
|---|---|
| 提案生成 | `no_navigable_station_pair`、`no_navigable_valid_view_pair`、`no_station_pair_for_nominal_channels`、`asset_pool_empty:<role>` |
| 廉价代理 | `straight_corridor_not_blocked`、`no_sufficient_detour`、`no_aggregate_visibility_contrast`、`no_channel_contrast`、`no_legal_narrow_and_wide_contrast`、`no_visible_yaw_contrast`、`workspace_interior_only` |
| 资产解析/物理 | `initial_edit_collision`、`placement_did_not_fit_same_support`、静置不稳定、支撑或源场景影响不通过 |
| 站位/最终代理 | `no_valid_initial_stations`、`visibility_hint_initialization_unavailable` 等 |
| 预算 | `variant_timeout`、`physical_candidate_budget_exhausted`、`proposal_budget_exhausted` |

## 8. 与标定后分类的职责对照

一句话：**build-scenes 负责“造出合法候选”，标定后的诊断负责“判定模式是否成立”。** 两者以 accepted 场景、`stations[].initial`、`proposal_intent`、`station_hints` 为交界，互不回写。

| 维度 | build-scenes | 标定后诊断/分类 |
|---|---|---|
| 输入 | 来源场景、配方、资产索引、模式配置 | build accepted 场景、`stations`、原始 trials、planning/measurement、有界 probe |
| 主要动作 | 任务发现、共享准备、提案、廉价代理、物理编辑事务、一次 repair、选站冻结 | 真实 cuRobo 标定（每站 3 次）、任务资格、S0/S1 选择、诊断、多标签分类 |
| 是否执行机器人 | 否：只在未开始仿真的 clone 中初始化测量 | 是：真实规划、执行、任务判定和录制 |
| 证据类型 | 几何代理、静置后物理检查、站位初始化合法性、三相机代理可见性 | 原始 trial 成败、规划阶段、执行与录制完整性、测量值与 probe |
| 产出 | `scenes.jsonl`、冻结场景、`proposal_intent`、`station_hints`、构造指标 | `trials.jsonl`、`diagnosis.jsonl`、`final_labels`、`intent_hit`、任务资格 |
| 状态语义 | `accepted/rejected/skipped` | `eligible/no_success/no_high_rate_s1/incomplete`；标签 `matched/unmatched/unresolved/not_applicable` |
| 可修改对象 | 场景物体、目标（仅 Reachability 同支撑面）、待冻结初始姿态 | 不修改场景；只新增 trial、测量和诊断记录 |
| 预算 | 32 提案 / 3 物理候选 / 1 repair / 1800 s | 每站 3 次真实 trial、最多 20 站；每 S0 至多 1 个 IK probe、60 s |
| 不宣称 | 困难、成功率、可完成任务、模式成立 | 不重新构建、不补造 rollout、不把 unknown 写成失败或成功 |

### 8.1 逐模式的“构造”与“判定”分工

| 模式 | build-scenes 只负责造出 | 分类阶段真正要的证据 |
|---|---|---|
| Reachability | 工作空间边缘站位；必要时同支撑面平移目标 | 诊断 IK 可行比例 ≤0.01，且相对目标靠近/侧移 ≥0.2 m；存在高率 S1 |
| Ground Obstacle | 地面障碍或天然结构，使直线被挡且 A* 绕行 ≥1.2 | 统一导航测量：S0→S1 直线不 free、A* 有路径；高率 S1 |
| Visibility | 低可见站位 + 可导航的更好视角；S0 不要求低成功率 | S0 三相机完整且 <0.2；S1 `known_max`>0.6 且增益 >0.3；可达；S0 可高操作率 |
| Arm-path Collision | 名义手臂通道 capsule 被挡，另一站位仍有通道 | 至少一条真实失败 trial：goalset IK 有解、同阶段 TrajOpt 失败 |
| Approach | 真实数据 pregrasp→grasp corridor 被挡 | goalset 通过，pregrasp/grasp 的 IK 或 TrajOpt 失败 |
| Narrow Space | 底盘余量 <0.08 m 的站位 + 更宽可达对照 | S0 clearance<0.08，S1−S0>0.10；仍以真实成功率为高低池 |
| Orientation | 同 XY 的默认 yaw 与 ±45° 显式站 | 同 XY、yaw 不同、非底盘初始相同、可见且初始化合法；成功率差由高低池体现 |
| Articulated Interaction | 只接 Open 任务，不编辑资产 | S0 有真实任务失败/低率；S1 完成完整关节任务 |

### 8.2 交界处的四个“不等于”

1. **accepted ≠ 困难**：build 通过只说明场景物理合法、构造代理成立；困难必须由真实 trial 判定。
2. **proposal_intent ≠ final_labels**：分类会把每个候选 S0 对全部八类规则检查一遍，可能出现 additional labels，也可能完全不命中意图；`intent_hit` 只在 hint 站上统计。
3. **构造被拒 ≠ 任务失败**：`rejected` 只说明这次候选没造出来；`no_success` 是标定完成但零成功；`incomplete` 是记录/基础设施不完整，两者不能互换。
4. **probe ≠ trial**：诊断 probe 只补缺失的 IK/导航/可见性证据，不计入每站 3 次成功率分母，也不生成执行轨迹。

## 9. 当前验证边界

- 已有 accepted 正例的意图：Reachability、Orientation、Ground、Narrow、Arm-path、Approach、Articulated（见[运行清单](../delivery/05-run-inventory.md)第 3 节）。
- **Visibility 在 build 阶段仍无 accepted 正例**：多次 run 的 `no_aggregate_visibility_contrast` / 资产不足，不能据此宣称 Visibility 模式已可构造。
- accepted 数量不等于困难样本数量；例如 Reachability 的边缘站位代理很宽松，最终标签仍依赖标定。
- 构建期未运行 cuRobo、未跑 rollout、未生成成功率；所有 `task_completion` 与最终模式结论保持 `unknown`。

## 10. 源码地图

| 职责 | 文件 |
|---|---|
| 命令分发 | `src/lastmile_dataflow/cli.py` |
| 房屋/任务调度与主记录 | `src/lastmile_dataflow/workflows/scene_datasets.py` |
| 任务级共享准备 | `src/lastmile_dataflow/scenes/build/context.py` |
| 提案、编辑、代理结果类型 | `src/lastmile_dataflow/scenes/build/proposal.py` |
| variant 搜索主循环 | `src/lastmile_dataflow/scenes/build/builder.py` |
| 物理事务与结构约束 | `src/lastmile_dataflow/scenes/build/validation.py` |
| 一次有界修复 | `src/lastmile_dataflow/scenes/build/repair.py` |
| 八类构造器 | `src/lastmile_dataflow/scenes/modes/*.py` |
| 共用几何/资产/通道代理 | `src/lastmile_dataflow/scenes/modes/geometry.py` |
| 站位采样与最终选择 | `src/lastmile_dataflow/scenes/stations/sampling.py` |
| 资产索引选择 | `src/lastmile_dataflow/scenes/loading/asset_library.py` |
| 三相机可见性测量 | `src/lastmile_dataflow/data/rendering/head_views.py` |
| 构造预算与阈值 | `src/lastmile_dataflow/scenes/mode_config.py`、`configs/task_modes/mode_defaults.json` |
