# lastmile-datasets 生成需求

- 状态：active
- 日期：2026-10-24
- 替代：无
- 关联：[交付报告](../delivery/01-implementation.md)、[PandaOmron 当前实验需求](../../curobo-time-optimization/requirement/02-panda-omron-time-optimization.md)

2026-10-16 提出的数据集生成需求保留；软件实现已落地，真实采集待验证。当前实验主体与标定次数以 PandaOmron 需求为准，不改变本文的两类示教和物化定义。

**目标：先生成引用型 lastmile-datasets，再通过独立物化处理，将两类成功示教及训练必需数据复制到新的自包含数据集。物化完成后可整体搬迁、发布和直接用于约定的训练读取器，不再依赖 station_evaluations 或原采集目录。**

## 1. 范围与输入

- 继续使用 `collect-lastmile`，不新增 CLI 命令，不恢复历史格式或旧管线。
- 输入为场景库、执行配置与匹配的标定 run；标定包含原始 `trials.jsonl` 和实际 rollout。
- 引用型生成阶段不重新执行 S1 操作，不复制第二套 trial 或轨迹；物化阶段只复制所选示教及必需数据。省略 `--calibration` 时，沿用当前 run 内标定一次的能力。
- 场景、任务、机器人控制、传感器、后端及实际执行参数必须匹配；选点与配对参数不应冒充执行条件，单纯调整它们不应要求重新 rollout 标定。
- 本文规定目标行为，不代表当前软件已支持这些输出。

## 2. 选点只决定资格，配对只做全组合

同一场景、同一任务内，从原始有效 trial 计算 `rate = successes / evaluable_trials`。基础设施错误或未知结果不算任务失败；不完整的标定不得用于正式选点配对。平滑热图只用于展示。

```text
Calibration → 原始 trial 实测成功率
    eligible_S0 = {station | rate <= lastmile_low}
    eligible_S1 = {station | rate >= lastmile_high}

Pair Generation
    pairs = eligible_S0 × eligible_S1
```

- 阈值含等号，满足 `0 <= lastmile_low < lastmile_high <= 1`；有效阈值随 run 保存。
- 保留全部合格 S0，不只取最低成功率的一个或前几个；保留全部合格 S1。
- `lastmile_nearby_m`、`max_s1_candidates`、`start_count` **均不参与正式 lastmile-datasets 的资格截断或配对**。
- 不根据距离、成功率排序名次或 A* 是否有解，事先删除 pair。远 S1 也保留。
- 若有 2 个合格 S0、3 个合格 S1，必须生成 6 个唯一有向 pair；不跨场景或跨任务组合。
- 任一集合为空时，配对集合为空。这不影响已有成功直接操作示教的纳入。
- 本次不顺带改动困难 S0 benchmark 的选点定义。

## 3. 每个 pair 独立执行连续示教

每个 pair 默认安排一次独立 attempt：在 S0 初始化，A* 规划并实际导航到 S1，随后执行操作。执行器内部已有的操作重试不算新增 pair。

- 每次使用新的 Simulation / attempt；执行开始后不恢复快照、不重置、不传送。
- 连续轨迹必须来自同一次真实执行，不能把标定操作轨迹拼接到导航后。
- A* 无路径仍保留 pair 和原因，但不调用执行器、不生成虚假 rollout。
- 导航失败、操作失败、超时、初始化/仿真/记录错误分别保留；未知不能改写成任务失败。
- 单个 pair 的局部失败不取消其他 pair。共享模型或 GPU 故障停止批次，未执行 pair 保持未执行并记录停止原因。
- 全组合不能通过候选上限静默截断；若批次中断，必须能区分完整计划与已执行部分。

## 4. 三种记录各有职责

| 记录 | 职责 | 不承担的职责 |
|---|---|---|
| 原始 trial / attempt | 保留标定与执行事实、任务判定和实际轨迹 | 不复制成另一套标定 |
| `pair_index.jsonl` | 全量配对计划及其执行结果；每个 pair 一条 | 不等同于成功示教列表 |
| `train_index.jsonl` | 成功示教入口，区分来源类型；物化后指向内部副本 | 不充当全量实验结果或另一套标定 |

`pair_index.jsonl` 是本阶段配对的主记录，不再额外维护内容相同的 `episodes.jsonl` 配对副本。`train_index.jsonl` 是面向训练的筛选视图，不是第二套 trial。

### 4.1 pair_index 保留全部 pair

- 执行前先保存完整配对集合；执行后仍是一 pair 一条逻辑记录，结果写回方式由实现方案确定。
- 最小内容：`pair_id`、`scene_id`、`task_id`、`s0`、`s1`、两端实测率、执行状态、原因、真实 rollout 引用及下节导航指标。
- 计划内尚未执行、无路径、已执行失败、错误和成功都必须可区分；批次停止不能让剩余 pair 消失。
- 没有实际 attempt 的 pair 不分配虚假 rollout；pair 身份与 rollout 身份分开。

### 4.2 train_index 明确两种 source_type

| `source_type` | 轨迹来源 | 纳入条件 |
|---|---|---|
| `s1_direct_operation` | 已有 calibration attempt，直接操作 | 真实操作成功、记录完整且存在真实执行轨迹 |
| `s0_to_s1_continuous` | 本次 pair 的完整连续 attempt | 实际导航成功、操作成功、记录完整且存在真实执行轨迹 |

**本轮按“所有真实成功的 calibration attempts 都可以加入”解释：直接操作分支不再受高率阈值限制。** 即使站位整体成功率低，其真实成功且完整的某次 trial 也可以纳入。`eligible_S1` 仅决定连续配对目标；`s1_direct_operation` 表示直接操作来源类型，不保证来源站位一定属于高率集合。若仍需限定高率站位，应明确修改本条，而不是在实现中隐式过滤。

- 同一站位的多条合格成功 trial 全部纳入；同一原始 attempt 只纳入一次，不随配对次数重复。
- 导航成功不等于操作成功，文件存在或执行结束也不等于成功。成功依据来自原始任务判定，记录完整性单独判断。
- 两类记录只共享真实公共字段；各自保留必要专属字段，不强行补成同一形状。
- 直接操作记录没有 `s0`、`pair_id`、`navigation` 或导航难度字段；既不填 `s0: null`，也不伪造导航成功。
- 引用型阶段以来源 run 与 rollout ID 共同定位原始 attempt，避免不同 run 的局部编号碰撞；引用相对当前输出 run 解析，仍依赖来源目录。物化后以内部 `rollout_path` 读取，原始身份只用于追溯和去重，不再参与数据加载。

以下为引用型记录的字段示意，不是真实结果；两类结构不强行补齐：

```jsonl
{"source_type":"s1_direct_operation","scene_id":"scene_a","task_id":"pick_a","station_id":"station_b","source_run":"../../../station_evaluations/small/curobo_v2_001","rollout_id":"r000007"}
{"source_type":"s0_to_s1_continuous","scene_id":"scene_a","task_id":"pick_a","pair_id":"p000001","s0":"station_a","s1":"station_b","source_run":".","rollout_id":"r000000","euclidean_distance_m":1.42,"astar_path_length_m":2.31,"navigation_steps":827}
```

## 5. 远 S1 不删除，用实测指标描述导航难度

- `euclidean_distance_m`：S0、S1 底盘平面位置的欧氏距离，单位米；所有 pair 均可计算。
- `astar_path_length_m`：A* 返回的计划路径折线长度，单位米；统一长度口径，不能混同实际行驶距离。无路径或未完成规划时为未知，不填 0。
- `navigation_steps`：实际完成的 `navigation` 阶段控制间隔数，不是底层物理子步，也不是整条轨迹总步数；`arrival` 和操作阶段不计入。失败时保留已经执行的导航步数，不能只在导航成功时记录。
- 确认未下发导航控制时步数为 0；实际步数无法取得时保持未知，不按规划长度估算或补齐。
- 指标保存在 pair 结果中，成功连续示教也携带这些指标，便于后续按导航难度分析；不得据此隐式删除远距离成功示教。

## 6. 独立数据集物化处理

物化是**独立于标定、规划和采集的后处理**：读取已生成的引用型数据集，在新输出目录复制数据并重写读取路径。不启动 Simulation、GPU rollout 或策略，不重新选点、配对、筛选成功结果。可在采集结束后单独执行；具体接口由方案确定，仍不新增第六个顶层 CLI 命令。

### 6.1 复制范围与内部路径

- 以输入 `train_index.jsonl` 为唯一示教集合，统一复制 calibration 成功轨迹与本次连续成功轨迹；原始输入和既有数据保持只读，不在原处改写。
- 以唯一源 attempt 为复制单位，至少包含 `trajectory.npz`、`recording.json`、训练必需的图像/深度等观测，以及读取和解释数据所需的任务、控制、相机与有效配置元数据。视频等非必需诊断附件按需复制，不搬整个 station_evaluations。
- 输入采用了派生训练文件时，其依赖的图像、元数据同样内部化；不能只复制 `data.npz` 而保留外部图像依赖。不存在的必需模态不能伪造或补成完整。
- 两种来源统一存入物化输出的 `rollouts/<dataset_rollout_id>/`；输出 `train_index.jsonl` 的 `rollout_path` 相对物化数据集根目录解析，不依赖调用目录。
- 训练会读取的索引、配置及观测引用均按各自格式改成内部相对路径。不得依赖外部软链接或移动源数据来冒充复制；不对所有历史 JSON 字段做无差别替换。
- 搬迁后无需读取原 calibration、原连续采集 run 或场景库。此处承诺的是训练数据自包含，不是仿真重放资产、模型权重或训练软件环境打包。

### 6.2 来源元数据与去重关系

- 每条物化训练记录保留原 `source_type`、场景/任务/站位信息；连续示教保留 pair 关联和导航难度。两类记录仍保持各自结构，不添加伪造导航字段。
- 保留 `source` 元数据，至少含原始来源 run、原始 rollout ID；必要时保留原 trial 或 pair 身份。该元数据是历史来源说明，即使原目录不可访问也不影响加载。
- 用原始来源 run 与 rollout ID 识别同一 attempt；同一源 attempt 只复制一份，重复引用指向同一个内部 `rollout_path`。不同 run 下编号相同的 attempt 不能合并，也不按轨迹内容相似度去重。
- 不因去重丢失已有的来源或 pair 关联，不凭重复引用扩增训练样本；直接操作与真实连续轨迹不能因共享 S1 而合并。
- 来源身份与内部副本的映射保存在现有训练记录中，不额外生成逐文件清单或重复映射索引。

物化后的直接操作记录示意；`source.run` 仅为追溯，不作为读取路径：

```json
{"source_type":"s1_direct_operation","scene_id":"scene_a","task_id":"pick_a","station_id":"station_b","rollout_path":"rollouts/d000001","source":{"run":"outputs/station_evaluations/small/curobo_v2_001","rollout_id":"r000007"}}
```

### 6.3 配对记录与完成条件

- 物化输出保留完整 `pair_index.jsonl` 及必要有效配置和汇总，不能只留下训练成功子集。已物化成功轨迹的 pair 引用改为内部路径；未入训练集的失败 attempt 不要求复制，其来源信息仅用于追溯，不承诺副本可读。
- 物化不修改原始成功/失败判定，不新增配对或模拟执行结果。缺少必需数据、复制失败或中断时明确报告未完成，不能静默丢样本后宣称完整交付。
- 完成条件：全部唯一成功源 attempt 的必需数据已复制，训练读取路径全部在输出内部，来源与去重关系保留。汇总报告输入示教数、唯一轨迹数、实际物化数量和完成状态。
- 将输出整体复制到另一位置，在读取器无法访问源目录的条件下，能够加载两类样本的实际训练必需数据并组成训练 batch；验证不得删除或移动原始数据。
- “直接训练”需明确对应读取器、动作格式和必需模态，并通过实际 batch 读取验证；物化本身不等于自动适配任意模型格式，也不代表已验证训练效果或仿真重放。

## 7. 运行手册与最小验收要求

- 后续实现时将 [运行手册](../../../docs/运行手册.md) 拆为第 2 节“逐点标定”、第 3 节“lastmile-datasets 生成”；原 benchmark 与结果说明顺延为第 4、5 节。MolmoBot 标定说明归第 2 节，不混入 cuRobo 连续采集。
- 第 3 节分别说明引用型生成与独立物化的步骤、输入输出和依赖区别；`--calibration` 指向含 `config.json` / `trials.jsonl` 的具体 run，而不是其父目录。
- 配对数量等于各场景任务 `|eligible_S0| × |eligible_S1|` 之和；改变附近距离与两个数量上限，不改变正式 pair 集合；阈值边界包含等号。
- 无路径与停批后未执行 pair 不丢失；没有 rollout 就不出现训练轨迹。汇总分别报告计划数、执行覆盖、各类结果及两种来源的训练条数，不把未执行归为任务失败。
- 无 S0 时仍能纳入成功 calibration；低率站位的成功 trial 也能纳入；失败、未知及记录不完整 trial 不纳入。
- 直接操作条目无伪造导航字段；连续条目有真实导航与操作成功依据；一个 calibration attempt 不因多个 pair 重复进入训练集。
- 物化验收覆盖两种来源、重复引用去重、跨 run 同编号不误合并，以及搬迁后脱离源目录加载真实 batch；配对全集和任务判定不变，来源可追溯。
- 软件回归只证明这些规则；真实成功示教、GPU rollout 与训练格式适配需另行验证，不在本需求文档中宣称通过。

## 8. 实现前核对与验证边界

实现前核对了 [selection.py](../../../src/lastmile_dataflow/data/evaluation/selection.py)、[collection.py](../../../src/lastmile_dataflow/workflows/collection.py)、[标定与采集测试](../../../tests/workflows/test_collection.py) 和 [配置说明](../../../configs/lastmile_datasets/README.md)：当时选点受 `start_count` 与附近距离限制，连续采集截断 S1 候选，并写入 `episodes.jsonl`。这些是本次替换的行为，不是新实现的能力说明。

实现前的非归档跟踪文件未找到 `collect_views()`、`all_attempts_index`、`train_index` 或 `pair_index`。本次新增所需配对与训练索引，不恢复旧模块或额外的全 attempt 事实库。

本文保留需求定义；软件验证、搬迁读取和未运行的真实采集边界统一见交付报告，不把离线回归等同于真实任务成功或模型训练效果。
