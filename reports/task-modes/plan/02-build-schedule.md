# 任务模式构建计划表

- 状态：active
- 日期：2026-10-26
- 替代：无；将完整实施方案拆为可跟踪的构建任务
- 关联：[已确认需求](../requirement/01-task-mode-construction.md)、[完整实施方案](01-task-mode-implementation.md)

## 1. 执行约定与当前进度

本表覆盖构建、标定诊断、分类、采集和 benchmark，作为实施任务的唯一进度表。详细算法与字段定义查实施方案，不在这里复制第二份设计。

用户于 2026-10-26 将本轮范围收敛为“流程到标定完成即可，后续任务先不用做”。本轮交付 P0–P4（含 S0/S1 与诊断结果）；P5/P6 后续任务暂不推进，已有实现和实验产物保留，不回滚。当前结果见[标定交付](../delivery/02-calibration-delivery.md)。 已完成多组真实完整标定与六类标签正例；Orientation/Visibility 无正例，按负结果/未验证边界交付。

| 已完成的准备 | 结果 |
|---|---|
| 需求整理与口径确认 | PandaOmron `robocasa-v1`，底盘不固定；S0 优先 0/3 再 1/3，S1 优先 3/3 再 2/3；三相机、几何多标签、Articulated 无后抓取门槛 |
| 完整实施方案 | 已明确目录与接口、八类构造、事务、缓存、诊断、下游接入及验收 |
| 构建任务拆分 | 本表列出 P0–P6 的 36 项代码与验证任务；本轮 P0–P4 已完成；P5/P6 按用户要求暂不推进 |

固定约束：1 m 半径、0.25 m 间距，最多 20 个有效姿态×3 次；Orientation yaw 对照计入总额。只保持五个公开 CLI，离线资产索引为内部脚本。build-scenes 不输出最终标签。

## 2. 阶段总表

| 阶段 | 目标 | 主要交付 | 阶段完成条件 | 状态 |
|---|---|---|---|---|
| P0 参数与现状基线 | 拆清范围、身份、配置和计时基线 | 新配方定义、独立范围配置、站位身份规则、旧链路计时 | 采样不覆盖操作范围，移动底盘语义保持，基线有实际来源 | 已完成 |
| P1 资产与共用几何 | 消除运行时全库检索和重复几何准备 | JSONL 资产索引、role Selector、真实几何缓存、occupancy/clearance | 少量代表资产真实可加载；导航与 clearance 定义一致 | 已完成 |
| P2 构建主干 | 统一上下文、编辑、事务、采样和三相机 | BuildContext、ModeBuilder/SceneEdit、任务级调度、三相机测量 | no-edit/pose/object 三条路径可冻结重载，基线不被污染 | 已完成 |
| P3 八模式候选构造 | 实现全部模式 Builder | 八类构造器与廉价代理 | 每类能输出合法候选或明确拒绝；不冒充最终标签 | 已完成 |
| P4 标定与最终分类 | 原始 trial→S0/S1→probe→多标签 | 阶段/lane 证据、优先池、有界 probe、diagnosis.jsonl、阈值配置 | 三次 trial 分母不变，所有模式使用真实证据，未知保持未知 | 已完成 |
| P5 下游接入 | 同一分类结果贯穿训练与评测 | train 采集、val benchmark、去重和多标签指标 | 不重新定义标签、不重复标定、Visibility 可独立入链 | 后续暂缓 |
| P6 真实验证与收尾 | 验证行为、实际能力和加速效果 | 回归结果、真实模式样本、连续采集/bench、性能与交付报告 | 软件与真实验证分开报告；未成功/未验证边界明确 | 后续暂缓 |

## 3. 详细构建任务

文件均相对 `src/lastmile_dataflow/`，`scripts/`、`configs/`、`tests/` 为仓库根目录。拟新增文件按方案落地，允许在不改变职责的前提下合并小模块；不为凑目录增加空壳。

### P0 参数、身份和计时基线

| 编号 | 任务与涉及文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P0.1 | 整理新 recipe、模式/诊断配置和路径；`scenes/dataset_config.py`、`runtime/execution_config.py`、`configs/` | 已确认需求 | 固定 Panda RoboCasa、Pick/Open、1 m / 0.25 m；合法路径显式配置；新字段不混用旧 Case 语义 | 已完成 |
| P0.2 | 解耦采样圆、操作工作区、导航范围；`workflows/collection.py`、`planning/astar.py`、`runtime/execution/`、`runtime/outcomes/` | P0.1 | dataset geometry 不覆盖 execution radius；导航可圈外绕行；操作范围仍按执行配置约束 | 已完成 |
| P0.3 | 统一 target 身份、xy_id/station_id 和显式 yaw；`scenes/tasks/`、`scenes/stations/`、`scenes/dataset.py` | P0.1 | 重编译不改变目标身份；同 XY 多 yaw 有独立站位 ID；显式 yaw 不被 aim_station 改回 | 已完成 |
| P0.4 | 保留改造前实际计时基线与配置引用；现有构建/标定日志及必要计时点 | 已确认需求；须在重构旧链路前完成 | 有加载、静置、资产 compile、station clone、标定等实测计数/耗时；缺少的指标明确未测，不推算伪造 | 已完成 |

### P1 资产与共用几何

| 编号 | 任务与涉及文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P1.1 | 实现 AssetProfile、离线测量与索引脚本；`scenes/loading/asset_profiles.py`、`scripts/build_asset_index.py` | P0.1 | 覆盖嵌套 RoboTHOR 目录；统一坐标和类别；单一 assets.jsonl；loadable 表示可进入障碍物编辑链 | 已完成 |
| P1.2 | 将类别检索改为 role Selector；`scenes/loading/asset_library.py` | P1.1 | 按语义、role、尺寸、support 和放置条件选最多 5 项；排除当前 target 同类；无候选直接记录负结果 | 已完成 |
| P1.3 | 合并真实资产几何加载与缓存；`scenes/loading/assets.py`、`scenes/augmentation/sampling.py` | P1.1 | 不对同资产重复测量 compile；只延迟加载最终少量候选；与 session 组合模型缓存职责分开 | 已完成 |
| P1.4 | 整理 raw/inflated occupancy 和障碍 clearance；`planning/astar.py`、`scenes/augmentation/regions.py` | P0.2 | 障碍间距场与起点路程场分开；底盘余量不重复扣 margin；直线与 A* 用同一导航模型 | 已完成 |

### P2 构建主干、最终站位与三相机

| 编号 | 任务与涉及文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P2.1 | 建任务级 BuildContext，迁入共享准备；`scenes/build/context.py`、`workflows/scene_datasets.py` | P0.3、P1.2–P1.4、P0.4 | 同 task 的 baseline/settle/lattice/occupancy/support 只准备一次；编辑后的派生结果不误用旧缓存 | 已完成 |
| P2.2 | 实现 ModeBuilder、BuildProposal、SceneEdit 和 BasePoseEdit；`scenes/modes/base.py`、`scenes/build/proposal.py` | P0.3、P1.3 | Builder 只提案；SceneEdit 有实际应用职责；no-edit/base-pose/object-edit 可区分；不新增通用插件框架 | 已完成 |
| P2.3 | 接通 AddObject/MoveTarget、静置、检查和一次 repair；`runtime/construction/session.py`、`scenes/build/validation.py`、`repair.py` | P2.2 | 复用现有物理会话；物理/支撑/源场景影响和静置后代理均检查；repair 后仍失败则拒绝；候选资源关闭 | 已完成 |
| P2.4 | 改为 task 内 intent×variant 循环，统一保存主记录；`workflows/scene_datasets.py`、`scenes/build/builder.py` | P2.1、P2.3 | 父进程唯一写 scenes.jsonl；候选间不累积编辑；共享 no-edit snapshot；无职责旧包装退出 | 已完成 |
| P2.5 | 接通粗筛、hints/yaw 优先、覆盖补选与真实初始化；`scenes/stations/sampling.py`、`scenes/dataset.py` | P2.4 | 最多 20 个实际合法姿态，不足保留实际数量；不因覆盖选点丢失对照；不对每 cheap proposal clone 全 lattice | 已完成 |
| P2.6 | 核对 RoboCasa 三相机并实现 ROI/无遮挡参考测量；`data/rendering/head_views.py`、`runtime/control/panda_robocasa.py` | P0.3、P2.1 | 三路绑定/外参有实际核对；取最大可见性；全低才判低；unknown 不当零；参考渲染不改物理场景 | 已完成 |

### P3 八类候选构造器

以下文件均位于 `scenes/modes/`。公共资产选择、物理事务和保存不在八个 Builder 中重复实现。

| 编号 | 模式与文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P3.1 | Reachability；`reachability.py` | P2.5 | workspace 边缘站位优先；天然不足才 MoveTargetOnSupport；移动目标后更新 anchor、支撑和 lattice | 已完成 |
| P3.2 | Orientation；`orientation.py` | P2.5、P2.6 | 同 XY 默认 yaw/±45° 对照可见且初始化合法；计入 20 姿态，不在构建期声明操作失败 | 已完成 |
| P3.3 | Articulated；`articulated.py` | P2.5 | 只消费有效 Open task，不编辑；有合法站位候选；不增加“先抓住把手”的构造或分类门槛 | 已完成 |
| P3.4 | Ground Obstacle；`ground_obstacle.py` | P2.5、P1.4 | 一个 floor_obstacle 优先；proxy 直路被挡且有绕行；静置后仍成立；不硬编码必须 Chair | 已完成 |
| P3.5 | Narrow Space；`narrow_space.py` | P2.5、P1.4 | 天然低余量优先；必要时 1→2 个障碍；仍有合法站位、通路和更宽候选，不以碰撞点充窄点 | 已完成 |
| P3.6 | Visibility；`visibility.py` | P2.5、P2.6 | camera→ROI band 定向放一个 occluder；三路聚合代理降低且有更好视角；不要求 S0 操作低率 | 已完成 |
| P3.7 | Arm-path Collision；`arm_path.py` | P2.5 | nominal capsule/几何通道代理；1→2→3 自适应、满足即停；Cheap Probe 不暗中跑 IK/TrajOpt | 已完成 |
| P3.8 | Approach；`approach.py` | P2.5 | 天然 pregrasp→grasp 限制优先，不足加一个 blocker；保留合法目标和其他接近候选 | 已完成 |

### P4 标定证据、诊断和最终分类

| 编号 | 任务与涉及文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P4.1 | 统一单站/batch phase、lane、seed 与证据引用；`planning/curobo_v2.py`、`pool.py`、`runtime/execution/` | P0.1、P0.3 | goalset/pregrasp/grasp 映射明确；失败可定位到真实 trial/lane；不按日志行数猜阶段 | 已完成 |
| P4.2 | 合并原始计数、完整性和优先池；`data/evaluation/selection.py` | P0.1、P0.3 | S0 0/3→1/3；S1 对每模式先合格 3/3 再合格 2/3；初始化/记录/共享错误不凑任务失败 | 已完成 |
| P4.3 | 实现必要的有界 IK probe；`planning/curobo_v2.py`、`pool.py`、`runtime/execution/protocol.py` | P4.1、P4.2 | 先复用日志；缺分母时才 probe；每 S0 默认≤1 次；底盘及规划域匹配；不改变三次 trial 分母 | 已完成 |
| P4.4 | 实现八类统一分类与每标签 S1 证据选择；`data/evaluation/diagnosis.py` | P3.1–P3.8、P4.1–P4.3 | 检查全部标签；存在一个合格 S1 即可；Visibility 独立 S0；Articulated 无后抓取门槛；未知保留 | 已完成 |
| P4.5 | 接入 evaluate-stations，写任务资格/intent_hit/诊断结果；`workflows/collection.py`、`data/evaluation/diagnosis.py` | P4.4 | 唯一 trials.jsonl；diagnosis.jsonl 引用证据；零成功、无 S1、不完整分开；不覆盖 frozen 来源 | 已完成 |
| P4.6 | 小规模真实标定与几何阈值定标，固定正式配置；`configs/`、阶段交付记录 | P4.5 | 记录实际成功、probe 与测量；阈值按批次固定，不逐 val 样本调；无成功对照如实记未验证 | 已完成 |

### P5 采集与 benchmark 接入

| 编号 | 任务与涉及文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P5.1 | train split、分类 pair 与连续采集接入；`workflows/collection.py`、`data/export/` | P4.6 | 消费统一诊断；高率 tier 一致；跨标签 pair 去重；不重新标定；成功且记录可用才入训练数据 | 后续暂缓 |
| P5.2 | val split、模式 episode 和 oracle/RecoveryRegion 接入；`workflows/lastmilebench.py`、`data/evaluation/recovery.py` | P4.6 | Visibility-only 可入链；一个 S0 一个基础 episode；恢复区域引用该 episode 的合格 S1；oracle 不发策略 | 后续暂缓 |
| P5.3 | 接入多标签统计与完整计划分母；`data/evaluation/benchmark.py`、相关可视化 | P5.2 | episode 总体去重；标签分组允许重叠；coverage/unknown/infra 独立；不把 Case 当模式 | 后续暂缓 |

### P6 验证、性能与代码收尾

| 编号 | 任务与涉及文件 | 前置依赖 | 验收条件 | 状态 |
|---|---|---|---|---|
| P6.1 | 完成改动相关测试与全套软件回归；`tests/scenes/runtime/planning/data/workflows/` | P5.1、P5.3 | 开发中先局部，集成后全套一次；覆盖新算法和数据语义；不为旧 Case 或移动文件保留无意义测试 | 后续暂缓 |
| P6.2 | 八模式真实正负例核对；现有 `tests/smoke/` 按职责扩展 | P4.6、P6.1 | 每个宣称已验证的模式有真实 S0/probe/高率 S1；无正例不伪造，不把模式范围缩减成已验证范围 | 后续暂缓 |
| P6.3 | train 连续执行、val 策略评测和三相机数据读取/回放 | P5.1–P5.3、P6.1 | 实际跑 S0→S1→操作和 bench；分别报告导航、操作、记录、导出结果；未成功不算成功示教 | 后续暂缓 |
| P6.4 | 对照共享准备与端到端性能 | P0.4、P6.2、P6.3 | 比较同来源/同语义的加载、compile、初始化、proposal、probe 耗时；同时报告接受、命中和成功示教数 | 后续暂缓 |
| P6.5 | 完成相关重构收尾、当前文档同步与交付总结；源码、`configs/`、`docs/`、`reports/task-modes/delivery/` | P6.1–P6.4 | 删除失去职责的旧 Case/包装与重复逻辑；目录和单文件结构清晰；说明实际结果、性能和未验证边界 | 后续暂缓 |

## 4. 推荐推进顺序

```text
P0.1 / P0.3 / P0.4 → P0.2
    → P1.1–P1.4
    → P2.1–P2.6
    → P3.1/3.2/3.3 → P3.4/3.5 → P3.6 → P3.7/3.8
    → P4.4/4.5/4.6
    → P5.1–P5.3
    → P6.1–P6.5
```

P4.1–P4.3 可在 P0 的相关依赖完成后提前做，和 Builder 开发交错衔接；P2.6 提前实现三相机基础能力供 Visibility/Orientation 使用。具体先后以任务行的依赖为准，不要求等整阶段全部完成才处理下一阶段的独立任务，也不默认启用多 agent。

每完成一条可执行主链就做一次相应小样验证，问题在当前模块定位修复；不等所有代码写完才首次加载真实资产，也不为每个小变动重复跑全量实验。

## 5. 贯穿各阶段的代码与实验要求

| 项目 | 执行要求 |
|---|---|
| 目录职责 | 构造算法在 scenes，实际物理动作在 runtime，规划在 planning，结果在 data，workflow 只编排 |
| 单文件结构 | 职责说明→导入/常量→数据类型/公共接口→实现→私有 helper；相关逻辑相邻，命名直接 |
| 抽象层数 | ModeBuilder/SceneEdit 保留实际扩展职责；其他使用简单类/函数，不建通用框架或无职责包装 |
| 重构方式 | 先合并重复逻辑和删除旧规则，再整理文件；迁移完移除旧调用，不保留两套构建/判定链 |
| 记录规则 | 原始 trial 唯一；probe 与原三次分开；每阶段一个主结果；轨迹只来自真实执行回调 |
| 物理生命周期 | 原始输入只读；执行后不 reset/传送；独立试验新 Simulation/attempt；候选错误局部隔离，共享故障停止批次 |
| GPU | 实验前查看 0–7 实时资源，命令显式选卡，不固定卡号或干预其他进程 |
| 性能结论 | 不以拒绝全部候选声称加速；“廉价筛掉约 90%”是实测目标，不是虚构通过项 |
| 文档维护 | 任务完成及时更新本表；阶段结果集中写入必要交付报告，不为小步骤增设附件体系 |

## 6. 阶段验收记录表

此表记录阶段结束时的结果位置，不另建重复实验索引。详细配置和真实输出继续在 run 中保存。

| 阶段 | 软件检查结果 | 真实检查结果 | 交付报告 | 状态 |
|---|---|---|---|---|
| P0 | 配方/split/三次移动底盘配置测试通过；集成回归见 P1 | val0 CPU 准备链与配置路径已实测，未跑新 GPU 标定 | [构建交付](../delivery/01-build-delivery.md) | 已完成 |
| P1 | 最终集成 299 项：297 通过、2 跳过 | 离线索引/6 代表重载；公开构建已使用 Selector，旧在线检索退出 | [构建交付](../delivery/01-build-delivery.md) | 已完成 |
| P2 | 最终集成 299 项：297 通过、2 跳过 | CPU/GPU 事务/相机；真实 MoveTarget 接受并重载；双 house spawn 父进程单表 | [构建交付](../delivery/01-build-delivery.md) | 已完成 |
| P3 | 八类几何代理与预算/缓存/调度测试通过 | 五类构造接受；Visibility/Arm-path/Approach 为负结果；无最终标签 | [构建交付](../delivery/01-build-delivery.md) | 已完成 |
| P4 | 八类规则、阶段/lane、三次分母及有界 probe 已验证 | 多组完整标定；六类真实标签正例；Orientation/Visibility 无正例 | [标定交付](../delivery/02-calibration-delivery.md) | 已完成 |
| P5 | 已有改动及验证保留 | 用户要求本轮止于标定，后续采集/导出/benchmark 不再推进 | [既有记录](../delivery/03-downstream-delivery.md) | 后续暂缓 |
| P6 | 标定相关软件/GPU 验证已记录 | 其余后续验收暂缓，已有实验产物保留 | [既有记录](../delivery/03-downstream-delivery.md) | 后续暂缓 |

## 7. 计划表编写阶段与后续实施记录

保留现有需求和方案文档，依据 P0–P6 拆出 36 项任务，补齐文件范围、前置依赖、验收条件和状态；同步方案与报告入口。文档链接、编号/依赖及格式做静态检查。本轮只编写构建计划表，未修改源码/运行配置，未执行测试或真实实验，所有代码构建项保持未开始。

### 实施进展：P0 基础能力

已实现与本体解耦的 1 m / 0.25 m 默认采样、采样不覆盖执行半径、完整房间导航与端点长度、基于名称的任务 ID、同 XY 多 yaw 身份及显式朝向保留。保存旧准备链 CPU 实测，并引用历史标定报告；没有同协议 GPU 新旧对照，不报告加速倍率。P0.1 仅完成采样默认值和 RoboCasa 机器人数据路径修正，新 recipe/schema、split 和诊断配置仍未接入，P0 整体保持进行中。详见阶段交付记录。

### 实施进展：P0 配置补齐与 P1 资产/几何

完成独立 v2 配方和固定来源 split 解析、集中模式阈值/预算、共享三次移动底盘标定配置；新配方尚待 P2 接入公开 build-scenes。离线生成 1695 条实际 THOR 记录（1154 可适配、541 不支持），包含嵌套办公椅；6 个代表资产重载尺寸匹配且缓存复用。公共 occupancy、障碍距离、底盘 clearance 与导航 inflation 已分离，新增 SciPy 显式依赖。P1.2 保持进行中，直到旧 Case 在线检索在 P2 被实际替换。全套回归 293 项：291 通过、2 跳过；未跑新模式 GPU rollout。

### 实施进展：P2 共享构建内核与真实三相机

完成 P2.1/P2.2/P2.3/P2.5/P2.6 模块与验证：任务内一次准备、数据抓取门槛、提案/编辑抽象、复用 runtime 事务和等时长对照、有界 repair、yaw/覆盖与最多 20 合法姿态、no-edit 快照与初始化复用、三路 ROI/无遮挡参考。真实 val0/Panda CPU 事务和三路 GPU 像素已核对；19 像素的停车视角保持 unknown。修复非机器人 qvel 清零与实际分割 ID 混色问题。P2.4 的父进程接入、旧 Case 删除以及八个具体 Builder 仍未完成，不把内核或相机通过当作模式标定完成。

### 实施进展：P1/P2 编排补齐与 P3 八类候选

公开入口改为 house worker 内每 task 一次共享准备与 intent×variant，父进程唯一 scenes.jsonl；删除旧 Case 构造、在线检索与中间发现/资格文件。实现八类候选，天然提案计入 32 总额，资产通过 cheap 后才 resolve，真实根偏移与初始化复用有测试。val0 Potato、Safe Open、默认 Alarm Clock 和 val103 Cup_30 均运行；取得五类构造接受，同 support MoveTarget 真实接受并重载，双 house/双 variant spawn 得到 8 accepted/2 skipped。最终回归 299 项：297 通过、2 跳过。尚无模式标定/最终标签、Visibility/Arm/Approach 正例或新成功示教；P4–P6 保持未开始。

### 实施进展：P4 阶段/lane 证据与三次分母

补齐显式 phase/query/solve/lane 记录、batch 占位目标排除、原 grasp 输入和 planning_ref；实现严格三次完整性、0/3→1/3 与每标签合格 3/3→2/3。独立 diagnostic_ik 返回全 seed 覆盖检查，不执行 TrajOpt/物理步，不修改原分母。软件 317 项：315 通过、2 跳过。真实三 trial smoke 发现全部 GPU 为 Blackwell、cu126 不兼容，已停止并保留 infrastructure_error/unknown；cu128 环境修复仍在下载。P4.1/P4.3 待真实验证和每 S0 probe 编排；P4.4–P4.6 未开始，不把局部模块当作最终诊断完成。

### 实施进展：P4 八类规则与诊断主记录

完成八类统一分类模块和每标签 S1 tier 选择；接入实际准备状态测量、原 trial/lane 引用、每 S0 至多一次独立 IK、父进程 active 超时终止与 diagnosis.jsonl。335 项：333 通过、2 跳过。已用真实失败标定输出 incomplete/null/零 probe；真实 S0013 准备状态三相机与水平目标坐标测量通过，基线保持不变。CUDA wheel 仍在下载，尚无完整真实标定或最终标签；P4.1/P4.3/P4.5 继续验证，P4.6/P5/P6 未开始。

### 实施进展：P5 train/val 与统一诊断消费

train 按每标签合格 tier 合并 S0/S1，复用标定成功轨迹；数值成功轨迹按实测状态生成训练观测，不新增物理步。val 每 S0 一个 episode，私有恢复区域只使用该起点合格 S1；多标签组重叠，总体分母去重，unknown/记录错误独立。局部 33 项通过，全套 350 项：348 通过、2 跳过。真实不完整标定经公开 build-bench 得到 0 episode、零新标定 rollout。P5 软件已接通，正式正例依赖 P4.6；cu128 重试安装仍在下载，未取得新成功示教或策略评测。

### 实施进展：P6 当前环境的观测与控制

修正 Panda π0.5 权重/统计的数据根并启动独立推理环境安装。从 Cup_30/S0013 的真实准备状态取得 16D 状态与三路 RGB，渲染状态不变、零新增物理步。当前资产的真实保持/10 mm 步进与底盘移动 TCP 跟踪已执行：TCP 跟踪误差约 0.68 mm / 0.00096 rad。依赖仍在下载，尚无真权重推理、完整标定或困难 S0 闭环；见 P6 真实验证进展。

### 实施进展：cu128、真权重与 Approach 补充构建

cu128 和独立 π0.5 环境安装完成；Torch/JAX 真实 GPU 矩阵 kernel 通过，π0.5 使用真实 Cup_30 三路输入推理得到有限 50×12 动作块（未执行）。补充来源 1–9 的三类候选构建共 1 接受/65 拒绝，新增 house 3 抽屉 Open 的天然 Approach 候选，17 合法站位；Visibility/Arm-path 仍无接受正例。cuRobo 三 trial r02 已启动，待实际结果；最终标签和主链闭环仍未验证。

### 实施进展：真实阶段、IK 与首条成功视觉回放

cuRobo 三 trial r02 实际执行并记录任务失败，batch lane/seed 与三阶段证据已核对，无基础设施错误。独立真实 IK 16×32 完整覆盖，167/512 可行，4.64 s，plan_count=0，无 TrajOpt/物理步。Open 17×3 完整标定全部 goalset IK 无解，诊断 no_success。Cup_30 的完整标定运行中出现 r000010/S0029 真实 mobile_pick 成功，已完成三相机各 328 帧实测状态回放，val 不入训练索引。另启动 train Potato 5×3 完整标定。P4.1/P4.3/P4.5 的实现与对应真实边界验证完成；P4.6 仍待高率 S1 与阈值定标，完整标签与闭环未完成。

### 实施进展：完整负结果与新环境回归

Cup 60 条完整标定得到 2 success/37 failure/21 planning_no_solution，14186 物理步；两成功站位各 1/3，资格 no_high_rate_s1。Open 51 条及 Potato 15 条全规划无解，资格 no_success。真实 train 复用得到零 pair/示教，完整 val 标定构建零 episode。修复 mode summary 的普通阈值漏计 1/3 点，重建 r02 正确报告 20 低率点；全套 351 项：349 通过、2 跳过。Alarm Clock 两 train 候选的完整 24 条标定也已完成，全部 planning_no_solution；继续寻找合格 S1，不放宽成功率门槛。

### 实施进展：闭爪底盘回退缺陷修复

扩展瓶类取得 Arm-path 实际 Vase 编辑正例及 Narrow Footstool repair 正例；π0.5 完成 60 步真实闭环但未成功。通过末帧几何与实际轨迹定位 RoboCasa follow_points 未更新 manip_base_target，闭爪回到初始站位；最小修复已完成。相同 S0013 三 seed 从 0/3 变为 2/3 mobile_pick，闭爪底盘不再回退。局部 14 项通过，全套 352 项：350 通过、2 跳过。修复后完整 Cup 与显式高把手 Open 的 r02 已完成，见 [标定交付](../delivery/02-calibration-delivery.md)；旧负结果不当作修复后任务固有困难。见 [修复记录](../delivery/04-mobile-base-hold-fix.md)。

### 最新范围与交付

用户于 2026-10-26 将本轮范围收敛为“流程到标定完成即可，后续任务先不用做”。本轮交付 P0–P4（含 S0/S1 与诊断结果）；P5/P6 后续任务暂不推进，已有实现和实验产物保留，不回滚。当前结果见[标定交付](../delivery/02-calibration-delivery.md)。

标定交付收尾：全套回归 354 项，352 通过、2 个显式 OpenGL 检查跳过；日志 outputs/logs/task-modes-calibration-delivery-regression-r01.log。后续工作已按用户要求停止推进。
