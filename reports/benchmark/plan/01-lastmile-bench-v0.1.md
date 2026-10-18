# lastmile-bench v0.1 实施方案

状态：active
日期：2026-10-18
替代：无（首版实施方案）
关联：[需求](../requirement/01-lastmile-bench-v0.1.md)、[标定口径修订](../delivery/03-calibration-scope-correction.md)、[现状](../info/01-current-status.md)、[方案整理记录](../delivery/02-implementation-plan.md)

实施状态（2026-10-18）：P0/P1 已完成，P2 已在两个 RBY-1 episode 上实际执行并得到 0/2 成功；Panda 构建及导航区域进入已验证。没有 Open 来源标定，Panda VLA 与自主恢复成功正例未验收。实际交付见[实施与构建记录](../delivery/phase_01/2026-10-18-build.md)。

## 1. 实施目标与默认范围

在 LastMileDataFlow 现有五个命令内完成：**直接复用已有 scene-datasets + 标定结果（允许底盘移动） → 构建可达恢复区域 → 导出困难 S0 → Full policy 自主闭环 → 汇总任务成功、恢复与底盘行为**。

**按用户 2026-10-17 澄清修订：不新增固定底盘标定，不要求重新生成场景或补做整图标定。成功率指参考执行器从各站位初始化，在原标定协议下完成任务的实测比例。**

构建端沿用输入数据中的机器人和场景，先在已有标定上打通一个 Pick 对象，再纳入已有 Open 标定并扩展 Case。待测策略接口与构建分开：现有 MolmoBot 执行器面向 RBY-1，PandaOmron 的 VLA 接口尚未接入；该差异作为对应策略评测的适配事项，不阻止利用 Panda 等已有标定先构建 bench。

本方案只规定后续实现，不在本次运行实验。代码继续放现有领域模块，不建立新仓库、第二套执行器或新的 CLI 命令。正式 benchmark 不使用未标定 smoke。

核心约定：

- `low=0.20`、`high=0.60`，均含等号；每个合法困难 S0 最多形成一个 episode，不按 S0×S1 展开。
- cuRobo 是构建参考执行器，可按原协议联合移动底盘与机械臂；待测策略独立闭环执行，不要求与标定使用相同后端。
- S1、区域、路径和难度只在构建/评分侧使用；Full policy 不接收恢复目标，也不调用 A* 代替自主调整。
- 首先实现正确的筛选、记录和统计，再扩量。构建无样本或策略全失败都如实报告。

## 2. 现有能力与实际改动

| 环节 | 可复用内容 | 本轮改动 |
|---|---|---|
| 场景/站位 | `scenes/dataset.py`、`scenes/stations/sampling.py` | 保留场景、对象状态和站位身份，按评测物理配置重查初始化 |
| 场景配置 | 来源 scene-datasets 的快照与机器人配置 | 构建保留来源物理配置；native 等控制适配仅由实际待测策略需要时处理 |
| 既有标定 | `workflows/collection.py`、`data/evaluation/selection.py` | 读取已有 trial，按初始站位完整计数，接受开放底盘的 cuRobo 结果 |
| 构建匹配 | `runtime/execution_config.py` 与现有 profile | 区分物理配置、标定执行配置、待测策略配置，替换 benchmark 当前的全量 profile 相等判断 |
| 低/高率筛选 | `data/evaluation/benchmark.py::select_starts` | 高率改为 60%，取消固定 1 m 配对前提和默认只取一个起点 |
| 导航 | `planning/astar.py`、整机 footprint、支撑物约束 | 复用 Grid，补充到区域的搜索与可达性；记录包含端点连接段的路径长度 |
| 闭环执行 | `runtime/execution/molmobot.py::run_vla_attempt` | 正式评测始终不传 `path/goal`；补必要结果字段，策略控制适配与构建分离 |
| 真实记录 | `data/recorder.py::AttemptRecorder` | 在现有轨迹中加入显式底盘位姿，避免从特定 state22 下标猜测 |
| 统计 | `data/evaluation/benchmark.py::summarize` | 补恢复/底盘诊断、分组 SR、Macro 与各自覆盖率 |

上述路径均相对于仓库根目录。现有 `collect-lastmile` 的全组合训练示教语义保持独立，不修改其低/高阈值来间接实现 benchmark 规则。

## 3. 直接复用已有标定

### 3.1 复用资格

先检查输入标定的实际条件：

| 已有输入 | 处理方式 |
|---|---|
| 来源 scene-datasets 对应的 cuRobo 标定，允许底盘联合移动 | 直接复用原始 trial 和 rollout 引用，保留底盘活动范围与预算 |
| 来源标定采用固定底盘 | 按其原协议使用并明确标注；不与开放底盘协议的试验混算同一站位率 |
| 标定已有完整计数，但少于新配置默认次数 | 沿用已有标定计划，不强制补到 5 次或其他新设次数 |
| 待测策略使用不同后端或控制接口 | 构建照常复用来源标定；差异与必要控制适配在该策略评测阶段处理 |
| 部分站位不完整或发生基础设施错误 | 这些站位不支撑 S0/H 资格；其他完整站位可继续构建，并报告覆盖范围 |

每个 trial 的 `station_id` 表示初始站位。标定过程中移动底盘仍是该 trial 的正常执行，不因位移超过某个静止容差而失效。成功后的实际底盘位置保留在 rollout 中，不替换初始站位身份，也不从一次终点位置补造一个高率锚点。

### 3.2 成功率语义与构建匹配

采用 `p_hat(q | reference_protocol) = successes / evaluable_trials`：

- 低率 S0：参考执行器从该站位启动，即使允许既定范围的底盘调整，实测成功率仍 ≤20%。
- 高率站位 H：同一参考协议下，从该站位启动的实测成功率 ≥60%；不要求操作阶段底盘静止。
- 高率区域描述该参考协议下的有利启动区域，不表示站在其中无需移动就一定能操作成功。
- 待测策略可通过不同的底盘调整与操作序列完成任务，不强制复现 cuRobo 的局部运动或某个成功终点。

输入只做一次必要绑定：scene/object/task、站位身份与来源快照、标定 run 的参考执行协议和有效计数。底盘活动范围、重试与规划预算从已有 run 读取，不用新的 benchmark 默认配置反向要求旧结果重跑。

现有 `lastmilebench.build` 先调用 `native_scene`，再以 MolmoBot profile 去匹配标定，会阻断 cuRobo 来源结果复用。本轮改为：

1. 在来源场景和参考配置下读取、汇总既有标定；`station_calibration` 的 backend 取实际标定后端，不能由待测策略配置代填。
2. 构建直接引用或冻结来源快照，不调用 native 转换改变构建输入；不新增 scene-datasets 预处理模式。
3. 评测加载所选策略时，按实际接口处理必要的 native/相机/动作适配，保留来源标定协议与评测协议的区别。差异本身不触发自动重标定。
4. 若待测策略不支持来源机器人，清楚报告该策略未接入；已构建 episode 及 oracle 仍有效，不退回重标定流程。

这条路线不要求改动 cuRobo 标定执行器、native 固定底盘控制或夹爪开闭桥。仅当真实策略适配出现具体问题时，再针对该接口修复。

## 4. 构建算法

建议仅增加一个集中区域计算模块 `data/evaluation/recovery.py`；导航搜索仍属于 `planning/astar.py`，编排仍在 `workflows/lastmilebench.py`。

### 4.1 主过程

```text
读取 scene-datasets 和 cuRobo trial
  → 按 scene + object + task 绑定，汇总每站位有效计数
  → S0 集合：合法、完整、成功率 ≤ 0.20
  → H 集合：合法、完整、成功率 ≥ 0.60
  → 按 scene/task 建一次导航栅格和恢复区域近似
  → 对各 S0 搜索可达恢复区域，计算难度
  → 排除非法初始化、无高率证据、无合法路径的 S0
  → 全部保留或显式抽样
  → 写 episodes.jsonl、内部 oracle 和构建摘要
```

分组优先复用任务中的 `instance_id` 和 `task_id`；Open 的关节身份属于任务定义。同类对象、同一房屋的其他目标或不同初始关节状态不能混用高率证据。完整性由站位自己的有效 trial 判断，不要求无关站位全部成功完成后才能使用已有证据。

### 4.2 自由空间与路径合法性

- 复用 `scene_grid`、`task_support_obstacles`、`robot_footprint(whole_body=True)`，使用导航保持姿态的整机保守包络及高度，不能只用底盘半径判整机可通行。
- 导航包络以底盘参考点为中心覆盖允许的 yaw 扫掠；若场景内站位初始保持姿态不同，采用这些姿态的保守包络。地图范围取 scene-datasets 目标局部域，记录边界。
- 网格只保留地面支撑和碰撞约束下的自由空间，八邻域不穿障碍对角。实际坐标到栅格中心的连接段也检查支撑与包络通行；不能仅检查两端。
- 该首版导航模型允许保守假阴性：只有保持姿态、允许原地转向的合法路径才纳入；暂不增加移动时收臂或新导航规划器。摘要明确 `conservative_whole_body_grid`。
- 在构建准备态核对每场景的导航姿态/整机包络；阶段真实验收再选择路线实际导航，验证地图与控制口径一致，不逐 episode 重跑 oracle 导航。

### 4.3 Region approximation 的具体实现

对每个高率站位 h 构造局部 patch：

1. 从 h 沿自由网格扩张，最远自由路径距离不超过 `region_radius_m`。使用现有 `distance_field`，避免圆形邻域穿墙。
2. patch 中每个底盘位姿还需满足 `|wrap(yaw-yaw_h)| ≤ region_yaw_tolerance_rad`。保存 h 的朝向，不把所有 yaw 都算成高率。
3. 与已知非高率标定冲突的邻域采用最近标定站位裁剪：在当前位置和 yaw 容差内，找 XY 最近的完整标定站位；仅当最近站位属于 H 时计入近似区域，并以实际位姿距离处理栅格边界。等距时非高率优先。这样不会让已知低率 S0 因扩张而在初始时刻属于恢复区。
4. 高率锚点及其合法局部连接保留。将所有 patch 并集表示为导航栅格单元及对应允许朝向区间；不另外拟合成功概率模型。

裁剪是保守几何近似，不表示中间点已实测达到 60%。若同一位姿有互相冲突的标定记录，先按统一原始 trial 合并计数，不靠优先选一次成功解决。若栅格分辨率不足以区分邻近低/高率位姿，保留锚点的精确位姿连接；仍不能表达的局部邻域记录为近似不可用，不抹掉低率证据。

区域成员函数统一为 `contains(xy, yaw)`，构建、Recovery Ratio 和 rollout 诊断共用。采样/地图记录中的完整候选域用于分母，不能以 H 自身充当候选域。

### 4.4 到区域的距离与可达性

在 `astar.py` 中增加小型区域目标搜索，复用 Grid 的合法邻接。到多个区域目标可用零启发式 A*，即 Dijkstra；首版场景规模下不需要新增规划依赖。

- 搜索得到 S0 可达的区域目标集合 `R_reach`，在其中取最短导航路径；路径终点为区域内可行位姿，不固定为一个 h 中心。
- 导航包络允许的旋转及目标 yaw 连接须合法。纯 yaw 恢复单独支持：XY 不变而朝向进入允许区间时，平移 Path Length 为 0。
- 使用实际输出折线的长度，包含 S0 到栅格中心、终点连接段。现有 `search()['length_m']` 仅是网格累计值，不能直接当完整路径长度。
- 欧氏距离取 S0 到同一可达区域 XY 投影的最小距离；区域采用网格与锚点近似，路径和欧氏距离使用同一表达。为零时 Detour Factor 为 `null`，原因 `zero_euclidean_distance`。
- Recovery Ratio 的局部候选集取 scene-datasets 中距 S0 不超过 `local_candidate_radius_m` 的合法候选站位，使用其真实 yaw；保存成员数/候选数。首版默认使用整个目标局部采样域，避免额外截断；可显式设置半径子集。

所有保留 S0 都计算指标后再做规模抽样。`max_episodes_per_scene=0` 表示保留全部；正数时按 seed 均匀抽取，不能按成功率最低或路径最短静默排序截断。

## 5. 数据格式与信息边界

### 5.1 Benchmark 目录

```text
<benchmark-run>/
├── config.json                 # 有效构建参数、输入引用、预算、版本标识
├── episodes.jsonl              # 可执行实例；一行一个 S0
├── oracle.json                 # 评分内部数据：共享区域 + 各 episode 证据
├── summary.json                # 筛选数量、原因、抽样与覆盖范围
└── scene-files/<scene_id>/     # 冻结的匹配评测场景；同场景只存一次
```

`oracle.json` 是构建/评分必需数据，不是第二套 episode 索引或原始 trial 副本。共享 region 按 scene/object/task 存一次；episode 部分只保存绑定、S0 成功数/总数、可达性见证、三个难度指标和分组标签。高率锚点保存计数及原标定引用，不复制 rollout。

`episodes.jsonl` 的最小字段沿用现有结构：`episode_id`、`scene_id`、`scene`、`task`、`station`、`seed`；目标仅包含原任务物理成功条件。删去当前混在 episode 中的 difficulty 标签，内部评分按 `episode_id` 关联 oracle。`task`/`station` 供环境初始化，不将整个字典发送给模型。

oracle 的区域表达保存地图 origin/resolution、所用自由单元/局部 patch、朝向区间、锚点及裁剪所需站位计数；评分只读取构建结果，不每次重新划区域。v0.1 先用 JSON 原生数组，不先引入压缩分片、数据库或重复派生索引。

### 5.2 评测目录与记录

```text
<evaluation-run>/
├── config.json                 # benchmark 引用、策略、模式、seed、实际预算
├── results.jsonl               # 每 episode 的实际执行结果和诊断
├── summary.json                # 总体/分组 TSR、Macro、覆盖率
└── rollouts/<episode_id>/      # 复用 AttemptRecorder 的事实与轨迹
```

`results.jsonl` 在已有任务结果字段上增加：

| 字段 | 含义 |
|---|---|
| `recovery_success` | `true / false / null`，按隐藏区域近似诊断 |
| `recovery_first_time_s` | 首次有效进入时相对 episode 开始的仿真时间；未进入为 null |
| `base_path_length_m` / `final_displacement_m` | 实际轨迹平移总长 / 最终位移 |
| `max_displacement_m` | 相对 S0 的最大位移，用于判断往返调整 |
| `base_yaw_travel_rad` / `final_yaw_delta_rad` | wrap 后逐段转角绝对值之和 / 最终相对转角 |
| `max_yaw_delta_rad` / `effective_adjustment` | 相对 S0 的最大偏航 / 是否超过平移或转角容差 |
| `diagnostic_status` | 诊断可用性及缺少轨迹等实际原因，与任务结论独立 |

轨迹采样沿用 recorder 的真实控制间隔边界，增加 `base_xyyaw` 三列和现有 `time_s` 对齐，保持状态数=动作数+1。路径长度按这个明确采样频率计算，不声称是连续物理路径的精确积分。

### 5.3 防止答案进入策略

环境进程持有 oracle，MolmoBot 子进程继续只通过现有 RPC 接收相机、允许的本体状态和任务指令。其请求中不发送 benchmark 路径、region、S1、成功率、Case/family 标签或 oracle 规划提示；不新增在线恢复奖励。

Open 继续使用现有首帧对象点条件；它只定位任务对象，不含恢复站位。若该条件参与评测，所有对照保持相同，并在协议里明确。首版支持现有 RPC 策略接口，不把拥有任意文件读取权限的自定义程序称作严格隔离评测；该类型的执行封装另议。

## 6. 闭环、可选对照与统计

### 6.1 Full policy

`evaluate-bench` 从固定计划列表逐条运行 `run_vla_attempt(..., path=None, goal=None)`。独立初始化一次，后续不恢复对象和站位；移动与操作均由策略动作产生。成功以现有 Pick/Open 物理证据确认，保留全部真实失败。

run 结束后从同一 recorder 轨迹计算底盘与恢复诊断，评分函数不参与策略动作生成。首次进入必须同时满足：底盘平移或 yaw 相对 S0 超过有效调整容差，以及 `contains(xy,yaw)`；诊断不要求最终停在区域内。

Recovery 检查基于实际位姿，不要求先经历一次操作失败。完整轨迹未进入为 false；缺少必要轨迹且未找到进入证据时为 unknown。若完整前缀已证明进入，则即使后续诊断中断仍可保留 true，并标注轨迹完整性。Task outcome 与记录/诊断可用性分别保存。

### 6.2 后续可选消融

固定底盘 policy 可以作为后续评测消融，使用相同 episode、观测、任务判据和预算，比较禁止/允许策略移动底盘时的表现。它不改变来源标定，也不纳入本轮必做开发、CLI 参数或交付数量。首版先完成 Full policy 的真实闭环、运动记录和区域诊断；这些诊断不单独证明移动的因果收益。

### 6.3 分组和分母

- Case：直接沿用 scene-datasets 的 Case1/Case2/Case3，作为首版主 Macro 的分组，不再把每个 episode 叫作一个 case。
- Task：`pick`、`open`。
- Failure family：优先使用原始标定的证据分类。首版采取单个主标签：`planning_limited`、`execution_limited`、`mixed`、`unknown`，并保留原始 reason 计数。只有明确的规划类失败才归第一类，物理执行失败归第二类；无法从旧记录判断的 timeout 保持 unknown，不强行解释为遮挡或不可达。
- 原始 Reachability/Collision 等诊断可作为细粒度附加标签展示，但未经过具体证据确认时不宣称因果 family。Case 构造类型不直接冒充失败原因。
- 输出 `macro_by_case`、`macro_by_task`、`macro_by_failure_family`，主 Macro 为 `macro_by_case`；分别等权平均，不跨不同分组再平均。
- 每组和总体保存 planned、success、failure、timeout、unknown、not_executed 及 coverage；完整时才给正式 SR/Macro。有效子集 SR 单独命名，缺失组不从 Macro 中消失。
- Recovery Success 自带诊断覆盖率，以全部计划 episode 为完整性边界；未知诊断不记成 false。底盘长度分布保存有效条数，可报告总体和成功子集，避免只展示成功轨迹。

不在首版增加自动因果诊断系统或新的解释模型。

## 7. 配置与命令设计

仅新增 benchmark 构建配置，实施时落地；已有标定配置继续作为输入引用，不新建强制重跑标定的配置：

| 配置 | 主要字段与首轮建议 |
|---|---|
| `configs/benchmark/v0.1.json` | low=0.20、high=0.60；region_radius_m=0.10、region_yaw_tolerance_rad=15°；map_resolution_m=0.05；navigation_whole_body=true；local_candidate_radius_m=null 表示整个目标局部域；max_episodes_per_scene=0；seed=42 |

首轮预算先沿用现有 `horizon=400`（40 s 仿真）、`wall_timeout_s=600`，有效调整建议阈值为 0.02 m 或 2°。region/yaw 参数是起步配置，不是定义常数；首批必须检查实际站位间距、裁剪和恢复机会覆盖，再冻结正式配置。若未测出高率站位，先定位真实标定原因，不通过降低 60% 门槛补样本。

命令调整：`build-bench` 新增 `--config`，正式构建移除必需的 `--policy-config`；评测保留 `--policy-config`。不新增准备场景或固定底盘模式。构建 seed、预算和抽样统一放配置，不重复维护多组 CLI 默认值；同步更新相关测试及手册，不增加旧参数兼容壳。

以下均为**实施后的目标接口示意，当前不可直接据此执行**：

```bash
# 直接使用已有输入，不先重新标定
.venv/bin/lastmile-dataflow build-bench \
  --dataset <existing-scene-datasets> --calibration <existing-calibration-run> \
  --config configs/benchmark/v0.1.json --output <new-benchmark-run> --gpu <gpu>

.venv/bin/lastmile-dataflow evaluate-bench \
  --benchmark <benchmark-run> --policy-config configs/policies/molmobot.json \
  --output <new-full-evaluation-run> --gpu <gpu>
```

## 8. 分阶段交付与验证

阶段顺序按依赖执行；每阶段更新一份实际交付记录，不为各小步骤新建报告。

| 阶段 | 主要交付 | 验证与完成条件 |
|---|---|---|
| P0 已有输入读取与资格统计 | 来源 scene-datasets、既有标定计数、参考协议及 S0/H 候选 | 可读取已有开放底盘标定，按其原计划判断完整性，列出每任务低/高率数量与可用覆盖范围；不执行新 rollout |
| P1 正式构建 | region、到区域路径、难度指标、episodes/oracle/summary | 至少一个 S0 同时满足 ≤20%、同任务高率 ≥60%、合法可达；没有合格样本则保留真实负结果并处理实际瓶颈 |
| P2 闭环与行为评分 | Full policy、底盘轨迹、恢复诊断、分组汇总 | 为选定策略完成必要接口适配并评测首批 episode，SR 与覆盖率能核对；得到真实自主调整后任务成功的演示才能宣称恢复链闭合 |
| P3 Open 与首批扩展 | 相同管线接入 Open，冻结 Case/family 与样本列表 | Open 至少一个合格真实 episode 并完成评测；Pick/Open 各自报告覆盖范围，不以 Pick 成功替代 Open 验收 |
| P4 小规模正式交付 | 确定的 benchmark + Full policy 结果 + 使用说明 | 输入与配置引用明确，任务/恢复/底盘/难度/分组统计齐全，完成需求 A1–A8 对照记录 |

首个构建目标是从已有结果中提取 1 个对象上的 1–5 个合格 S0；先验证读取、区域与路径，不再先做整图标定。流程打通后首批目标建议为 Case1/Case2/Case3 各 5 个 episode（约 15 个），从已有场景候选中生成；不足时报告实际数量，不复制样本凑数，也不把 15 作为算法正确性门槛。

### 8.1 必要的软件验证

围绕会改变研究结论的行为补测试，不穷举低概率异常组合：

1. 原始 1/5 和 3/5 分别满足低/高边界；开放底盘 trial 正常参与资格统计；沿用来源计划判断 2 次、5 次等不同批次的完整性，不跨对象、任务或不同参考协议借证据。
2. 小地图检验穿墙、绕行、最近可达 region、yaw 成员、低率点裁剪和纯转向；验证路径长度包含端点连接段。
3. 用短位姿轨迹验证“往返移动但最终位移为零”、纯转向、进入后离开和缺轨迹的恢复/运动指标。
4. 用组规模不均的结果验证总体 TSR 与 Macro 不同、未知/未执行不冒充失败、缺失组不被移除。
5. 验证构建不启动新标定、不改变来源物理控制配置；Full 策略请求无 S1/region/path，实际评测适配保持任务对象与初始场景状态。

测试放入现有 `tests/planning/`、`tests/data/`、`tests/runtime/` 和 `tests/workflows/` 对应目录。相关测试通过后运行仓库全套离线回归；只有出现新失败或新改动才重复扩大验证。

### 8.2 真实验收与运行规模

依次验证既有数据的非空构建、可绕障导航口径、Full policy 闭环和 Open；不同执行试验使用新 attempt/run。构建复用已有标定，新增标定 rollout 预算为 0；正式 N 个 episode 的 Full policy 每轮为 N 次。先测读取/建图和单 episode 执行耗时，再决定扩量。若已有输入缺少合格低/高率站位，报告具体缺口，不将整个项目自动改为重做标定。

实际执行时查看 GPU 0–7 利用率和显存，选空闲设备后通过 CLI 传入；不固定卡号，不干预已有任务。基础设施/共享模型故障停止批次，剩余计划保留未执行；普通任务失败继续其他 episode。

若 Full policy 全失败，保留 TSR 和诊断结果，说明恢复能力未得到正例证明。A*→S1→cuRobo 的 oracle 演示可用于检查场景与物理链路，但须单独标识，不能填入 Full policy 成功样本，也不能替代自主恢复验收。调试与正式评测分 run，正式计划内全部成败都保留。

## 9. 文件落点与最终交付

优先修改现有文件：

- `workflows/lastmilebench.py`：已有标定读取、构建与评测编排。
- `data/evaluation/benchmark.py`：S0 筛选、结果汇总和 Macro；新增 `data/evaluation/recovery.py` 集中区域、难度和轨迹诊断。
- `planning/astar.py`：到 region 的搜索，复用原有 Grid。
- `data/evaluation/selection.py`、`runtime/execution_config.py`：按来源 trial/配置读取参考标定协议和每站位完整计数；不更改标定执行语义。
- `runtime/execution/molmobot.py`、`data/recorder.py`：策略闭环、显式底盘轨迹及诊断所需结果；实际 native 等控制适配单独按策略接口需要修改。
- `cli.py`、对应配置及相关测试：保持五个命令，落实目标参数。

完成实现后同步 `docs/运行手册.md`、`docs/data-format.md`、配置说明和 README 的当前边界；按用户目录要求，新增长期说明优先放 `reports/benchmark/docs/`，现有入口只补必要链接，不批量搬迁无关文档。

实际交付记录放 `reports/benchmark/delivery/phase_01/` 起的对应阶段目录，写清运行输入、有效配置、命令、结果、失败原因、真实证据位置和未验证范围。最终只保留必要场景、标定引用、benchmark、实际 rollout 和汇总；不生成重复 trial、文件指纹或额外封存体系。

当前可开始的下一步是 P0：读取已有 scene-datasets 与标定 run，确认 S0/H 候选计数，随后直接实现 region、路径筛选和 benchmark 导出。策略接口适配放在 P2，不先扩展标定管线。
