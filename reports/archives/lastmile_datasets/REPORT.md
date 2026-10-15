> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# LastMile Datasets 初版方案：复用 cuRobo，先完整记录，再适配 MolmoBot

日期：2026-10-12（Asia/Shanghai）  
状态：**实现方案待评审；本次没有修改采集代码，没有运行真实 rollout。**

## 结论

**最简实现是在现有 collector 外增加一个场景包批量入口，并对现有 collector 做少量选点与发布调整。** 不重写 cuRobo、不重写连续导航、不重写 raw/canonical recorder，也不重写 MolmoBot adapter。

主线为：**scene-datasets 冻结输入 → cuRobo 逐点实测 → 低率 S0 / 高率 S1 → 连续 S0→S1→操作 → raw/canonical 数据 → 独立 MolmoBot 派生包。**

按用户最新要求，采集协议以“保存更多真实信息，模型格式后适配”为原则。本方案撤回上一版将 native 控制转换和 native benchmark 标定放在采集前面的安排：**首版沿用场景包和 cuRobo 已有的 position 控制配置。** native 控制适配、模型训练和完整 benchmark 重构都不是原始数据采集的前置条件。

需要保留一个真实边界：现有 adapter 已支持状态、图像和动作导出，但 **position 夹爪动作不能无标定地转换为 MolmoBot native motor 动作**。因此“原始数据可供后续使用”与“已经得到完整动作监督、可以直接训练”分别验收，不能混称。

## 1. 范围：只打通一条已有模块组成的管线

### 首版做什么

- 消费 scene-datasets 已构建、检查并封存的障碍物场景；不再构造场景。
- 对包内指定任务和冻结采样点执行 cuRobo，多次独立试验，保存实测率表。
- 选择低率 S0 及附近高率 S1，真实执行导航和后续操作。
- 所有试验和连续 episode 均保留已有 raw/canonical 数据；成功训练视图另行筛选。
- 保存源场景、采样点、标定、选点和连续轨迹之间的可追溯引用。
- 调用已有审计与 MolmoBot adapter，不在采集时压缩成 22D state / 20D native action。
- 输出共用的成功率证据与选点关系，为 LastMileBench 提供可复用输入。

### 首版不做什么

- 不恢复原五阶段路线，不调用旧 `lastmile_pipeline`。
- 不新增场景编辑、复杂难度分级、复杂路径证明、策略恢复算法或通用调度框架。
- 不因模型输入需要而更改源夹爪控制、物理频率、相机 FOV 或源包。
- 不要求先运行 MolmoBot 推理或训练；不实现新的 π0 adapter。
- 不把格式导出当作模型学习效果或控制器等价性证明。

**采集路径支持现有 Pick/Open 分支；首次真实验收先做 Pick。** Open 使用绑定到具体关节的任务条件单独验收，不能用 Pick 成功替代。

## 2. 数据分层：模型只影响派生层

最简管线与数据分层

*图 1（解释类）：蓝色表示有现成代码依据的模块，橙色表示首版需要补齐的编排连接；不表示这些连接已通过真实端到端验收。原始采集不依赖 MolmoBot native 格式；benchmark 复用证据，但仍检查自己的配置兼容性。*

| 层 | 保存什么 | 首版实现方式 |
|---|---|---|
| 场景输入 | 模型、状态、任务、障碍物、构造证据、冻结站位和配置身份 | 复用 `scene_datasets` 封存包，只读消费 |
| raw | 全状态、原始 RGB/depth、请求、提交命令、实际 ctrl、接触、时间与模型映射 | 复用现场 recorder，标定和连续执行都启用 |
| canonical | 帧/区间引用、分组状态、执行事实、标定、任务、结果与场景身份 | 沿用 `raw-first-canonical/1.0.0` |
| LastMile 关系层 | S0/S1、率表、源包摘要、失败证据、路径、阶段与入集理由 | 小型编排元数据，不再发明一套状态/动作格式 |
| derived | MolmoBot state22、图像变换、native action、chunk/mask、浏览视频 | 复用 adapter；写到源 episode 外的新目录 |

协议依据为 scenes-datasets-design 的 [canonical 协议](../../../../scenes-datasets-design/docs/datas/canonical-data-protocol.md)、[实现说明](../../../../scenes-datasets-design/docs/datas/raw-first-implementation.md) 与 [scene-datasets 输入契约](../../../../scenes-datasets-design/docs/datas/scene-datasets-v1.md)。本次比对，这三份文档和 [data-format](../../../../scenes-datasets-design/docs/datas/data-format.md) 与本 worktree 的副本逐字一致，不需要另建协议副本。

“多记录”指保留可获取、已声明的信号，不指补造没有采到的信息，也不要求第一版新增所有潜在传感器。当前完整状态/RGB 采样在实际执行区间边界；ctrl 记录所有实际提交，接触逐 physics tick 记录。**不能称为每个物理 tick 都采了全状态和图像。**

## 3. 现成代码在哪里，哪些地方确实要调整

以下路径相对 `src/lastmile_dataflow/`，链接指向本 worktree 当前源码。

| 能力 | 现有入口 | 初版处理 |
|---|---|---|
| 验证与绑定场景包 | stations/scene_package.py | 直接复用，不重新采样 |
| 包输入接入 collector | workflows/no_edit.py 的 `collect_batch/run_scene/prepare_execution_stations` | 每个 sample/config 使用独立子 run |
| cuRobo 逐点执行与汇总 | 同文件 `collect_task`；stations/no_edit_statistics.py | 复用试验循环；分开标定结束和闭环结束 |
| cuRobo 规划与真实操作 | runtime/no_edit_v2.py、[planning/curobo_v2.py](../../../src/lastmile_dataflow/planning/curobo_v2.py) | 不重写规划与操作算法 |
| 连续导航与操作 | runtime/no_edit_execution.py 的 `run_raw_attempt/navigate`；navigation/astar.py | 直接复用，补充明确到站复核与阶段引用 |
| 低/高率邻近筛选 | benchmark/episodes.py 的 `select_starts` | 复用规则；入口先检查完整试验数与点集身份 |
| 现场完整记录 | recording/canonical.py、recording/recorder.py | 显式启用 raw-first/depth；复用原有封存流程 |
| 数据审计与全结果索引 | validation/canonical.py、exporting/canonical_index.py | 原样审计各 attempt；父 run 聚合子 run 索引 |
| 连续段兼容导出 | exporting/no_edit_layout.py 的 `export_rollout` | 复用 `no-edit-dataset-v2` bundle，不改旧格式解释 |
| MolmoBot 派生导出 | integrations/molmobot_dataset.py、molmobot_images.py | 直接调用已有 adapter，不反向限制采集 |

现有 collector 已经包含“逐点试验 → 统计 → A* → 连续操作 → 导出”。真正需要调整的是以下连接，而不是增加另一套算法：

1. 当前 CLI 一次只绑定一个 `scene_package`；增加遍历 dataset 中多个 sample/config 的薄入口。
2. 当前 S0 只要求率 `<1`，S1 只要求有一次成功；改为显式低/高率规则。
3. 当前整体平均率低于 `discard_below` 会挡住闭环；新模式不使用它作为 LastMile 配对资格门槛。
4. 当前 `complete` 可包含 episode errors；新选点资格必须检查有效试验数，不能拿异常点凑低率。
5. 当前标定与连续采集混在一个 task 中；增加阶段完成记录，让标定可单独交付和复用。
6. 当前成功 bundle/index 不等于逐条 canonical 审计通过；发布训练视图前补审计 gate。

旧 `collect-no-edit` 默认行为保持不变。新增 LastMile 模式才启用这些规则，避免重新解释历史结果。

## 4. 输入准备：不重构场景，不重新发现任务

总入口读取 dataset manifest 与包清单，选择有效且封存完整的 sample/config。父 dataset 为 partial 时，可处理其中独立封存且检查通过的子包，但保存原始缺额与排除清单，不能声称全量完成。

每个处理单元为 **`sample_id + robot_sensor_config_id`**，而不是 house。相同 house 的多个障碍物变体使用独立子 run，避免现有 `scenes/val_103` 路径覆盖。

处理单元准备步骤：

1. 调用 `package_manifest` 校验摘要、任务、配置和站位。
2. 从包内 `scene/version.json` 读取冻结机器人配置，不套用默认机器人 JSON。
3. 从包内 `task.json` 读取任务；复用 collector 的匹配检查，不扩大到同 house 其它目标。
4. 使用 `stations/selected.json` 的全部点及完整 `initial`，禁止重新生成 lattice 或截取前若干点。
5. 初始化新执行 run，复用 `bind_package` 和 `Simulation.from_snapshot`。

执行分辨率、采样半径、spacing、可见性门槛等必须与源包绑定兼容。默认沿用源包实际采集配置，例如 position 包的 320×240，不因为 MolmoBot 后续输入要求就虚称已采到 1024×576。若需更高分辨率采集，应显式建立新传感器配置及相应有效性检查，不修改旧包。

现有 collector 仍校验 ProcTHOR 原 XML/metadata 摘要和只读资产路径。首版继续使用这一约束；本方案不是脱离全部外部资产的通用分发实现。输入缺失时提前报告，不等到 GPU 执行中才发现。

## 5. 标定与选点：只使用真实计数

### 5.1 复用逐点试验

首版每点默认 **5 次**独立试验，复用 `task_seed`、`execute_isolated_episode`、`run_raw_attempt` 和 `aggregate`。每次新 Simulation、新 attempt；失败证据保留。独立试验可以从同一冻结初态初始化，连续 episode 内不能重置。

沿用 `curobo_v2_v080` 和现有 `holonomic_joint_planning` 操作模式，不新增固定底盘实验。因此实测率是“从这个站位出发，在声明的移动操作模式和预算内完成任务的比例”，不是固定底盘可达率。

| 结果分类 | 有效标定计数 | 记录要求 |
|---|---|---|
| success | 有效且成功 | 真实物理任务成功证据 |
| failure | 有效且非成功 | 实际失败位置及原因 |
| timeout | 有效且非成功 | 声明的任务预算结束，不当成基础设施失败 |
| planning_no_solution | 有效且非成功 | 合法有限预算请求无解；单独保留，无执行时无动作/视频 |
| initialization / simulation / recording / infrastructure / episode error | 默认不作为正常有效任务结果 | 保存分类与已采内容，不冒充低成功率 |
| 未执行 / 中断 | 不完整 | 不填充名义动作或成功率 |

每点 `rate=successes/evaluable_trials`；无有效试验时为 null。正式选点要求 **5/5 有效 trial**（或配置声明的次数），不只检查现有 `complete`。已有异常重试证据不覆盖；同一 trial 只能有一个被采纳的有效结果，替代关系显式保存。缺数点允许补齐，但不能在没补齐时用于正式组合。

任务成败沿用现有 Pick/Open oracle 与 `runtime-record-only-v1` 接触政策，不为新字段改变终止条件。成功判据名称、参数、单位、保持时间和代码身份写入冻结执行证据；不能仅因规划成功、动作完成或夹爪闭合就标记成功。

### 5.2 统一选择 S0 与 S1

首版默认沿用已有 benchmark 的低/高率约定：

| 参数 | 默认值 | 用途 |
|---|---:|---|
| low | 0.2 | S0 的实测率 ≤ low |
| high | 0.8 | S1 的实测率 ≥ high |
| nearby_m | 1.0 m | 两点 XY 直线距离上限 |
| starts_per_sample | 3 | 每个 sample/config 最多选择 3 个 S0 |
| min_successful_rollouts | 1 | 成功训练视图至少有 1 条合格闭环 |

这些是初版可配置约定，不是已经优化的统计结论。5 次试验下，低率意味着最多 1 次成功，高率意味着至少 4 次成功；不宣称精确估计了真实概率。

复用 `benchmark.episodes.select_starts`，只向其传入本次冻结 selected 集合和完整有效计数。S0 按率升序、station ID 打破并列；每个 S0 的高率邻点按率降序、距离升序、station ID 排序。原 benchmark 的 `s_plus` 对应这里的高率候选集合，最终执行的一个候选记作 S1。

保存率表、所有候选关系、最终 S0/S1、阈值、距离与 attempt 引用。无组合记 `no_eligible_pair`，不偷偷放宽阈值或退回任意点。整体平均率低不是单独淘汰条件。热力图可继续生成，但 **Gaussian 插值只用于展示，不用于选点**。

### 5.3 率表是公共证据，不是另一套轨迹格式

尽量兼容已有 `lastmilebench-station-rates-v1` 的 backend、dataset/sample/config/model/task 绑定及 `station_id/trials/successes` 字段，额外保存：

- `selected_stations_sha256`、`source_package_sha256` 和冻结执行配置摘要；
- planner/backend、control profile、底盘模式、成功判据和预算；
- scheduled/evaluable/error/timeout 计数、完整性；
- 原始 trial/attempt 及文件摘要引用。

每个 sample/config 一份率表。多个配置不能只按 `<sample_id>.json` 覆盖：放在 `<config_id>/` 命名空间，或给下游明确选择一个配置。摘要绑定不匹配时拒绝复用。现有有效标定允许导入，不必一律重跑；只有来源、试验次数、点集与冻结执行条件全部匹配才可跳过已有 trial。

## 6. 闭环执行：S0→S1→操作必须是一条真实 episode

对每个 S0 的高率候选，复用已有 A* 搜索和 `run_raw_attempt(..., path=..., goal=..., winning_control=...)`。

1. 新建独立 attempt，在 S0 的完整冻结姿态初始化。
2. 搜索 S0→S1 路径；无路径保存 `path_unavailable`，换下一个已选高率候选，不否定率表本身。
3. `begin()` 后连续 stepping 导航，保存规划路径与实际状态。
4. 复用现有 `navigate` 的控制闭环和到站保持，在导航返回时明确复核实际 XY/yaw 与冻结容差；超差不得仅凭函数结束称到站成功。
5. 从实际到达状态重新规划和操作。S1 的成功 grasp/seed 可以作为候选提示，但不恢复 S1 快照，不回放旧轨迹。
6. 执行任务 oracle，保存结果与所有原始记录。每个 S0 找到一条合格成功轨迹后即可停止尝试其它 S1，维持最简采集。

连续段沿用最多一次外层操作重试；它是同一 episode 内有边界记录的动作续接，不能恢复场景。如果需要从头独立重试，则创建新 episode。重试预算与实际次数必须保存。

S0 的低率失败证据来自独立标定 attempts。**首版不要求先在当前 episode 中失败一次再导航，也不把 S0 标定失败视频拼成当前连续轨迹。**

阶段至少包括 navigation、arrival、manipulation、hold、termination。现有 trajectory 的 phase 继续保存；LastMile 关系元数据补起止 frame/step/time 引用，规划阶段没有物理执行时只记录事件，不能伪造 action。

最终状态分开保存：配对是否合格、导航是否成功、操作是否成功、仿真是否合法、记录是否完整、canonical 审计是否通过。`no_successful_rollout` 仍是一次有证据的数据采集结果，不删除已产生的 raw/canonical。

## 7. 数据保存：沿用原始协议，只补 LastMile 关系

### 7.1 首版显式启用的记录

- `CollectionConfig.raw_first=true`、`record_depth=true`，拒绝使用现有 `no_raw_first.json` 作为正式采集配置。
- 全 qpos/qvel/ctrl、act、qfrc_actuator、sensordata 与模型映射，按 recorder 的实际能力保存。
- 头部和双腕的无损 RGB、可用 depth 与 mask、逐图外参及静态标定。
- 第三人称沿用当前 cuRobo 默认开启；raw observer 与带覆盖层的视频分别保存，observer 不作为策略输入。
- 原始意图、校验/投影后实际提交命令、控制生效区间、真实状态响应及逐 tick 接触。
- 原始 task/scene/source、目标实例、障碍物构造证据的引用、planner 诊断与耗时。
- 实际仿真时间、control/physics tick、墙钟时间、预算、种子和代码身份。

不只保存 MP4，不只保存 MolmoBot 22D，不用实测 Δq 冒充提交命令。深度、相机或记录发生异常时公开缺失原因，保留已采数据；未知支撑、夹持或关节字段维持 null/unknown，不填零猜测。当前 live recorder 不支持运行中变更频率/profile，首版继续冻结这些条件。

### 7.2 目录只增加一个父级编排与发布视图

```text
outputs/scene_datasets/<dataset>/                 # 不可变输入，不写入
outputs/no_edit/<unique-unit-run>/               # 复用现有 collector 输出
  frozen_config.json / timing.json
  scenes/val_<house>/
    scene/ / scene_dataset_input/
    tasks/<task>/
      stations.json / station_input.json / trials.json
      station_statistics.json / selection.json / result.json
    attempts/<operation>/<station>/<attempt>/
      raw/ / canonical/ / trajectory.jsonl / planning.json / videos/
  canonical_index.jsonl / dataset_index.jsonl / datasets/
outputs/lastmile_datasets/<parent-run>/           # 新：小型关系和聚合视图
  run.json / progress.json / units.jsonl          # 执行中可更新
  calibration/<sample>/<config>/rates.json
  selections/<sample>/<config>.json
  audits/<attempt>.json
  lastmile_index.jsonl                           # 全部连续 episode，包括失败
  all_attempts_index.jsonl                       # 标定 + 连续；引用子 run 索引
  train_index.jsonl                             # 成功且审计通过的连续 episode
  manifest.json / checksums.json / summary.json  # 最终发布时封存
outputs/derived_packages/<new-export>/            # adapter 派生，不写回原包
outputs/logs/                                    # 全部运行日志的唯一入口
```

父 run 不再复制每帧数组；引用唯一源 attempt 及其摘要。已有 `export_rollout` 可以继续生成兼容、自包含的成功 bundle，但它不是全结果数据的唯一来源，也不要求所有失败都重复复制进多个 bundle。

`lastmile_index` 至少记录源 dataset/sample/config/task/model、episode/attempt、S0/S1、率表/selection 摘要、phase 引用、path/navigation/manipulation 结果、源 canonical 摘要、审计与训练资格。相对路径按父 manifest 明确解析基准；依赖位于子 run 时记录可解析 locator 和内容摘要，不把绝对本机路径当成永久身份。跨机器打包可以复制并检查源包字节，不改写其内部摘要。

原始索引、闭环索引和成功训练视图不能互相替代：**失败 raw 有价值，但不等于成功动作训练样本。** sealed 仅代表封存，不自动代表 canonical 审计通过。

### 7.3 恢复与发布

- 冻结输入身份、代码摘要、参数和采样点集完全一致才可 resume；否则新 run。
- 完成的有效 trial 不再执行；异常/中断只补未完成 trial，新 attempt 保留旧记录。
- 标定和连续采集的阶段边界独立登记；`calibration_only` 完成不能设置 `dataset_eligible=true`。
- 原始 attempt 封存后不追加后验标签；审计、选点或映射补充写父 run 或独立补充包。
- 发布前审计成功候选并重建 train_index；源记录异常、未知结果或审计失败不进入该视图。
- 父 manifest 封存后不更新 progress/index/checksums；后续派生写新包。执行不完整的父 run 保留进度而不冒充完整发布。

## 8. MolmoBot 利用路径：adapter 已有，动作转换有明确边界

现有 adapter 版本为 `molmobot-rby1-multitask/1.0.0`。它会先 `audit_canonical`，再读取真实帧、状态、提交命令和三路 raw 图像。输出含 `data.npz / samples.jsonl / images / manifest / checksums`。

| 原始数据条件 | 现有 adapter 能做什么 | 能否宣称完整动作监督 |
|---|---|---|
| position，未提供 motor 标定映射 | `--state-images-only`：22D 状态、head fisheye 与腕部图像 | 否；可作状态/图像派生与后续适配输入 |
| position，提供显式夹爪映射及标定来源 | `--gripper-map`：native 格式动作、chunk/mask | 可以形成带转换声明的动作监督包；不是控制器等价或原生实测 motor 的证明 |
| 真正 native 采集 | 直接导出 native 动作、chunk/mask | 按原生信号导出；首版不要求改造成此路径 |

adapter 的动作监督来自窗口末次提交的绝对目标，相对窗口初始实测状态转换；不能改成把 measured motion 当作命令。现有 100 ms 实际边界窗口、16 步 chunk 与末尾/安全范围 mask 直接复用，不把缺帧或部分执行补成完整有效样本。

**有完整 raw 也不能唯一反推出另一种控制器本来会提交什么 motor command。** 因此首版先保证原始数据封存且能完成 state/images 导出；需要动作训练时，在独立 adapter 配置中提供经过明确标定的夹爪映射，并验证动作导出与有效 mask。缺映射时标 `action_supervision_unavailable`，不能声称“可直接 SFT”。模型训练能否读取 adapter 包、相机物理 rig 是否匹配 checkpoint、训练效果好坏，都需要后续独立验证。

真实记录的 language_instruction 不丢弃、不猜测翻译。Open 另需使用者提供首帧原始 head 图上的归一化 conditioning point；现有 adapter 会与图像执行同一 warp。缺少提示点时可以先完成原始采集，派生状态单独标记待补，不能从场景真值静默制造提示。

已有命令（现在可调用，但需要真实、完整且审计通过的输入）：

```bash
# 从 lastmile-datasets worktree 根执行；PY 指向显式准备的项目依赖环境。
export DATAFLOW_PYTHON="$PY"
bin/lastmile-dataflow audit-canonical <continuous-attempt>

# 首版 position 的无动作派生：不修改源 episode。
bin/lastmile-dataflow export-molmobot \
  --attempt <continuous-attempt> \
  --output outputs/derived_packages/<new-state-image-export> \
  --state-images-only --seed 42

# 后续提供标定映射后，生成完整格式动作监督；不是原生控制器等价重放。
bin/lastmile-dataflow export-molmobot \
  --attempt <continuous-attempt> \
  --output outputs/derived_packages/<new-action-export> \
  --gripper-map <calibrated-map.json> --seed 42
```

对 Open，在以上相应导出命令补 `--conditioning-point X Y`。同一条原始 episode 可以多次导出不同版本；每次用新目录并绑定源摘要、adapter 参数、图像变换和标定映射，不重跑机器人、不改原始证据。

## 9. 与 LastMileBench 共用什么，不强行共用什么

共享场景输入校验、实测率表、低/高邻近选择和原始证据。数据侧复用 A* 并要求一条真实连续成功轨迹；benchmark 侧从 S0 起点让策略自由完成任务，S1/高率邻点仅是构造证据，不提供给策略，不要求策略访问 S1。

**轨迹 adapter 兼容性与 benchmark 标定兼容性是两件事。** 当前 [LastMileBench build](../../../src/lastmile_dataflow/workflows/lastmilebench.py) 会派生 native 模型，并强校验率表的 `robot_sensor_config_id/compiled_model_sha256`。首版 position 率表不能只换个字段就作为它的同配置正式标定。

最简落地顺序：

1. 本次首先交付 position cuRobo 的完整采集和公共率表/选择证据。
2. 公共 schema 与选择代码直接复用；与目标 benchmark **绑定完全匹配**的标定可以直接导入。
3. 对目前 native benchmark，显式报告 `calibration_config_mismatch`，保留公共证据供后续接入；不影响原始 LastMile 数据采集。
4. 后续若要求同配置正式 native benchmark，需单独完成匹配 native 配置的 cuRobo 执行/标定，或另行明确跨配置参考标定协议。首版不擅自放宽现有强校验、不把 position 证据冒充 native 证据。

因此“通用”指共用代码和证据契约，不是不同机器人控制配置的率表可以无条件互换。当前 benchmark 的 smoke/正式标定区分继续保留，不把 smoke 改名为正式评测集。

## 10. 最小代码改动与单入口

### 10.1 建议修改范围

| 文件 | 最小改动 |
|---|---|
| 新 `workflows/lastmile_datasets.py` | 遍历封存包，生成独立子 run，调用已有 `collect_batch`，聚合率表、关系、审计和索引 |
| `stations/no_edit_config.py` | 增加显式 LastMile 选点模式及 low/high/nearby、calibration_only 等参数；旧默认行为不变 |
| `workflows/no_edit.py` | 新模式下：标定落盘后检查有效试验数、调用公共选择器、不用整体 discard_below 淘汰；calibration_only 不执行连续段；连续结果可恢复 |
| `runtime/no_edit_execution.py` | 必要时补导航完成时的 XY/yaw 复核与阶段边界元数据，不修改规划算法/动作协议 |
| `benchmark/episodes.py` | 尽量不改；率表入口检查完整性，调用现有 `select_starts` 后确定实际 S1 候选 |
| `exporting/canonical_index.py` 或新编排文件 | 复用子 run 索引，聚合父 run；审计通过后才发布新 train_index，不改旧索引历史语义 |
| `cli.py`、新 `configs/lastmile_datasets/`、相关 tests | 一个薄命令入口、冻结配置与集成/故障回归 |

raw/canonical recorder、MolmoBot adapter 和 cuRobo planner 原则上不改。如果实际回归发现记录遗漏，仅对真实缺口增量补齐；不另造 recorder 或 adapter。所有计划中的字段和入口都需实现后才能使用。

### 10.2 建议接口：一个命令即可完成首版

以下是**待实现的接口示意，不是当前可运行命令**：

```bash
bin/lastmile-dataflow collect-lastmile-datasets \
  --dataset <sealed-scene-datasets-root> \
  --assets-dir <readonly-assets-root> \
  --config configs/lastmile_datasets/v0.json \
  --output-dir outputs --run-id <new-parent-run> \
  --gpu-ids <available-devices> --workers 1

# 同一冻结输入/配置/代码下只补未完成单元。
# 同一命令增加 --resume；只做标定则增加 --calibration-only。
```

配置默认值：逐点 5 次、low=0.2、high=0.8、nearby=1.0 m、最多 3 个 S0、至少 1 条成功闭环、连续操作最多 1 次外层重试、raw-first/depth 开启。planner 的 `max_attempts=5` 与逐点 5 次 trial 是不同层级，不混称。

执行预算沿用现有 cuRobo 默认值并显式冻结，例如 attempt 墙钟 300 s、navigation 最大 2400 步、Pick 最大规划查询 12、Open 最大查询 128；这些不是 MolmoBot benchmark 的 400 policy 动作预算。场景单位墙钟沿用 86400 s，磁盘最低空闲检查沿用 50 GB；运行前可以在新配置中调整，不能在 run 中悄悄变更。

首版 `workers=1`，样本串行跑通即可。GPU 选择、CUDA/EGL 绑定、子进程隔离、日志与磁盘检查直接沿用现有 collector。以后只增加样本级并行，不新写调度服务、不硬编码 GPU 号。采集不加载 MolmoBot checkpoint，adapter 导出也不要求在线模型推理。

运行前按点数核算预算：一个包有 M 个 selected 点时，标定计划为 **5M 次**独立试验；若 M=20，就是 100 次。这是计划量，不是本次实测量。300 s/attempt 下，仅标定的名义墙钟上界已达 30000 s（约 8.3 h），还未包含初始化、记录/收尾和连续段；因此先验收单包，再开批次。50 GB 只是沿用的最低空闲阈值，不是完整数据量估计。无损 RGB/depth 会增加存储，首轮应记录实际每 episode 大小与生成耗时，再估算批次容量；空间不足停止并保留证据，不静默关闭 raw/depth。

## 11. 实施顺序与验收

### 11.1 实施顺序

1. **单包接入**：从一个真实封存 Pick 包读取 robot/task/stations，确认不重新采样、不修改源状态。
2. **标定与选择**：复用逐点执行，保存公共率表；补新模式的低/高规则与有效试验门槛。
3. **真实连续段**：复用 A*、`run_raw_attempt` 与记录器；补充到站复核和 phase 引用。
4. **发布与 adapter**：审计成功连续段，生成全结果/闭环/训练视图，调用已有 state/images adapter。
5. **多包编排与恢复**：遍历 source 清单，保证相同 house 多变体不覆盖，补父 run 汇总。

没有合格 S0/S1 或闭环未成功时，仍交付原始数据与失败报告，不降低阈值凑成功。后续更换阈值可以建立新选择/发布版本并引用原标定，不改写已封存标定试验。

### 11.2 必须补的回归

- 同 house 多 sample/config 独立输出；partial dataset 只处理有效子包并保留缺额。
- station selected 集合、初始化姿态、模型/任务/profile 摘要错配均拒绝。
- 计划 5 次但包含 errors 或缺数时，不能当作完整正式选点；重复 trial 不增分母。
- 整体率低但有合格局部低/高组合时，不被旧 discard_below 挡住。
- 无组合、无路径、导航超差、操作失败、超时、记录异常各自有明确状态。
- calibration_only 不启动连续段、不把标定记录当作成功 LastMile 数据。
- 到站不 restore；非机器人 qpos/qvel 在初始化之外不被人为重置。
- 成功但 canonical 审计失败不进入 train_index；全结果索引仍保留证据。
- resume 不覆盖旧 attempt，不重复完整 trial/成功连续段，输入变化拒绝 resume。
- position 无映射可导 state/images，但动作导出拒绝；独立派生前后源摘要不变。
- native benchmark 与 position 标定不匹配时明确拒绝，不篡改率表配置身份。

### 11.3 真实验收分为三个结论

| 验收 | 最小证据 | 能说明什么 |
|---|---|---|
| 管线执行完整 | 一个真实 Pick 包的 selected 点完成标定，选择/排除原因齐全，所有结果分类可追溯 | 编排运行完整；不保证产生成功样本 |
| LastMile 成功数据 | 至少一条真实 S0→S1→Pick 成功连续 episode，navigation/manipulation 成功，canonical 审计通过 | 成功恢复数据链路成立 |
| MolmoBot 数据可利用 | 同条 episode 的 state/images 导出可读取、mask/摘要完整；若声明动作训练，再提供标定映射、动作/chunk/mask 验收 | 派生数据格式成立；不证明训练性能或 native 等价重放 |

首次真实验收不需要启动 MolmoBot 推理。Open 和完整动作训练导出另列结果，缺提示点/夹爪标定时明确待补。全批统计还需报告 houses、目标、Case、sample/config、selected 点、有效 trials、组合、连续尝试、成功数、审计通过数和导出数；同 house 多变体不是跨 house 泛化。

## 12. 本次方案核对结果与剩余限制

- 代码基线：lastmile-datasets 的 HEAD 为 `950fc407cb21f9d30367e8a6128fa95028c52a80`；scenes-datasets-design 的 HEAD 为 `a63a817463baa6537484e2de39d3b11b212dc740`。
- 已读当前场景包接口、collector、cuRobo 连续执行、统计、benchmark 选择、raw/canonical recorder 与 MolmoBot adapter；核对四份指定数据文档一致。
- 已执行现有相关软件回归：**34 项，33 通过，1 跳过，0 失败**。跳过的是可选本地上游 fisheye 对照；涉及合成模型、mock 传感器/部分流程，不是 CUDA rollout 或真实 MolmoBot 推理验收。
- 回归日志：validation/related-tests.log。命令为在本 worktree 根执行 `PYTHONPATH=src:tests MPLCONFIGDIR=/tmp/lastmile-plan-mpl ../no-edit-lastmile-val/.venv-curobo-v080/bin/python -m unittest test_canonical_data test_lastmilebench test_native_station_input -v`。
- 文档检查：23 个本地引用均可解析，代码块闭合，SVG XML 检查与栅格预览检查通过；图源为 figures/pipeline.py。Markdown 已转为临时 HTML 并嵌入 SVG，但 Chromium 的 snap 启动缺少必要权限，未完成浏览器内嵌预览，不将其描述为通过。
- 本次只新增方案和解释图，没有实现上述新入口，没有启动标定、导航采集、数据派生或模型训练。

**交付判断：现成执行、记录与 adapter 代码足够作为首版基础；下一步应做薄编排和少量资格/发布修补，而不是先改造 native 控制链。完整动作训练的夹爪标定与正式 native benchmark 的匹配标定保持独立边界。**
