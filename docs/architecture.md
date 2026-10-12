# 完整数据采集管线架构

## 1. 设计范围与当前边界

本目录结构面向方案中的**完整五阶段**。截至当前：

- **阶段一**底座（场景/机器人/记录）：已实现并有回归。
- **阶段二**规则式场景构建：已实现（case1 已验证），并新增**程序化障碍物增强** `augment-obstacles`。
- **阶段三**固定底盘站位图 + 原生 cuRobo 采集：已实现；并扩展出**原始 val 无编辑采集** `collect-no-edit`（cuRobo 联合规划底盘 x/y/yaw + 单臂 10D）。
- **阶段四**连续导航 + VLA 操作：**已实现**原始场景连续导航/操作（A* + cuRobo）、VLA 站位评测与 S0→S1 移动操作实验；失败接管示教（`demonstrations/`）仍为占位。
- **阶段五**批量调度与发布：`exporting/` 已提供采集索引、热图、导航栅格与 rollout 导出；`scheduling/` 仍为占位。

三个长期约束：

1. 领域模块负责算法，`workflows` 负责串联，`runtime` 是唯一真实运动入口。
2. 场景构建、case 成立、机器人完成任务分别给结论，不合并成一个 `success`。
3. 原始资产和旧工程只读。新工程不导入、不调用、不 source `lastmile_pipeline`；不复用人工门禁。

## 2. 仓库布局

```text
LastMileDataFlow/
├── README.md                         使用入口与当前能力
├── pyproject.toml                    独立安装、依赖与 CLI（含可选 extras）
├── bin/lastmile-dataflow              源码运行入口，只设 PYTHONPATH 后调 cli.py，不绑定 GPU
├── configs/
│   ├── agent_api.json                Agent API 提供方与模型（不含凭据）
│   ├── curobo-v080-pin.json          cuRobo v0.8.0 标签与源码哈希固定
│   ├── robots/                       模型、动作语义、相机、控制频率和适配器
│   ├── tasks/                        case、目标、编辑权限与验收要求
│   ├── builds/                       阶段二构建配置
│   ├── stations/                     v3 采样/规划/执行与预算
│   ├── case_edits/                   单场景编辑、程序化增强、最终站位接入配置
│   ├── collection/                   种子、执行/Agent 预算、输出与协议
│   ├── no_edit/                      无编辑采集配置（curobo_v080 / molmobot / val）
│   └── policies/                     策略模型配置（molmobot）
├── src/lastmile_dataflow/
│   ├── config.py / io.py / cli.py     共用配置、序列化和薄命令入口
│   ├── scenes/                       来源加载、实例映射、版本与初态候选
│   ├── robots/                       机器人协议、执行器、限位与初始化
│   ├── runtime/                      仿真生命周期、快照、动作执行与 attempt 编排
│   ├── recording/                    轨迹、相机、视频、事件、结果和证据摘要
│   ├── validation/                   物理安全、协议与分开验收结论
│   ├── integrations/                 外部格式边界：旧样例、动作转换、MolmoBot 导出
│   ├── catalog/                      [阶段 2] 场景与资产索引、检索
│   ├── construction/                 [阶段 2] 编辑事务、落位闭环、case 模板、程序化增强
│   ├── agents/                       [阶段 2/4] 观察包、决策、复查、工作记录
│   ├── tasks/                        [阶段 3] 从导出模型发现任务，不看历史成功标签
│   ├── stations/                     [阶段 3] 站位采样、试验、站位图、增强站位接入
│   ├── planning/                     [阶段 3/4] cuRobo、碰撞世界和持物规划
│   ├── navigation/                   [阶段 4] A* 路径搜索、连续导航
│   ├── policies/                     [阶段 4] MolmoBot VLA 客户端与推理子进程
│   ├── demonstrations/               [阶段 4] 失败检查点、接管与分支示教（占位）
│   ├── scheduling/                   [阶段 5] 任务队列、预算、断点续跑和成本（占位）
│   ├── exporting/                    [阶段 3/5] 热图、导航栅格、采集索引、导出与派生视频
│   └── workflows/                    [阶段 2–5] 全链路编排、反馈与退出
├── scripts/                          诊断/审计/重建/重验/重渲等独立工具（见第 9 节）
├── tests/                            单元/故障回归与显式真实仿真验证
│   └── fixtures/                     小型只读样例；不放模型和视频
├── docs/                             架构、格式、运行手册与各阶段说明
├── reports/                          交付总结和验证日志，不作为运行输入
├── outputs/                          [忽略版本控制] 采集产物
└── 数据采集管线方案与计划.md            用户原始方案，保持不变
```

`demonstrations/`、`scheduling/` 目前只放职责说明；README 的“规划”不等于功能已实现。
避免按 case 复制仿真、机器人、视频和验收代码：case1/1.5/2/3 差异走
`construction/templates/`、`case_edits/*.json` 与 `validation/` 的配置选择。

实际采集通常在两个 git worktree 中运行：`.worktrees/no-edit-lastmile-val`（feat/no-edit-lastmile-val，原始 val 基线）
与 `.worktrees/procedural-obstacle-augmentation`（feat/procedural-obstacle-augmentation，增强场景与最终站位）。

## 3. 五阶段的完整数据流

```text
CollectionJob + RobotConfig + TaskConfig + CollectionConfig
  │
  ├─ catalog：静态检索 → SceneCandidate / AssetCandidate
  │
  ├─ scenes + runtime：加载源场景 → 实例映射 / 几何事实 / 观察
  │
  ├─ construction ↔ agents：受限编辑候选 → 决策 → 编辑事务
  │                    ↓
  │            runtime 静置 + validation 落位/穿模检查
  │                    ↓
  │            agents 复查 → 修复 / 回滚 / 预算退出
  │                    ↓
  │              冻结 SceneVersion + Checkpoint
  │
  ├─ stations → planning → runtime：站位 / 规划 / 真实执行
  │                    ↓
  │            recording：成功与失败 attempt 都保存
  │                    ↓
  │            validation：case 实证确认 / 反馈 construction
  │
  ├─ policies + navigation + planning：VLA / 专家连续执行（阶段四，已实现）
  │                    ↓
  │            demonstrations：失败现场 → 继续接管或新分支（占位）
  │
  └─ scheduling + exporting：限定预算队列 → 质量检查 → 数据集发布

recording 贯穿全链路；Agent/编辑/规划诊断进入事件和独立证据，不能覆盖原结果。
```

当前阶段一走最短基础路径：

`SceneSource → Simulation → RBY1Adapter → LightweightCheck → AttemptRecorder`。

阶段三/四的 no-edit 采集走更长路径：

```text
tasks/raw_scene → workflows/no_edit
  ├─ stations/no_edit_sampling → runtime/visible_initialization
  ├─ planning(cuRobo 10D) / policies/molmobot(VLA)
  ├─ runtime/no_edit_execution（cuRobo 连续段）
  │  runtime/molmobot_execution（VLA 站位与 S0→S1）
  └─ recording + validation/no_edit|molmobot + exporting/no_edit_heatmap
```

`runtime/runner.py` 只做有界短动作，不发展成涵盖所有阶段的巨型编排器；后续 `workflows` 复用该生命周期与记录组件。

## 4. 功能边界与扩展点

| 模块 | 拥有的职责 | 不应承担 |
|---|---|---|
| `scenes` | 源房屋、MJCF/实例/资产映射、版本和初始化候选 | 抓取成功判定、Agent 调用 |
| `catalog` | 10k 静态索引、资产池、支撑区域索引与检索排序 | 逐个加载全部房屋仿真 |
| `construction` | 预期支撑关系、编辑候选、少量修改、静置、回滚、case 模板、程序化障碍物增强 | 直接决定真实机器人任务成功 |
| `agents` | 标注观察包、结构化建议、复查和修复；模型/提示/预算记录 | 猜精确坐标、改物理阈值、覆盖严重失败 |
| `tasks` | 从实际导出模型发现 pick/open 任务与目标 | 依赖历史成功标签或人工批准 |
| `robots` | 每种机器人的集中动作语义、限位、控制下发、规划关节映射、原生 MolmoBot 桥 | 在各 case 内复制机器人知识；把 motor 当米 |
| `runtime` | 有权限的初始化、实际 stepping、快照与连续性约束；多后端执行入口 | 中途重置以制造成功 |
| `recording` | 原始动作、实际控制、现场、事件和不覆盖的 attempt；canonical 侧车与计时 | 实施 case 判定或隐藏失败 |
| `validation` | 数值/物理安全、轻量协议、任务专用结果检查（含 MolmoBot、增强场景） | 用几何通过替代任务语义通过 |
| `stations` | 分层站位预算、试验关系、成功/失败/未知分层、增强站位接入与覆盖压缩 | 将规划无解等同真实执行失败 |
| `planning` | cuRobo 请求、世界碰撞、轨迹结果、规划诊断 | 以非空轨迹冒充规划成功 |
| `navigation` | A* 路径搜索、运动控制、实测到站 | 传送或恢复预设标准到站状态 |
| `policies` | VLA 观测权限、策略动作流、模型子进程生命周期 | 给 VLA 注入未声明目标真值/专家路径 |
| `demonstrations` | 原失败到接管的关联；继续恢复与新分支的区别（占位） | 重写原始 cuRobo/VLA 失败结果 |
| `scheduling` | 配置一致性、队列、成本、独立新 attempt（占位） | 把断点续跑解释为覆盖旧轨迹 |
| `exporting` | 完整性、版本/视频/轨迹对应、热图/栅格、发布索引、派生视频 | 修补证据来凑齐成功数据 |
| `integrations` | 外部数据/规划动作/模型导出格式转换，兼容层 | 包装调用旧仓库运行代码 |
| `workflows` | 跨模块状态机、局部反馈、预算和退出 | 底层算法实现 |

## 5. 依赖方向与执行权限

- 低层：`io / config / robots / scenes / validation`。
- 执行基础：`runtime → robots / scenes`，`recording → io`。
- 编排：基础 runner 组合 runtime、validation 与 recording；workflows 组合领域能力。
- 领域层：catalog、construction、planning、navigation、policies 等不得彼此循环导入。
- 外部 Agent/VLA 只能返回结构化决策/动作，由程序验证后执行。
- 导出和调度消费引用/manifest，不直接修改场景或动作。

机器人位置只允许在初始化阶段写入。`Simulation.begin()` 后，即使 attempt 已终止，也不能
恢复快照或再次开始；独立试验必须创建新 Simulation 和新 attempt。编辑 API 只对构建会话开放，
不能成为连续执行的绕过入口。`runtime/episode_guard.py` 保证 episode 级隔离，共享资源失败为 fail-stop。

## 6. 跨阶段数据契约

已实现格式见 [data-format.md](datas/data-format.md)。后续领域记录统一携带：

- `schema_version`、源场景/版本 ID、任务 ID、机器人配置摘要。
- `attempt_id` 或关联的 `checkpoint_id`，策略来源和冻结预算。
- 输入证据引用、动作/操作、输出证据引用、独立结果和终止原因。

记录边界与实现状态如下：

| 记录 | 关键内容 | 负责模块 | 状态 |
|---|---|---|---|
| `no-edit` run（`frozen_config.json` / `scene_index.json` / `summary.json` / `timing.json` / `dataset_index.jsonl`） | 站位试验、热图、连续 rollout、四路视频、S0/S1 | stations + workflows + validation | 已实现 |
| `augmented_variant` 接入（`station_input.json` / `stations.json` / `augmented_input/`） | 冻结增强快照与最终站位集的绑定、hash 与覆盖算法 | stations/augmented_input | 已实现 |
| 程序化增强 run（`variants/vNNN/edits.json` / `checks.json` / `stations_final.json` / `rgb/`） | 新增物体、物理与构造检查、覆盖压缩点集 | construction + workflows/obstacle_augmentation | 已实现 |
| MolmoBot 导出（state22 / native action） | 只读派生，含显式标定映射 | integrations/molmobot_dataset | 已实现（仅 MolmoBot Adapter） |
| `SceneCandidate` / `EditTransaction` / `AgentWorkRecord` | 检索、编辑事务、观察引用 | catalog / construction / agents | 已实现（阶段二） |
| `StationResult` | 底盘 XY/yaw、臂/躯干/grasp、初始化、规划、是否执行 | stations | 已实现（阶段三） |
| `DemonstrationLink` / `DatasetManifest` | 接管来源、源房屋分组、版本与覆盖 | demonstrations / exporting | 部分实现 / 占位 |

规划结果采用明确状态：`success / no_solution / infrastructure_error / not_tested`。
没有实际执行的规划失败保留空轨迹和诊断，不生成“执行失败视频”。
困难标签必须声明当前策略与预算，不能写成物理不可解。

## 7. 存储演进

每个 attempt 自包含 `scene/`，方便独立恢复与审计，代价是模型重复占空间。
实际布局按以下方式演进（`outputs/` 已被 Git 忽略）：

```text
outputs/
  indexes/                              静态场景/资产索引及缓存版本
  scene_versions/<version-id>/           冻结模型、资产映射和完整快照
  builds/<build-id>/                     编辑事务、Agent 观察/日志、物理检查
  station_maps/<map-id>/                 点表、规划诊断、attempt 引用和可视化
  attempts/<attempt-id>/                 所有真实执行/未执行退出的原始证据
  no_edit/<run-id>/                      无编辑采集：站位、热图、rollout、视频、数据集索引
  diagnostics/<run-id>/                  诊断/验收 run（增强、重验、smoke、pilot）
  dependencies/                          WARP/MPL 缓存与模型缓存证据
  logs/<run-id>*.log                     全部运行日志的唯一入口
  demonstrations/<demo-id>/              原失败到接管的关联，而非复制篡改轨迹
  jobs/<job-id>/                         调度状态、预算与运行成本
  exports/<release-id>/                  通过质量检查的清单和发布索引
```

SceneVersion 内容寻址、attempt 唯一且不覆盖；拆出共享模型后仍保留完整摘要与可独立导出的依赖闭包。
源模型、资产、历史数据不写入本工程；资产只读外部路径由配置显式指定。

## 8. 实施顺序

1. **阶段一**：基础动作、快照、现场与视频；旧样例显式适配回归。已完成。
2. **阶段二**：catalog → construction 编辑/静置验证 → agents 观察/复查 → 四类模板 → 程序化障碍物增强。已完成。
3. **阶段三**：planning cuRobo 原生后端 → stations 分层采集 → no-edit 联合底盘规划采集 → case 实证验证与浏览报告。已完成。
4. **阶段四**：navigation/policies 连续专家与 VLA → 连续导航+操作、VLA 站位评测与 S0→S1。已实现；demonstrations 接管待补。
5. **阶段五**：scheduling 批量运行与恢复 → exporting 发布质量、成本和多样性统计。部分实现。

每阶段新增能力继续遵守原始证据不可覆盖、自动预算退出和三个独立结论，不引入逐例人工审批。

## 9. 独立工具脚本

`scripts/` 放不进主流程的单次诊断/审计/重建工具，均为只读或显式新 run：

| 脚本 | 用途 |
|---|---|
| `setup_curobo_v080.sh` | 安装 cuRobo v0.8.0 独立后端（固定标签 + 源码哈希校验） |
| `audit_procedural_obstacles.py` | 只读复核增强 run：重做物理/case 检查、覆盖压缩、链接核对 |
| `rerender_obstacle_cases.py` | 仅当相机覆盖模块变化时重渲媒体；快照与站位字节一致 |
| `rebuild_augmented_native.py` | 把已验收布局在 native 基线上回放并重新冻结（MolmoBot 前置） |
| `revalidate_native_stations.py` | 对 native 快照重验全部原始稠密候选点并覆盖压缩到 ≤20 |
| `audit_no_edit_station_input.py` | 只读检查增强站位接入：源 hash、点集逐项相等、计划次数与 trial ID |
| `audit_head_fov_replay.py` | 从冻结轨迹复算 head FOV 硬校验 |
| `replot_no_edit_maps.py` | 由已有实测数据重绘热图，不新增试验 |
| `report_obstacle_cases.py` / `verify_reach_sampling.py` | 增强案例报告 / 臂展采样核对 |
| `tidy_outputs.sh` | 把散落日志归集到 `outputs/logs/` |

## 阶段二已落地接口

`construction/config.py` 定义独立 v2 契约，`catalog/index.py`/`assets.py` 负责检索与资产资格，
`scenes/geometry.py` 提取实际水平碰撞区域，`construction/candidates.py`/`templates.py` 提供规则候选与四类要求。
`runtime/build_session.py` 是构建期唯一仿真编辑/静置入口；不放松 `Simulation` 连续执行期间的禁止恢复规则。
`validation/placement.py` 检查支撑、稳定性、严重穿透与副作用；`agents/protocol.py` 提供严格观察/决策协议。
`workflows/build.py` 管预算、冻结、独立恢复和阶段一交接，反馈以追加文件记录。
规则决策不记为模型复查。

## 阶段三/四已落地接口

- **无编辑采集**：`stations/no_edit_config.py`（冻结配置）、`stations/no_edit_sampling.py`（均匀盘采样 + begin 前可见性初始化）、
  `runtime/no_edit_execution.py` / `runtime/no_edit_v2.py`（cuRobo 10D 联合规划 + 真实 20D 执行）、
  `validation/no_edit.py`、`exporting/no_edit_heatmap.py`、`workflows/no_edit.py`（隔离 GPU worker、增量热图、可续跑）。
- **增强场景**：`construction/obstacle_config.py` / `obstacle_sampling.py` / `obstacle_regions.py`、
  `runtime/obstacle_edit_session.py`（纯程序化事务，无 Agent）、`workflows/obstacle_augmentation.py`、
  `validation/obstacle_augmentation.py`；接入见 `stations/augmented_input.py` 与 `workflows/native_station_input.py`。
- **MolmoBot VLA**：`policies/molmobot*.py`、`runtime/molmobot_execution.py`、`validation/molmobot.py`、
  `robots/molmobot.py`（原生 motor 20D 桥）；独立移动操作实验 `scripts/run_molmobot_station_transfer.py`。
- **连续导航段**：由 `navigation/astar.py` + `runtime/no_edit_execution.py` 完成；
  命令限位与躯干联动见 `robots/action_limits.py`、`robots/torso_command.py`。
- **raw-first canonical**：`recording/canonical.py` 现场侧车 + `validation/canonical.py` + `exporting/canonical_index.py`，
  只实现 MolmoBot Adapter；旧 20D 成功筛选索引保留。

运行入口与参数见 [运行手册](运行手册.md)、[增强场景构造运行指南](增强场景构造运行指南.md)。
