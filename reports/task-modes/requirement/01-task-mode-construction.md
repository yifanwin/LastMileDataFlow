# 任务模式构建与标定后分类：需求梳理

- 状态：active
- 日期：2026-10-25
- 替代：需求层面接续旧 Case1/2/3 构建方式，并替代按机器人臂展、底盘尺寸确定采样区域的要求；本轮尚未修改实现及旧实验结论
- 关联：[完整实施方案](../plan/01-task-mode-implementation.md)、[当前架构](../../../docs/architecture.md)、[资产资料](../../raw_asserts/)、[原站位规则](../../pi05/requirement/02-embodiment-full-calibration.md)、[规划失败分析](../../curobo-time-optimization/analyze/2026-10-25-planning-failure-analysis.md)

## 1. 本轮交付与目标边界

本文为当前实施需求。用户已明确修订 Q1/Q5/Q6/Q7/Q8/Q16，其余问题采用初版默认方案，第 9 节记录最终口径。本轮更新需求文档，不实现 Builder、不修改运行配置、不运行新的构建或标定实验。剩余源码核对、小规模阈值定标属于实施工作，无需再次请求需求确认。

目标流程为：

```text
固定来源场景的 train / val split
    → build-scenes：发现任务、准备共享上下文、制造候选困难、验证并保存
    → evaluate-stations：cuRobo 实际标定
    → 发现低成功率 S0 / 高成功率 S1；Visibility 另选低可见性 S0
    → 对 S0 做 diagnosis probes
    → 结合 S0 + probes + 同一任务的 S1，形成最终多标签结果
    → train：collect-lastmile
    → val：build-bench → evaluate-bench
```

`build-scenes` 只声明构造意图 `proposal_intent`，不声明最终任务模式。Cheap Probe 通过、物理检查通过、场景冻结，均不等于困难模式成立。

任务模式描述：**任务语义保持不变时，某一初始站位完成任务所面对的几何与任务限制**。采用多标签规则，不要求识别唯一因果主因。不同构建样本允许使用不同目标实例或类别；同一个 S0/probe/S1 对照固定目标与初始场景状态，避免用换目标解释站位差异。

当前研究本体为 **PandaOmron，统一采用 `robocasa-v1`，S0/S1 标定不固定底盘**。成功率描述从该初始姿态出发、允许现有底盘规划与运动时的任务表现；不解释成固定底盘操作能力。初版不增加 RBY-1 实验要求。

## 2. 已明确的需求

| 项目 | 要求 |
|---|---|
| 模式范围 | Reachability、Ground Obstacle、Visibility、Arm-path Collision、Approach、Narrow Space、Orientation、Articulated Interaction，共八类，允许多标签 |
| 构造方式 | 同一来源场景可产生不同构造意图；优先天然结构、站位筛选和最小编辑，不再以大量随机 clutter 代替定向构造 |
| 分类时间 | cuRobo 标定之后；必须引用实际 S0/S1 和诊断证据 |
| Visibility | 单独的低可见性链，不要求 S0 的 cuRobo 成功率低；S0→S1 需要可行导航路径 |
| 采样区域 | 半径 `r=1.0 m` 的圆，格点间距 `r/4=0.25 m`，两者与机器人解耦 |
| 标定预算 | 每任务/variant 最多 20 个有效姿态，每姿态 3 次真实 trial；有效点不足 20 时保留实际数量与覆盖率，yaw 对照计入总额 |
| 成功率优先级 | S0 优先 0/3、候补 1/3；S1 优先 3/3，无满足对应模式条件的 3/3 时再找 2/3；不使用 1/3 作为 S1 |
| 示教门槛 | 全部标定没有一条成功轨迹的任务不可作为有效训练/评测任务；不能仅凭构建通过而入库下游 |
| 物体编辑 | 构建事务 → MuJoCo 实例化 → 静置 → 物理/结构与构造代理条件复查；失败时最多 Repair 一次 |
| Repair 范围 | 仅 pose 微调、尺寸相近的同 role 资产替换、支撑面上重新 placement |
| 资产选择 | Builder 请求功能 role；Selector 做语义、role、尺寸、放置可行性和多样性筛选，返回约 3–5 个候选 |
| 语义干扰 | 新增障碍物排除当前任务 target 同类；不默认排除同场景其他任务目标的所有类别 |
| 资产加载 | 离线生成轻量 AssetProfile；运行时只加载索引，不全量扫描/compile；少数最终候选按需加载真实几何 |
| 共享与加速 | 复用目标级 BuildContext、occupancy、clearance、几何与站位 lattice；廉价代理先筛选，再做少量真实初始化 |
| 抽象接口 | 任务模式构造和场景编辑分别有可扩展抽象；实际物理编辑继续复用现有 runtime 构建会话 |
| 代码组织 | 目录与单文件结构整洁、简洁、清晰、可读；允许相关范围内重构，合并重复职责、删除旧 Case 无用逻辑，不叠加平行流程 |
| 相机 | 使用 `robocasa-v1` 全部三相机；左右第三人称相机随底盘，腕相机随手；核对 RoboCasa 一致性 |
| 数据语义 | 原始资产/场景/已有结果只读；不改写失败为成功；unknown、基础设施故障与真实任务失败分别保留 |

## 3. 八类模式的构建与最终判定

表中“构造代理”只用于增加命中概率；“最终判定”在标定之后执行。除 Visibility 外，初版按低成功率 S0 和高成功率 S1 的对照组织。

| 模式 / 标识 | build-scenes 的构造与代理 | 标定后需要的证据 | 需要补齐或探究 |
|---|---|---|---|
| Reachability / `reachability` | 优先选择 target 周围工作空间边缘站位；检查距离、侧向位置和粗 workspace；必要时沿合法 support 轻移目标 | S0 的有效 grasp poses × 多 IK seeds 下可行比例近零；S1 成功率高，且相对目标有明显靠近/侧移 | 明确 IK 分母和预算；区分运动学不可达与碰撞/FOV/限位导致的 IK 失败；“明显位移”阈值待定 |
| Ground Obstacle / `ground_obstacle` | 选择 S0 候选→操作区域的直达 corridor，优先一个 `floor_obstacle`；直线被阻挡、A* 可绕行且 detour ratio 足够 | 存在高成功率 S1，S0→S1 的直线路径碰撞，而相同碰撞模型下的导航规划有路径 | 复用 `segment_free` 和 A*；最终几何标签按直线阻挡且有绕路成立，detour 门槛只作构造筛选，不要求因果排他证明 |
| Visibility / `visibility` | 用全部相机的 camera→target ray / occluder band 定向放置一个高物体，检查聚合可见性代理 | S0 聚合可见性低于 `τ_vis`；S1 高于 `τ_good`，提升超过 `Δτ_vis`，同时为操作高率站位；存在 S0→S1 路径；S0 可有较高操作成功率 | 三相机分别记录并取最大可见性；全部确认低可见才可作低可见 S0；缺失观测不能按零处理 |
| Arm-path Collision / `arm_path_collision` | 用 nominal/reference arm swept volume 放置 `arm_obstacle`，按 1→2→3 个递增，满足代理即停止 | S0 的 goalset IK 有解，但 goalset 轨迹规划失败；存在高成功率 S1 | 按既定阶段规则输出操作性标签，复用约束/连杆日志解释；不为唯一碰撞主因增加强制反事实实验 |
| Approach / `approach` | 利用天然墙面/物体与 pregrasp→grasp corridor；不足时添加一个 `approach_obstacle` | goalset 阶段通过，而 approach 或 grasp 阶段 IK/TrajOpt 失败；存在高成功率 S1 | 统一原生日志与业务阶段命名；区分接近方向受限、目标接触约定错误及后续夹持失败 |
| Narrow Space / `narrow_space` | 用 obstacle-clearance field 找天然低余量区域；不足时一个 floor obstacle，必要时两个 | S0 的底盘边界 clearance 小于 `τ_clear`；高成功率 S1 的 clearance 明显更大 | 首版使用站位处底盘物理边界余量，导航 margin 不重复扣除；路径最小余量另记；不要求因果证明 |
| Orientation / `orientation` | build 预留同 XY 的默认 yaw 与扰动 yaw，不增加资产 | 标定后找到同 XY 高率 S1 和低率 S0；S0 仍可见、底盘无碰撞，实际成功率下降 | yaw 对照计入 20 姿态预算，默认扰动 ±45°；保留显式 yaw，不被自动朝向覆盖；任务 rollout 仍允许底盘移动 |
| Articulated Interaction / `articulated_interaction` | 发现 Door/Drawer/Fridge 等天然 articulated task，第一版不编辑 | 同一 articulated task 有有效失败/低率 S0，且存在高率 S1 能完成完整任务 | S0 只需任务失败/低率，不要求先到达或抓住把手；S1 仍须有完整交互成功证据，基础设施错误不作任务失败 |

### 3.1 分类粒度与证据强度

最终标签归属于 `(variant, task, S0, robot/control/camera profile)`，每个标签关联实际使用的 S1。**存在任意一个满足该标签规则的 S1 即可**，不要求对全部 S1 成立。不同 S0 可有不同标签；同一 S0 的不同标签也可能由不同 S1 提供证据。

任务模式结果不宜仅写成场景级标签：同一场景不同位置可能分别受可达性、遮挡或通路限制。需要场景汇总时再对已判定 S0 聚合，不把构造意图复制给所有站位。

初版采用**几何与阶段规则多标签**，不强排唯一主因，不增加因果排他证明。标签按第 3 节规则产生，记录实际证据范围；Articulated Interaction 按任务类型、S0 失败/低率与 S1 成功判定。

初版规则中以下区别需要保留：

- Reachability 保留 IK 可行比例与约束原因，说明它是在允许底盘运动的既定规划域内测得；不将该标签描述成固定底盘的运动学不可达证明。
- Arm-path Collision 采用 IK 成功、goalset TrajOpt 失败的初版规则；保留碰撞与其他约束日志，但不要求额外实验排除所有其他原因。
- Ground Obstacle 和 Narrow Space 为几何标签，满足 S0/S1 对照规则即可，不要求证明几何限制是唯一失败原因。
- Articulated Interaction 不增加已接触把手或后抓取失败门槛，失败阶段仅作记录。
- 没有运行到某个后续阶段，不代表该阶段已通过或已失败。预算不足、求解异常、信息缺失保持 unresolved/unknown。

先复用原始阶段结果，仅对规则所需信息缺失的候选补必要 probe；不新增大型诊断或因果审计框架。

### 3.2 Orientation 的时序问题

采用初版默认：build-scenes 预先保存相同 XY 的默认 yaw 与 ±45° 扰动候选，evaluate-stations 后再认定高率 S1、低率 S0。每个 `(x,y,yaw)` 视为一个姿态，计入最多 20 个姿态的总预算；至少预留一组有效 yaw 对照，再按覆盖补齐。若没有高低率对照，该 Orientation 候选不命中。

这里固定 XY 仅用于构造和初始对照，实际 trial 不固定底盘。初版“明显下降”按既定低/高成功率分组判断，最小可能差为 `2/3−1/3=1/3`，不额外声称统计显著。

当前初始化会主动朝向 target 并调用 `aim_station`，实现时必须保留显式 yaw。不能在 build-scenes 内暗中跑成功率标定，也不能人为写入失败结果。

## 4. 构建流程与抽象职责

### 4.1 Task Discovery

从实际加载的 ProcTHOR 场景发现 `TaskSpec`。Pick 至少要求目标有效、抓取数据存在且有效、合法支撑、周围存在基本操作空间；articulated task 需要有效关节、操作目标和可用抓取/接触定义。

当前 discovery 能发现 pick/open，但现有 `grasp_candidates()` 对缺失抓取会生成 bbox pinch。正式 Pick 任务必须使用有效数据抓取，不以 bbox fallback 满足数据门槛；首版类别按有效抓取、尺寸、支撑与空间资格筛选，不继续受旧 Case Pick 白名单限制，Bowl/Kettle 等满足资格即可纳入。Open 复用现有把手接触/抓取定义；Close 不纳入首版。

实例、类别允许跨构建样本变化；每个样本保存明确 target 和匹配 instruction。不能新增障碍物后保留会指向另一物体的模糊任务指令。

### 4.2 BuildContext

对同一 `(scene, task, target)` 准备一次公共上下文，包含：

| 内容 | 复用范围与要求 |
|---|---|
| scene / baseline state | 同一任务的所有意图与 variant 共享只读基线，候选不能在上一个 variant 上叠加 |
| stations | 先缓存 1 m / 0.25 m lattice 和廉价几何信息，最终冻结时保存真实有效初始姿态 |
| occupancy | 场景原始占据可共享；按 robot footprint/margin 生成的 inflated occupancy 还依赖本体与 profile |
| clearance field | 保存距障碍物的距离，与从起点出发的导航路程场分开 |
| target / support | 目标几何、支撑面和操作区域复用；移动目标后更新 anchor、support、相关缓存与采样中心 |
| cameras | 外参绑定及当前姿态；底盘 yaw、初始臂姿态或目标位置改变时，更新派生可见性 |
| assets | 常驻轻量 AssetProfile；真实几何由 RuntimeGeometryCache 延迟加载 |

上下文还必须绑定 robot/control/camera profile。采样半径和间距与机器人无关，不代表 inflated occupancy、clearance、相机或可达性可以跨机器人无条件复用。

对象编辑后只更新受影响的派生几何与候选判定；不能沿用编辑前的导航、可见性或站位有效结论。是否局部增量更新，按实际性能收益决定，不预先做复杂缓存系统。

### 4.3 两层抽象

**ModeBuilder：** 描述如何产生某类候选困难；至少提供 `propose(ctx, rng)` 和 `cheap_validate(ctx, proposal)`，需要时提供定向 repair 建议。它不直接修改活跃 Simulation，也不输出最终模式标签。

**SceneEdit：** 描述场景变更；初版可落实为 `AddObject`、`MoveTargetOnSupport` 等具体编辑。BasePoseEdit 只修改待冻结的机器人初始化姿态。统一执行层负责把编辑转成现有构建会话操作，进行实例化、静置、检查与释放。

`BuildProposal` 建议采用统一字段 `proposal_intent`，并包含 `station_hint`、`edits`、可选 `pose_edit`、`metadata`。用户示例中的 `construction_intent` 与 `proposal_intent` 是同一概念，落地时应选一个，不保留两套同义字段。

`station_hint` 建议使用可解析的 station ID/候选集合；显式 yaw 放入 pose 数据。metadata 保存 corridor、occluder band、swept-volume 代理等必要构造依据。

用户建议的 `scenes/build/` 与 `scenes/modes/` 可以承载上下文和模式算法；实际 stepping/settle/事务继续位于 `runtime/construction/`。`physical.py` 如保留，只做现有 runtime 的薄调用，不另建物理执行器。

### 4.4 Proposal → Cheap Probe → Freeze

```text
Task Discovery → BuildContext → Mode Proposal → Cheap Geometric Probe
    ├─ 未通过：在有限 proposal/asset/pose 预算内继续搜索
    └─ 通过
         ├─ no-edit / base-pose edit
         │    → 最终站位初始化检查 → Freeze
         └─ object edit / moved target
              → 加载少量 AssetGeometry → 事务实例化 → Settle
              → 物理、支撑、稳定性、源场景影响与构造代理复查
                   ├─ 通过 → 最终站位初始化检查 → Freeze
                   └─ 失败且可修 → Repair ×1 → Settle → 复查
                                      ├─ 通过 → Freeze
                                      └─ 失败 → rejected
```

no-edit 不需要额外物理重建，但仍使用有效的已准备基线，不能跳过必要站位物理检查。object-edit 沉降后的姿态才是最终构造输入，不能以编辑前的代理通过代替最终复查。

Repair 只针对本候选的一次有限修复；不进入无限试错。轻微穿透/偏移时调 pose，遮挡不足时沿 ray 调整，直路未堵时向 corridor 中心调，绕行被堵时向边缘调，arm/approach blocker 过强或不足时向外/内微调。无法在允许范围内修复则 rejected；新 proposal 的预算与 Repair 次数分别记录。

Arm-path 的 1→2→3 是候选编辑数量递增；Narrow 的 1→2 同理。达到代理要求就停止增加，不能为满足旧 Case 的数量规则继续塞物体。Reachability 的 MoveTargetOnSupport 属于物理编辑，需要完整重建验证。

## 5. 资产索引、角色池与真实几何

### 5.1 三层数据和选择过程

1. **AssetProfile Index：** 离线扫描实际 THOR 资产，读取已有 metadata，必要时 compile 测量尺寸、footprint 和可加载性，补 category/role/support/room 规则，持久化轻量记录。
2. **AssetSelector：** 运行时读索引，依次做语义、role、geometry、proposal 放置条件筛选，再做多样性采样，选 3–5 个候选。
3. **RuntimeGeometryCache：** 仅对通过 Cheap Probe、准备进入精确物理构建的候选，加载 MuJoCo model/data 和局部碰撞几何，复用编译结果。

AssetProfile 按用户定义保存 `asset_id/category/width_m/depth_m/height_m/footprint_m2/movable/loadable/support_kind/compatible_supports/compatible_rooms/roles`。另需能由记录定位源 XML；这是资产输入引用，不是额外派生索引。

尺寸与本地坐标须统一在 THOR y-up → 项目 z-up 适配后测量。`loadable` 要明确表示“源文件能加载”还是“能作为当前 rigid/free-root 障碍物适配”；后者更适合本轮 Selector。

已有 metadata 不一定提供完整 room/support/role 信息。初版可集中维护一份简单类别规则，辅以实测几何，不引入语义模型推断或另一套资产库。

离线索引允许扫描和必要 compile；“只 compile 最终候选”针对在线 build 阶段。孤立资产几何缓存也不能消除编辑后完整场景拓扑第一次组合编译的成本。

### 5.2 角色池初步核对

| Role | 初版候选 | 需要的几何/物理筛选 |
|---|---|---|
| `floor_obstacle` | Chair、OfficeChair、Stool、Footstool、Ottoman、GarbageCan、HousePlant、FloorLamp、DogBed | 稳定落地、有效 footprint，尺寸适合 corridor，保留绕行 |
| `visual_occluder` | Vase、CoffeeMachine、Kettle、Toaster、DeskLamp、HousePlant | 足够高度与投影面积，合法 support，不遮掉所有恢复视角 |
| `arm_obstacle` | CoffeeMachine、Kettle、Toaster、DeskLamp、Vase、足够大的 Bowl/Pan | 与 arm swept-volume 代理实质重叠，不靠目标同类造成指认混淆 |
| `approach_obstacle` | CoffeeMachine、Kettle、Vase、DeskLamp、足够大的 Bowl/Pan | 限制 pregrasp→grasp corridor，保留目标及支撑有效性 |
| `small_clutter` | Cup、Mug、Bottle、Bowl、Plate、Book、Salt/PepperShaker | 只增加多样性，不能凭类别视为强因果障碍；排除目标同类 |
| `support_furniture` | DiningTable、Desk、CounterTop、CoffeeTable、SideTable | 初版优先复用现有家具，不为该 role 批量新增 |

本轮只读检查确认，上表普通 role 类别在本地 THOR 库有对应目录；OfficeChair 位于更深的 RoboTHOR 家具目录。当前 `ThorLibrary` 只遍历两级分类目录，不能由普通类别查询覆盖全部嵌套资产，离线索引要处理这一实际结构。

[AI2-THOR 官方类型表](https://ai2thor.allenai.org/ithor/documentation/objects/object-types/)可核对 Chair、FloorLamp、GarbageCan、HousePlant、Ottoman、Footstool、DogBed 等类型的可移动语义。它不证明本机每个导出资产都能成为稳定障碍物；role 表目前是候选规则，不是逐资产物理验证结论。OfficeChair/Stool 等还应按实际 RoboTHOR 来源核对类别映射。

离线资产索引作为内部脚本提供，不新增第六个公开 CLI。初版默认单一 `assets.jsonl`，复用现有读写依赖；用户示例中的 Parquet 不作为硬要求，不同时维护两种索引。

## 6. 站位、标定、诊断与入库

### 6.1 统一区域与 20×3

圆心沿用 task 的操作锚点 XY：Pick 为目标中心，Open 为把手/接触锚点。半径与间距固定为 `1.0 m / 0.25 m`。

按现有方形 lattice 裁圆算法，该区域有 49 个原始 XY 格点，之后进行 occupancy/footprint 等廉价过滤，再做必要真实初始化，从有效候选中覆盖采样最多 20 点。机器人尺寸只参与碰撞和初始化，不参与圆半径、格点间距定义。

最多 20 个姿态 ×3 次为最多 60 次实际 trial；不足 20 时标定全部有效姿态，记录实际数量与覆盖率，不补造点。Orientation 同 XY 多 yaw 各占一个名额；定向构造的合法 station hint/yaw 对照优先保留，再做其余覆盖采样。

采样圆、导航地图范围和底盘操作约束是不同参数。导航允许离开 1 m 采样圆，在源场景可用房间范围内搜索；底盘操作范围沿用显式执行配置。当前 `scene_grid()` 也被裁在 `config.radius_m` 圆内，需要解耦，不能因为统一采样区域而误判真实可绕行路径。

### 6.2 S0、S1 与允许底盘移动的标定

使用完整原始 3 次 trial 的成功计数，不从平滑热图挑困难点：

| 用途 | 优先集合 | 候补集合 | 规则 |
|---|---|---|---|
| S0 | 0/3 | 1/3 | 先处理 0/3，再使用 1/3；两级记录各自计数与优先级 |
| S1 | 3/3 | 2/3 | 对当前 S0 和模式，先找满足其几何/路径/同 XY 等条件的 3/3；没有时再找 2/3 |

候选总资格为 S0≤1/3、S1≥2/3，但不能只用两个阈值丢失优先级。无满足规则的 2/3 或 3/3 时，不降低 S1 门槛。Visibility 的 S0 另按可见性选取，不受低成功率分组限制，S1 仍遵循高率优先级。

**S0/S1 标定不固定底盘，统一 PandaOmron `robocasa-v1`。**躯干、FOV 与其他规划选项沿用选定 Panda 执行配置，并让原始 trials 和诊断使用相同配置；不为实现本需求额外固定底盘或切换本体。Reachability 的 IK probe 也沿用该规划域，不暗中改成固定底盘测量。

几何阈值（IK 近零、位移、visibility、clearance、detour）按原默认方案在小规模 probe 中定标，再保存为显式配置并在正式批次前固定；不是新的用户确认门槛。默认不为边界站位追加成功率 trials，额外诊断不改变原 3 次分母。

### 6.3 成功示教和高率区域是两道门槛

| 标定结果 | 后续处理建议 |
|---|---|
| 完整且 0 条实际成功轨迹 | 记录 `no_success_demonstration`；不能进入训练任务或有效 benchmark；保留真实负结果 |
| 至少 1 条成功，但没有达到阈值的 S1 | 不视为可恢复模式已成立；可保留成功示教，任务模式配对暂不合格 |
| 有 S1，但没有低率 S0 | 一般困难链没有候选；仍可检查 Visibility 的低可见性 S0 |
| 有合格 S0/S1，但某模式证据不足 | 保留其他已成立标签，该模式 unresolved |
| 标定不完整、基础设施错误或 profile 不匹配 | 记 incomplete/error，不能推断“任务全失败” |

这里的成功示教还要有真实执行与可用记录；仅 planner 成功或文件存在不算。数值标定如果关闭图像记录，复用其成功轨迹按现有记录链补回放观测，并检查导出是否满足训练输入；不能把无观测数据直接当完整 VLA 示教。

“任务构建失败”在流程中应分阶段表达：build-scenes 可以先保存物理合法候选，evaluate-stations 才能判定它是否存在成功示教。建议更新标定结果中的任务资格，不回写覆盖已冻结来源，也不自动删除无用候选数据。

### 6.4 Diagnosis Probes

建议先读 S0/S1 的原始 trial 和已有 `grasp_result`、`solver_trace`、任务 verdict；只有标签仍缺关键证据时，才做有预算的额外 probe。

| Probe | 最小输出 |
|---|---|
| goalset IK | 有效 grasp 数、实际尝试的 seed 数、可行数/分母、误差与主要约束失败原因；不能将未尝试 grasp 计失败 |
| phase diagnosis | goalset/pregrasp/grasp/后续交互的到达与成功状态；统一单站和 batch lane 的阶段映射 |
| navigation | 同一 grid/profile 上的 segment 是否 free、规划是否有路、路径长度/直线距离与 detour ratio |
| visibility | S0/S1 相机与关注区域、指标定义、测量值/差值及有效性 |
| clearance | 站位或路径对应的 clearance、定义与机器人 footprint/margin |
| orientation | 相同 XY、yaw 差、碰撞/可见性合法、两侧实际成功次数与率差 |
| articulated | S0 的真实任务失败/低率、S1 的完整成功、关节初末值/目标值；接触和失败阶段有则记录，不作为 S0 标签门槛 |

probe 不能悄悄取代原始 trial，不计入原 3 次成功率分母；需要额外物理 rollout 时，每次创建新 Simulation/attempt，执行后不重置或传送。只读求解可使用独立未开始的准备状态；其结果标明为诊断，不能伪装为执行轨迹。

Visibility 的 S1 同时满足高可见性、完整任务高成功率和可达路径，以服务恢复示教。关注区域默认 Pick 使用目标实例，Open 使用把手/交互区域。

全部三相机分别测量可见性 `v_c`，使用 `V=max(v_left, v_right, v_wrist)` 表示至少一个相机能有效观察的程度；逐相机值同时保存。低可见 S0 要求三相机测量均有效且 `V<τ_vis`。S1 只要有相机明确达到 `τ_good` 即满足高可见门槛，并满足 `V(S1)−V(S0)>Δτ_vis`；未知值不当作零。姿态和图像尺度固定为相同 profile，初版不使用相机均值，避免单路无遮挡被平均成低可见。

可见性比例默认采用同相机/同姿态下可见目标投影面积与无遮挡参考投影面积的比例，并同时保留原始可见像素数；完全出视野计低可见，渲染失败计 unknown。参考测量只用于几何诊断，不改变已冻结场景或执行观测。具体阈值和低像素处理按小规模 probe 固定。

## 7. 最小数据记录与构造命中率

继续保持每阶段一个主 JSON/JSONL。构建阶段复用 `scenes.jsonl`，标定保留唯一 `trials.jsonl`；最终 S0 模式结果可写入该标定阶段的一份诊断结果表，引用 trial/rollout/S1，不复制原始 trials 或第二套轨迹。

| 阶段 | 必要信息 |
|---|---|
| build-scenes | 来源场景与固定 split、task/target、seed、`proposal_intent`、具体 edits/pose、station hint、构造代理值、物理检查/repair 结果、最终场景与 stations 引用 |
| evaluate-stations | 原始 trial、配置/profile、站位成功计数与完整性、是否存在成功示教、S0/S1 资格 |
| diagnosis/classification | S0 ID、各标签所用 S1、probe 摘要/输入预算、`final_labels`、unresolved 原因、`intent_hit` |

建议统一使用：

```text
expected_labels   = final_labels ∩ {proposal_intent}
additional_labels = final_labels - {proposal_intent}
intent_hit        = proposal_intent in final_labels
```

build 阶段尚未分类时 `final_labels` 未生成，`intent_hit` 未知；不能预先写成 false。已完整判定但不命中时才写 false；如果相关模式证据不足，命中率统计中仍保留 unknown。

以 `(proposal/variant, task)` 统计构造命中，要求其预留的 station hint 或明确候选集合中至少一个 S0 有对应标签；若只在无关站位偶然发现标签，保留发现结果，但不计该定向 proposal 命中。

同时给出构建接受/拒绝数、完整标定数、无成功示教数、有 S1 数、可完成模式判定数、命中数和 unresolved 数。已判定命中率只对完成模式判定的候选计算；全部构造尝试的有效命中产出率以全部 proposal/variant 尝试为分母，包含构建拒绝和零成功任务。unknown 单列，不补成已判定 false。

split 在构造与标定前按来源房屋固定，同一房屋所有 target/intent/variant 继承同一 split。优先复用显式配置的划分；没有既定划分时，按排序后的来源房屋集合、seed=42 做一次 80%/20% 的项目 train/val 划分并保存，后续直接复用，不随成功率、标签或重试改分。现有资产来源为官方 val，保留 `source_split=val`，项目用途另记 `dataset_split`，不能改称官方 train。

## 8. 当前实现核对与差距

以下是本轮源码和少量只读文件检查结果，不是新实验验收结果。

| 能力 | 当前事实 | 本需求需要的变化 |
|---|---|---|
| 构建主链 | `scene_datasets.py` 做发现/资格/构建，按 target×case×variant 调度；Pick 增强、Open 不加物体 | 改为任务级共享上下文内运行不同 ModeBuilder 与 variant；保留薄 workflow |
| Case 逻辑 | Case1 强制一把 Chair 加 5–8 个台面物体；Case2 台面物体数量较多 | 拆除与最小模式编辑冲突的硬规则，不把 Case 字段映射成 final label |
| 目标资格 | 有实际模型目标、支撑与初始站位筛选；缺抓取可 bbox fallback；Pick 类型还有旧白名单 | 明确正式 Pick 抓取数据门槛，以及 Bowl/Kettle 等新示例类别是否纳入首版 |
| 公共计算 | 资格阶段可复用 `checked_stations`；但每个 variant 工作单元仍进入 `augment_obstacles()`，重载基线、park/settle、站位/区域与资产准备 | 按任务聚合工作单元，缓存公共只读状态；无需跨进程共享活跃仿真 |
| 资产选择 | `ThorLibrary.retrieve()` 按类别检索少量资产并 compile；`asset_geometry()` 还会为检索资产构建几何 | 离线 profile + role 筛选 + 延迟几何；不是简单新增一个缓存类名 |
| 已有编译缓存 | `EditAssetCatalog` 有对象级缓存；`ObstacleEditSession` 按资产组合缓存最多 4 个完整模型 | 复用并调整作用域，避免同一任务每个 variant 都重新创建；不能声称当前完全没有缓存 |
| 站位检查 | 每个候选 clone 新 MjData/Simulation，复用已有 MjModel；选中站位记录可见性时还会 clone | 先廉价代理过滤，最后少数真实初始化；clone 开销与 compile 开销应分开计量 |
| 采样参数 | `SceneStationConfig.from_robot()` 用臂展作半径、底盘半径/4 作间距，相关测试锁定旧行为 | 统一 1.0/0.25，并同步配置、测试和当前文档；保留旧结果的原参数说明 |
| 初始化 yaw | 默认对准 anchor，再执行 `aim_station()` | 支持显式 yaw，保存真实最终 yaw，Orientation 对照不能被自动改向 |
| 导航 | 已有 `segment_free/search/search_region/scene_grid`，支持 footprint 膨胀和禁止斜向穿角 | 组合成 Ground/Visibility probe，核对端点、范围、同一碰撞模型；不是重写 A* |
| distance field | `planning.astar.distance_field()` 是起点到自由格的路径距离 | 另补到障碍物的 clearance field；不要直接拿导航距离作 clearance |
| 可见性 | `head_frame()` 有真实 segmentation 的 pixels/image fraction；当前主要观察一个 camera | 定义模式所需比例/关注区/多相机规则，补阈值与差值；渲染失败保持 unknown |
| 求解证据 | cuRobo 保存 queries、IK/TrajOpt success/误差/约束项和 grasp 结果；单次与 batch 都有输出 | 补明确阶段对应和 IK 分母，归纳诊断数据，不重复执行所有已知 probe |
| 主 trial | `_trial_record()` 不直接包含 solver trace，保留 rollout 引用；成功率从原始 trial 计算 | 沿已有引用取得规划证据，避免复制日志进每条 trial |
| Open 执行 | 有把手接近、关节 waypoint、接触丢失和 open verdict 路径 | 验证完整关节交互成功正例，不能仅由 discovery 支持推断 Articulated 已可用 |
| 最终模式 | 本轮检查未见八类统一后标定分类、构造命中率或独立 Visibility S0 选择链 | 新增必要分类与下游筛选，复用现有标定/配对/记录链 |

相关实现入口：[场景工作流](../../../src/lastmile_dataflow/workflows/scene_datasets.py)、[增强准备](../../../src/lastmile_dataflow/workflows/obstacle_augmentation.py)、[当前增强器](../../../src/lastmile_dataflow/scenes/augmentation/builder.py)、[构建会话](../../../src/lastmile_dataflow/runtime/construction/session.py)、[站位采样](../../../src/lastmile_dataflow/scenes/stations/sampling.py)、[A*](../../../src/lastmile_dataflow/planning/astar.py)、[标定选择](../../../src/lastmile_dataflow/data/evaluation/selection.py)、[cuRobo](../../../src/lastmile_dataflow/planning/curobo_v2.py)。

### 8.1 PandaOmron 相机核对

| 相机 | 本轮确认 | 剩余工作 |
|---|---|---|
| `robot0_agentview_left` | `panda_robocasa.prepare_spec()` 在 `mobilebase0_support` 下补装；pos=`[-0.5, 0.35, 1.05]`，fovy=60° | 对照指定 RoboCasa/训练版本的外参、图像方向与预处理 |
| `robot0_agentview_right` | 同函数挂在相同 support；pos=`[-0.5, -0.35, 1.05]`，fovy=60° | 同上；已有同名相机时函数不会覆盖，不能仅凭名称推断姿态一致 |
| `robot0_eye_in_hand` | 实际资产 XML 挂在 `robot_0/robot0_right_hand`；pos=`[0.05, 0, 0]`，fovy=75° | 核对训练版本的腕相机外参、分辨率与观测预处理 |

`configs/robots/panda_omron_robocasa.json` 已列出三相机，主观察相机为 left。普通 `panda_omron` 配置仍有 overview 路线，不能把 RoboCasa profile 的实现视为所有 Panda 配置都已切换。

本轮直接读取的是 `/data1/wenyifan/lmdf/molmospaces_data/assets/robots/panda_omron/model.xml`，仅核对绑定和参数，未重新渲染或做移动联动实验。[既有交付](../../pi05/delivery/03-implementation.md)记录过三相机渲染，但不等于本轮完成与指定 RoboCasa 版本的逐项一致性验收。

另发现 `configs/local.json` 和所查 Panda RoboCasa robot JSON 仍引用 `/data1/wenyifan/molmospaces_data`，本轮该位置未找到 Panda 资产；`/data1/wenyifan/lmdf/molmospaces_data` 对应路径存在。实施实验前应显式校正实际选用配置，不做自动重定位。本轮未修改这些配置。

### 8.2 数据资产能支持什么

- [THOR 资产报告](../../raw_asserts/thor_20251117_资产库内容分析.md)提供几何、类别目录和已有 metadata 缺口说明；它不是已经完成的 role/尺寸/loadability 索引。
- [抓取报告](../../raw_asserts/grasps_droid_20251116_内容分析.md)记录空抓取文件，以及部分 articulated grasp 与当前资产不匹配；发现任务不能只看目录或文件存在。
- [场景报告](../../raw_asserts/procthor-10k-val_20251217_with_occupancy_场景库内容分析.md)描述 1,000 个官方 val 来源场景及 occupancy 图。旧图不能直接代表新增障碍物后的占据，也不能未核对坐标/分辨率/高度规则就替代导航栅格。
- 原有报告的统计与物理成功结论只归属于对应版本。本轮没有重做全库扫描、全资产 compile 或全场景验证。

## 9. 已确定的产品与实验口径

本节已由用户显式选择或授权采用默认方案，不再逐项请求确认。具体实现参数在必要的小规模实验中收敛并记录。

| 编号 | 主题 | 最终口径 |
|---|---|---|
| Q1 | 本体与底盘 | **用户指定：** PandaOmron，S0 标定不固定底盘；S1 与 probe 使用匹配配置，躯干/FOV 沿用现有 Panda 执行配置 |
| Q2 | 首版范围 | **默认：** 八类 Builder/分类接口，Pick/Open，Close 暂不纳入；Pick 按有效数据抓取与物理资格筛选，允许 Bowl/Kettle 等，不沿用旧 Case 白名单 |
| Q3 | 区域与点数 | **默认：** task anchor 为圆心，1 m / 0.25 m；最多 20 个有效姿态，不足保留实际数量，每点固定 3 次 |
| Q4 | Orientation | **默认：** build 预留同 XY 默认 yaw/±45° 对照，标定后定 S0/S1；各姿态计入 20 名额，不新增隐式标定轮次 |
| Q5 | 成功率与阈值 | **用户指定：** S0 优先 0/3、候补 1/3；S1 优先 3/3，无合格 3/3 时找 2/3。**默认：** 几何阈值经小规模 probe 固定，不逐样本调阈值 |
| Q6 | Visibility | **用户指定：** 考虑全部相机。**默认：** 三相机逐一记录，取最大可见性；低可见须全部已知且低；Pick 关注目标，Open 关注把手/交互区；S1 同时高可见、高操作成功率且可达 |
| Q7 | 标签强度 | **用户指定：** 几何多标签；Articulated 的 S0 只需真实任务失败/低率，不要求抓住把手后失败；S1 仍要求完整任务成功 |
| Q8 | S1 存在性 | **用户指定：** 任意一个合格 S1 支持标签即可；多标签，不强制唯一主因，也不要求对所有 S1 成立 |
| Q9 | 无成功及不完整 | **默认：** 零成功、无高率 S1、不完整/错误分别统计保留，不自动删除，不伪造任务失败 |
| Q10 | 诊断预算 | **默认：** 先复用日志，缺少规则所需证据才做有界 probe，预算沿用显式执行配置；不默认追加成功率 trial，probe 单独计数 |
| Q11 | clearance/导航 | **默认：** 站位处底盘物理边界余量，导航 margin 不重复扣；路径最小余量另记，导航可离开采样圆，在可用源场景房间内搜索 |
| Q12 | 目标与类别排除 | **默认：** 排除当前 target 同类障碍物；同一 S0/probe/S1 固定目标与初始场景，只允许跨样本换目标 |
| Q13 | split | **默认：** 按房屋预先固定，优先现有显式划分；无划分时 seed=42 做 80/20 项目划分并保存，保留官方 source_split 与项目 dataset_split |
| Q14 | 资产索引接口 | **默认：** 内部离线脚本，不新增公开 CLI；单一 JSONL 索引，不增加 Parquet 强依赖或重复索引 |
| Q15 | 命中率 | **默认：** 保留 S0 证据，variant/task 汇总限定 station hint/候选集合；已判定命中率与全部尝试有效产出率同时报告，unknown 单列 |
| Q16 | Panda profile | **用户指定：** 首版统一 `robocasa-v1`。**默认：** 对照当前项目接入的 RoboCasa/策略参考版本记录外参与预处理，不另换控制路线 |

## 10. 需要做的小规模探究与验收

| 探究 | 需要回答的问题 | 预期产出 |
|---|---|---|
| 单目标共享准备计时 | target×case×variant 中重复加载、settle、栅格、资产 compile、站位 clone 各占多少？ | 阶段耗时与次数；区分已有缓存命中和重复准备，确定最有价值的改动 |
| role 索引样本 | 不同目录深度、类别别名、y-up、rigid 适配、footprint 定义能否统一？ | 每个 role 少量真实可加载且稳定的代表资产，不重做发布级全库审计 |
| 一组真实 S0/S1 | PandaOmron `robocasa-v1`、不固定底盘时，是否有符合优先级的高低率对照？日志是否足够区分 IK/TrajOpt/approach？ | 一组最多 20×3、保留实际有效点数的真实标定、必要 probe 和证据支持的标签 |
| Visibility 对照 | pixels/image fraction 与遮挡比例差别多大？低可见性 S0 能否保留且到达高可见 S1？ | 固定指标与阈值，包含 cuRobo(S0) 不低但 Visibility 成立的样本 |
| Orientation 对照 | yaw 是否被初始化改回？同 XY 的两 yaw 是否有真实率差，仍满足可见/无碰撞？ | 至少一组实际 yaw 对照；无命中如实报告 |
| Ground/Narrow | 栅格直线、A* 绕行、clearance 与真实几何是否一致？ | 一个可绕行与一个无路负例；解释地图范围和 footprint，不补造 rollout |
| Articulated | 是否存在完整 Open 高率 S1，以及同任务真实失败/低率的 S0？ | S1 关节交互成功证据；S0 不要求建立把手接触；若无合格对照，如实记录 |
| Panda 相机 | 已编译三相机的 parent、外参、fovy 与指定参考版本是否一致，移动是否随正确刚体？ | 少量编译检查和真实图像/联动验证，确认观测口径 |

加速方向明确为：共享准备、索引筛选、栅格/clearance/raycast/workspace 代理、最终少量真实初始化。“Cheap Probe 先筛掉约 90%”作为优化目标测量，不能靠牺牲有效候选或重复计数宣称达到；至少记录 proposal 数、廉价拒绝数、物理实例化数、compile 次数与阶段耗时。

软件验收围绕真实职责：Builder 不修改基线；SceneEdit 与构建会话正确衔接；显式 yaw 保留；visibility unknown 不误标；标签引用 S0/S1/probe；所有零成功和 incomplete 分支保持正确语义；下游按固定 split 与任务资格消费同一份结果。

真实验收独立于软件回归：每个声称支持的模式至少提供一个有实际成功 S1 和完整证据的正例，并保留未命中/rejected/unresolved 的负结果。尚无正例的模式可有接口和候选构造，但不能宣称已经验证成功。

## 11. 文档执行记录与结果

### 首次梳理

1. 起始检查 `git status --short`，工作区干净。
2. 阅读架构、报告入口、原始资产分析和相关源码/测试；核对构建、采样、资产加载、构建事务、A*、标定选择、cuRobo 阶段日志及 Panda 相机配置。
3. 只读解析本机 Panda XML，确认腕相机绑定；检查 role 候选目录及 RoboTHOR OfficeChair 的嵌套结构；查阅 AI2-THOR 官方类型表辅助核对类别语义。
4. 形成本文，列出已明确需求、现有复用点、实际差距、16 项待确认口径和小规模探究任务；更新报告入口。

本轮只新增需求文档并更新其入口。未改源码/运行配置，未运行 unittest、GPU rollout、资产索引构建或新一轮标定；因此没有新增模式成功率、构造命中率或端到端闭环结论。

### 用户选择回填（2026-10-25）

1. 保留已有文档修改，按用户回复更新 Q1/Q5/Q6/Q7/Q8/Q16，其余问题落实初版默认，不保留为待审批项；文档状态改为 active。
2. 同步修改八类模式、Orientation 时序、采样预算、移动底盘标定解释、多相机聚合、Articulated 条件、split 和命中率口径，删除与最终选择冲突的固定底盘/后抓取门槛建议。
3. 更新报告入口，核对文档内部口径、相对链接和补丁格式。本轮仍只修改文档，未改源码/配置、未运行测试或真实实验；几何阈值定标及实现验证留在实施工作中完成。

### 完整方案编写（2026-10-25）

已编写[实施方案](../plan/01-task-mode-implementation.md)，覆盖模块与单文件组织、接口、八模式构造、物理事务、三相机、范围解耦、阶段证据、分类、下游消费、配置/预算、加速、实施阶段和验收。同步纳入用户补充的代码质量与适当重构要求。本轮未实施源码改动或新实验。
