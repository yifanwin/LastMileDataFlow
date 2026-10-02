# 完整数据采集管线架构

## 1. 设计范围与当前边界

本目录结构面向方案中的**完整五阶段**，不是仅为第一阶段搭一个临时脚本目录。
当前实现第一阶段；第二至五阶段预留清晰的领域目录、数据契约和依赖方向，**不提供返回假成功的占位实现**。

三个长期约束：

1. 领域模块负责算法，`workflows` 负责串联，`runtime` 是唯一真实运动入口。
2. 场景构建、case 成立、机器人完成任务分别给结论，不合并成一个 `success`。
3. 原始资产和旧工程只读。新工程不导入、不调用、不 source `lastmile_pipeline`；不复用人工门禁。

## 2. 仓库布局

```text
LastMileDataFlow/
├── README.md                         使用入口与当前能力
├── pyproject.toml                    独立安装、依赖与 CLI
├── bin/lastmile-dataflow              源码运行入口，不绑定相邻环境或 GPU
├── configs/
│   ├── robots/                       模型、动作语义、相机、控制频率和适配器
│   ├── tasks/                        case、目标、编辑权限与验收要求
│   └── collection/                   种子、执行/Agent 预算、输出与协议
├── src/lastmile_dataflow/
│   ├── config.py / io.py / cli.py     共用配置、序列化和薄命令入口
│   ├── scenes/                       来源加载、实例映射、版本与初态候选
│   ├── robots/                       机器人协议、执行器、限位与初始化
│   ├── runtime/                      仿真生命周期、快照、动作执行与 attempt 编排
│   ├── recording/                    轨迹、相机、视频、事件、结果和证据摘要
│   ├── validation/                   物理安全、协议与分开验收结论
│   ├── integrations/                 外部格式边界：旧样例、规划/导航动作转换
│   ├── catalog/                      [阶段 2] 场景与资产索引、检索
│   ├── construction/                 [阶段 2] 编辑事务、落位闭环、case 构建模板
│   ├── agents/                       [阶段 2/4] 观察包、决策、复查、工作记录
│   ├── stations/                     [阶段 3] 站位采样、试验、站位图
│   ├── planning/                     [阶段 3/4] cuRobo、碰撞世界和持物规划
│   ├── navigation/                   [阶段 4] 路径搜索、连续导航和实测到站
│   ├── policies/                     [阶段 4] 专家/VLA 策略与观测权限
│   ├── demonstrations/               [阶段 4] 失败检查点、接管与分支示教
│   ├── scheduling/                   [阶段 5] 任务队列、预算、断点续跑和成本
│   ├── exporting/                    [阶段 5] 质量检查、分组清单与发布
│   └── workflows/                    [阶段 2–5] 全链路编排、反馈与退出
├── tests/                            单元/故障回归与显式真实仿真验证
│   └── fixtures/                     小型只读样例；不放模型和视频
├── docs/
│   ├── architecture.md               本文件：全管线职责与扩展位置
│   ├── data-format.md                v1 已实现格式及未来格式边界
│   └── phase1.md                     第一阶段验收、运行与限制
├── reports/                          交付总结和验证日志，不作为运行输入
├── outputs/                          [忽略版本控制] 新采集产物
└── 数据采集管线方案与计划.md            用户原始方案，保持不变
```

未来能力目录目前只放职责说明；README 的“规划”不等于功能已实现。
避免按 case 复制仿真、机器人、视频和验收代码。case1/1.5/2/3 的差异将放到
`construction/templates/` 与 `validation/cases/`，以配置选择。

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
  ├─ policies + navigation + planning：专家 / VLA 连续执行
  │                    ↓
  │            demonstrations：失败现场 → 继续接管或新分支
  │
  └─ scheduling + exporting：限定预算队列 → 质量检查 → 数据集发布

recording 贯穿全链路；Agent/编辑/规划诊断进入事件和独立证据，不能覆盖原结果。
```

当前第一阶段走最短的基础路径：

`SceneSource → Simulation → RBY1Adapter → LightweightCheck → AttemptRecorder`。
`runtime/runner.py` 只做有界短动作，不把它发展成涵盖所有阶段的巨型编排器。
后续 `workflows` 复用该生命周期与记录组件，而不是重写仿真执行。

## 4. 功能边界与扩展点

| 模块 | 拥有的职责 | 不应承担 |
|---|---|---|
| `scenes` | 源房屋、MJCF/实例/资产映射、版本和初始化候选 | 抓取成功判定、Agent 调用 |
| `catalog` | 10k 静态索引、资产池、支撑区域索引与检索排序 | 逐个加载全部房屋仿真 |
| `construction` | 预期支撑关系、编辑候选、少量修改、静置、回滚、case 模板 | 直接决定真实机器人任务成功 |
| `agents` | 标注观察包、结构化建议、复查和修复；模型/提示/预算记录 | 猜精确坐标、改物理阈值、覆盖严重失败 |
| `robots` | 每种机器人的集中动作语义、限位、控制下发、规划关节映射 | 在各 case 内复制机器人知识 |
| `runtime` | 有权限的初始化、实际 stepping、快照与连续性约束 | 中途重置以制造成功 |
| `recording` | 原始动作、实际控制、现场、事件和不覆盖的 attempt | 实施 case 判定或隐藏失败 |
| `validation` | 数值/物理安全、轻量协议、任务专用结果检查 | 用几何通过替代任务语义通过 |
| `stations` | 分层站位预算、试验关系、成功/失败/未知分层、可视化 | 将规划无解等同真实执行失败 |
| `planning` | cuRobo 请求、世界碰撞、轨迹结果、规划诊断 | 以非空轨迹冒充规划成功 |
| `navigation` | 路径搜索、运动控制、实测到站 | 传送或恢复预设标准到站状态 |
| `policies` | 专家/VLA 观测权限、策略动作流 | 给 VLA 注入未声明目标真值/专家路径 |
| `demonstrations` | 原失败到接管的关联；继续恢复与新分支的区别 | 重写原始 cuRobo/VLA 失败结果 |
| `scheduling` | 配置一致性、队列、成本、独立新 attempt | 把断点续跑解释为覆盖旧轨迹 |
| `exporting` | 完整性、版本/视频/轨迹对应、源房屋隔离、发布索引 | 修补证据来凑齐成功数据 |
| `integrations` | 外部数据/规划动作格式转换，兼容层 | 包装调用旧仓库运行代码 |
| `workflows` | 跨模块状态机、局部反馈、预算和退出 | 底层算法实现 |

## 5. 依赖方向与执行权限

- 低层：`io / config / robots / scenes / validation`。
- 执行基础：`runtime → robots / scenes`，`recording → io`。
- 编排：基础 runner 组合 runtime、validation 与 recording；后续 workflows 组合领域能力。
- 领域层：catalog、construction、planning、navigation、policies 等不得彼此循环导入。
- 外部 Agent/VLA 只能返回结构化决策/动作，由程序验证后执行。
- 导出和调度消费引用/manifest，不直接修改场景或动作。

机器人位置只允许在初始化阶段写入。`Simulation.begin()` 后，即使 attempt 已终止，也不能
恢复快照或再次开始；独立试验必须创建新 Simulation 和新 attempt。编辑 API 将只对构建会话开放，
不能成为连续执行的绕过入口。

## 6. 跨阶段数据契约

已实现格式见 [data-format.md](data-format.md)。后续领域记录统一携带：

- `schema_version`、源场景/版本 ID、任务 ID、机器人配置摘要。
- `attempt_id` 或关联的 `checkpoint_id`，策略来源和冻结预算。
- 输入证据引用、动作/操作、输出证据引用、独立结果和终止原因。

建议新增格式的边界如下，**当前尚未实现**：

| 记录 | 关键内容 | 负责模块 |
|---|---|---|
| `SceneCandidate` | 源房屋、匹配事实、资产候选、检索排名 | catalog |
| `EditTransaction` | 修改前/后版本、预期支撑、候选 ID、落位/邻近资产变化、回滚来源 | construction |
| `AgentWorkRecord` | 观察引用、模型/提示版本、公开决策理由、工具结果、采用/拒绝、预算 | agents |
| `StationResult` | 底盘 XY/yaw、臂/躯干/grasp、初始化、规划、是否执行、attempt 引用 | stations |
| `DemonstrationLink` | 原失败 attempt、检查点、继续/分支方式、接管来源、新轨迹引用 | demonstrations |
| `DatasetManifest` | 源房屋分组、版本、已验证可解/失败恢复/站位/未解难例、成本与覆盖 | exporting |

规划结果采用明确状态：`success / no_solution / infrastructure_error / not_tested`。
没有实际执行的规划失败保留空轨迹和诊断，不生成“执行失败视频”。
困难标签必须声明当前策略与预算，不能写成物理不可解。

## 7. 存储演进

v1 使用每个 attempt 自包含的 `scene/`，方便独立恢复与审计，代价是模型会重复占空间。
第二至五阶段按以下布局演进，v1 读取器不直接假定这些尚未创建的索引存在：

```text
outputs/
  indexes/                              静态场景/资产索引及缓存版本
  scene_versions/<version-id>/           冻结模型、资产映射和完整快照
  builds/<build-id>/                     编辑事务、Agent 观察/日志、物理检查
  station_maps/<map-id>/                 点表、规划诊断、attempt 引用和可视化
  attempts/<attempt-id>/                 所有真实执行/未执行退出的原始证据
  demonstrations/<demo-id>/              原失败到接管的关联，而非复制篡改轨迹
  jobs/<job-id>/                         调度状态、预算与运行成本
  exports/<release-id>/                  通过质量检查的清单和发布索引
```

SceneVersion 内容寻址、attempt 唯一且不覆盖；拆出共享模型后仍保留完整摘要与可独立导出的依赖闭包。
源模型、资产、历史数据不写入本工程；资产只读外部路径由配置显式指定。

## 8. 实施顺序

1. **当前阶段**：基础动作、快照、现场与视频；旧样例显式适配回归。
2. **下一阶段**：catalog → construction 编辑/静置验证 → agents 观察/复查 → 首批四类模板。
3. **第三阶段**：planning cuRobo 原生后端 → stations 分层采集 → case 实证验证与浏览报告。
4. **第四阶段**：navigation/policies 连续专家与 VLA → demonstrations 接管，再扩展持物/柜体/门任务。
5. **第五阶段**：scheduling 批量运行与恢复 → exporting 发布质量、成本和多样性统计。

每阶段新增能力继续遵守原始证据不可覆盖、自动预算退出和三个独立结论，不引入逐例人工审批。
