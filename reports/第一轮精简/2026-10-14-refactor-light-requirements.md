# LastMileDataFlow 轻量化重构需求

**目标：只保留“场景增强 → 逐点成败图 → LastMile 数据 / 最小困难 S0 评测”主链路，做一个人类用户容易安装、复现和修改的科研仓库。先删除无关功能与重协议，再整理代码；不恢复历史五阶段，不建设发布、审计或来源追踪系统。**

- 日期：2026-10-14。
- 工作树：`LastMileDataFlow/.worktrees/refactor-light`；分支：`refactor/light-all`。
- 梳理基线：Git `2903830`。本次开始时工作树干净。
- 本次交付：需求文档。下述目录、文件格式和新命令是**重构目标，不是已经实现的接口**。

## 1. 本次怎么梳理，得到了什么

阅读了仓库约定、README、现有数据协议、核心 workflow、执行器、记录器、规划与策略适配器、环境脚本及测试目录。通过源码导入和函数调用核对入口，没有运行 GPU、模型或真实采集。

当前源码有 **137 个 Python 文件、19,495 行、19 个顶层领域目录、28 个 CLI 子命令**，另有 43 个 `test_*.py` 文件。这些是静态规模统计，不是测试数量或轻量化完成指标。

结论：核心计算已经分布在多个独立入口中，但输入读取、快照加载、采集记录和数据导出仍与多层 `manifest / checksums / audit / seal` 耦合。**不能只删除审计 CLI，也不能按目录名整块删除旧底座。** 应从核心入口保留实际需要的算法与执行逻辑，先解除协议耦合，再删除失去调用者的代码。

## 2. 只围绕这一条主链路

以下为目标流程的解释图；不表示现有代码已经端到端跑通。

```text
原始场景、物体资产、机器人资产
              │
              ▼
场景数据增强：任务选择 + 采样点采样 + 障碍物增强
              │
              ▼
scene-datasets：任务与编辑后的场景库、采样点
              │
              ▼
逐点独立 rollout：cuRobo / MolmoBot 等 VLA
              │
              ▼
各采样点的成功 / 失败记录与成功率热力图
              │
              ├──────────────────────┐
              ▼                      ▼
lastmile-datasets             lastmile-bench（最简）
S0 → S1 → 操作                从困难 S0 开始评测
              │                      ▲
              ▼                      │
未来模型利用 / 训练 ──────────────────┘
（本次不实现）
```

“全局图”指一个场景任务内整个采样区域的逐点结果图；批次可以包含多场景、多任务。不把不同目标、策略、机器人配置的成功率混成一张无条件地图。

### 2.1 本次必须保留

| 能力 | 最小职责 | 本次边界 |
|---|---|---|
| 场景增强 | 选定任务，生成采样点，加入程序化障碍物，保存实际编辑结果 | 无编辑场景可作为同一入口的基线；不需要 Agent 审阅链 |
| scene-datasets | 保存任务、可重载场景和采样点，供两类下游共用 | 场景不由策略成功与否决定是否存在 |
| 逐点 rollout | 每点多次独立初始化、真实执行、记录成功与失败 | 支持 cuRobo 与 MolmoBot；不把初始化通过当任务成功 |
| 成败图 | 从逐次结果汇总每点成功率和有效次数，画热力图 | 未测试、异常和无效点单列；平滑只用于显示 |
| lastmile-datasets | 选择困难 S0 与邻近可操作 S1，执行连续导航与操作，保存轨迹 | 首版沿用 cuRobo 操作 + A*；不要求本次补齐所有 VLA 连续采集 |
| lastmile-bench | 固定困难 S0、任务、种子和预算，执行策略，输出任务成功率 | 先做单策略串行评测；不做排行榜、排名服务或复杂评测框架 |

技术边界：规划用 **cuRobo**，当前策略用 **MolmoBot**，导航用 **A***。PI 等策略、短程导航只作为后续替换点，不先写插件系统、空实现或抽象类层级。模型训练和模型利用目前没有，本次不实现。

### 2.2 数据语义不能因为删协议而变弱

- S0：特定任务和执行配置下，实测低成功率的采样点；不是“离目标远”或“有障碍物”的同义词。
- S1：同一配置下实测高成功率、且位于 S0 邻域的采样点。邻近不证明 A* 有路径，更不证明真实导航成功。
- 每次逐点试验创建独立仿真。连续 episode 从 S0 开始，导航到 S1 后从**实际到达状态**执行操作，不拼接独立标定轨迹，不在执行中传送或恢复快照。
- 保留机器人动作、夹爪模式、状态维度、相机、控制频率与物理步长的实际语义。position 夹爪与 MolmoBot native motor 不可只改标签互换。
- pick / open 的成功仍由统一任务判定器给出。动作执行结束、规划成功、文件写完均不代表任务成功。
- 同 house 多目标或多增强变体仍来自同一源场景；若做训练/评测拆分，按源 house 分组，不按 rollout 随机拆分。

默认沿用当前选择参数：每点 5 次，S0 成功率 ≤0.2，S1 ≥0.8，邻域距离 ≤1.0 m。全部留在配置中，不散落硬编码。它们是有限样本下的实测筛选规则，不是已证明的困难程度。

## 3. 现有入口的真实调用关系

这里只列与删改有关的静态调用关系；“存在调用”不等于已完成真实运行验证。

| 入口 | 核心调用 | 当前耦合 / 重构方向 |
|---|---|---|
| `discover/qualify/build-scene-datasets` | `workflows/scene_datasets.py` → `obstacle_augmentation.py` → 任务发现、障碍物采样、站位初始化、仿真 | 合并发现/资格检查的重复路径；移除逐级封存与最终全量物理重放审计 |
| `augment-obstacles` | `workflows/obstacle_augmentation.py` → `construction/obstacle_*`、`runtime/obstacle_edit_session.py`、几何/支撑检查 | 算法并入场景增强，不再保留第二套公开数据协议和发布入口 |
| `collect-no-edit` | `workflows/no_edit.py::collect_task` → `run_raw_attempt` / `run_vla_attempt` → 统计、热图、A* | 是逐点执行与连续采集的实际复用底座；去掉 val-only、旧 augmented 包和多套布局分支 |
| `collect-lastmile-datasets` | `workflows/lastmile_datasets.py` → `scene_package.package_manifest` → `collect_batch` → `collect_views` | 当前要求封存包，并再次 canonical audit、生成多个索引；改为直接消费最小场景/rollout记录 |
| `build/evaluate-lastmilebench` | `workflows/lastmilebench.py` → `benchmark/episodes.py`、`native_scene`、`run_vla_attempt` | 保留起点选择和真实执行；删除 benchmark seal、checksum、哈希身份及层层绑定文件 |

两条下游共用站位统计与 S0/S1 选择。当前 `benchmark/episodes.py::select_starts` 只接受 cuRobo 标定；支持 VLA 来源需改为显式配置匹配，不能声称现有选择器已经支持。

当前 `collect_task` 的连续采集分支面向 cuRobo，MolmoBot 分支主要做站位评测。`run_vla_attempt` 虽有 `path/goal` 参数，也不等于公开 workflow 已交付 VLA 连续 S0→S1 数据。

### 3.1 直接删除的功能范围

后续实施时删除入口、专用代码、专用配置和只证明这些功能的测试，而不是仅把它们藏起来。

- 历史五阶段编排、阶段编号配置、旧示例和阶段交付说明。
- 旧 `run / build / station-map` 工作流；`case-edit` 四 Agent 构造、reviewer、vision-review、人工批准、feedback 链。
- 旧场景检索 CLI、静态发布索引、交付浏览页及 `render-delivery / export-stations`。
- `import-legacy`、旧格式 fixture、仅服务历史兼容的 adapter 与 fallback。
- `audit*`、`review-lastmile-timeouts`、`index-canonical`，及审计重放、独立复核 publication、封存 registry、来源追踪索引。
- 空占位目录，如仅有说明而没有本次功能的 `demonstrations/`、`scheduling/`。

历史五阶段代码及配套文档可删除，不再迁移成第二套 archive。历史结论不改写成“已成功”；已有真实采集数据、原始资产和非五阶段实验结果不因清理代码而删除。本次仅写文档，尚未执行任何删除。

### 3.2 先剥离冗余部分，不能整文件直接删

| 当前位置 | 应删除 / 简化 | 必须保留的实质 |
|---|---|---|
| `runtime/simulation.py`、`stations/scene_package.py` | 快照/包的 checksum、哈希身份、seal 状态和递归绑定 | 仿真创建、场景与初态加载、任务和采样点读取 |
| `recording/recorder.py`、`canonical.py`、`protocol.py` | 双套 raw/canonical 写入、全文件哈希、来源清单、发布封存 | 实际动作/状态/时间戳、必要图像、记录失败状态、资源关闭 |
| `validation/` | 独立 publication 审计、重复后验 validator、相同任务的多套判定 | 初始化有效性、真实任务判定、动作合法性、仿真异常识别 |
| `construction/`、`catalog/` | 旧 DSL、Agent 合约、registry 身份协议、容量穷举证书 | 障碍物放置、资产查找、碰撞/支撑、静置、任务目标映射 |
| `planning/curobo.py`、`curobo_v2.py` | 旧规划 API 路线与源码逐文件核验 | 当前 cuRobo v0.8.0 路线，以及 v2 仍借用的位姿/数学工具 |
| `integrations/`、`exporting/` | 强制派生包、多成功/失败快捷目录、重复训练索引 | 需要时可调用的薄模型转换、热图与导航图 |
| GPU 调度和服务器脚本 | 持续负载采样、队列调度、自动择卡默认、服务器搬运脚本 | 用户指定设备、串行执行、清楚的错误与进程清理 |

资产名称到文件的查找表、物体实例映射不是要删除的“registry”；它们有真实计算用途。规划约束、FOV 约束也不是仅凭 validator 名字就删除。保留与否看核心调用和实验作用。

障碍物放置保留有界重试和有效性检查。放不下就减少尝试并报告未生成，**不通过删除容量证书放行不符合配方的样本**；如需允许少放，明确更改配方。默认不再做穷举“无法追加”的认证及二次重放。

## 4. 每阶段只保留一个主 JSONL

配置与大数组不是主记录的重复索引。JSONL 存任务关系与结果，NPZ 存轨迹/状态，图像存实际观测；不为“只留 JSON”把大数组嵌进 JSON。

### 4.1 建议的最小运行目录

```text
outputs/<run>/
├── config.json                 实际生效配置 + seed + git_commit（可加 dirty）
├── scenes.jsonl                场景阶段主记录；运行该阶段才生成
├── trials.jsonl                逐点 rollout 主记录
├── episodes.jsonl              S0→S1→操作主记录
├── scene-files/<scene-id>/      编辑后场景、初态等必要载荷
├── rollouts/<rollout-id>/       trajectory.npz + 按需 images/ 或 video.mp4
└── figures/                    按需生成热图；不是下游资格依据

benchmarks/<name>/               默认位于 outputs 下
├── config.json                 构建配置、标定来源、seed、git_commit
└── episodes.jsonl              困难 S0 评测定义
outputs/<eval-run>/
├── config.json
├── results.jsonl                评测阶段主记录
└── rollouts/                    按需保留评测轨迹 / 视频
```

这是一个多阶段合用 run 的示例，不要求每次实验创建所有文件或所有目录。分阶段运行时只引用上游数据位置，不复制整套输入，也不沿用旧 `outputs/no_edit` 子 run + publication + derived packages 的三层关系。

每个主文件有唯一写入者；默认串行。未来确需多 worker 时集中写入主文件，不先做事务 registry。可选终端汇总或 `summary.json` 只由主记录生成，缺失不影响读取，不成为额外权威数据源。

### 4.2 主记录中只存不可推导的信息

| 主文件 | 最少信息 | 不再重复保存 |
|---|---|---|
| `scenes.jsonl` | 简单 `scene_id`、源 house/split、任务与精确目标、编辑后场景/状态位置、实际障碍物 placements、采样点及可重建初态、构造结果 | dataset/sample/config 多层哈希身份、每层 task/objects/environment 副本、候选/validated/selected 多份同点集合 |
| `trials.jsonl` | `rollout_id`、`scene_id`、`station_id`、重复编号、试验 seed、任务结果、执行终止原因、必要错误、记录是否完整 | 逐行配置/机器人/模型摘要、成功率和计数副本、额外 all-attempts/train/canonical 索引 |
| `episodes.jsonl` | `rollout_id`、`scene_id`、S0/S1 引用、seed、导航结果、任务结果、记录是否完整、导航/操作分段边界 | 完整 S0 失败列表和视频复制、多个 selection/train/group 索引、独立 audit 结果 |
| benchmark `episodes.jsonl` | `episode_id`、场景和困难 S0 引用、seed；S1 如有需要仅作可恢复性参考 | 每 episode 相同的策略/相机/预算配置、多级 benchmark/source/model 哈希绑定 |
| `results.jsonl` | `episode_id`、任务结果、执行终止原因、必要错误、执行步数/耗时 | 同一结果的 status/outcome/result 多份文件、发布状态 |

约定：同一 run 的后端、权重版本、机器人 profile、成功判据、相机、控制频率和数据根目录只在 `config.json` 记录一次。不同配置生成不同 run，统计时不混用。

ID 用局部稳定名称或递增编号即可，不用内容哈希；主记录中仅保存定位必要载荷所需的 ID。已按 `rollouts/<rollout-id>/trajectory.npz` 约定的路径不逐行重复写；非约定路径才显式存储。

采样点不仅是 XY。需要保存 yaw 与完整机器人初始化信息；共同关节初态放配置中，站位只存差异；不能推导的差异才存独立状态。世界坐标、米/弧度、四元数顺序与轨迹时间单位在一个简短数据说明中定义。

### 4.3 轨迹与场景只保留一份真实载荷

- `trajectory.npz` 保存实际执行动作、必要机器人/物体状态、仿真时间戳。导航/操作边界在 episode 主记录里引用；轨迹全程连续。
- 需要 VLA 数据时记录实际输入图像及对应时间/帧号；训练用途需要的观测不能只保留有损演示视频。depth、额外相机、第三人称、逐物理 tick 完整 trace 均按研究需求开启，不默认全量保存。
- 不同时强制写 raw、canonical、replay 和多套 model dataset。同一实际数据只存一次；模型格式转换按需运行，不阻塞核心采集。
- 场景必须可从已保存的编辑结果与初态重载。优先使用可重建的 MJCF/配置 + 必要状态数组；仅存随机种子不够。MuJoCo `.mjb` 可作同版本加速缓存，不能是唯一跨环境分发依据。
- 外部资产仍是下载前置，不默认复制全部资产到每个样本。场景 loader 根据数据根目录解析资产，重载不依赖原服务器路径。
- position→native motor 转换无标定时，不伪造动作监督。需要该模型格式时再处理，不能为了“轻量”宣称二者等价。

### 4.4 重现信息就到这里为止

每次实验保存 **实际配置 + seed + Git commit**。权重和数据版本是配置的一部分；推荐一个 `dirty` 布尔值，明确未提交修改不由 commit 完整描述。

不保存整树源码哈希、逐资产 checksum、递归 provenance、source manifest、审计 companion 或封存证书。版本固定属于安装配置，不是反复运行的源码审计。科研复现指能按同配置再运行，不承诺 GPU 数值和随机执行逐位一致。

默认新建 run，不覆盖旧数据。首版不要求复杂 resume；若保留恢复，只跳过主记录中已完整结束的 trial，不扫描并复核一整套旧封存文件，不自动修复历史 publication。

## 5. 成功率和困难评测必须说清分母

### 5.1 成败图以真实试验为依据

- 每点成功率 = 成功次数 / 有效任务试验次数，同时展示有效次数和计划次数。
- 规划无解、已正常进入试验后的任务失败、正常任务预算耗尽是该执行后端的有效负结果；初始化、仿真数值、记录设施、模型服务异常及未测试单列，不伪装成操作失败。
- 任务结果与终止原因分开：超时收尾时若已有完整成功证据，沿用同一任务判定器给成功，同时记录超时终止；没有证据则不升级成功。
- 无有效试验的点用“未测试/异常”标记，不填 0 成功率。重复编号去重，同一 trial 不因恢复被重复计数。
- 用逐点实测计数选 S0/S1；平滑热图不参与选择。首版仅从达到配置要求有效次数的点中正式选取，不把缺样点判成困难点。
- cuRobo / VLA、不同权重、控制模式或相机 profile 分别统计。显示比较可以共享坐标，但不能共享成功标签。

### 5.2 lastmile-datasets 与最小 bench

连续数据只把“真实导航成功 + 任务成功 + 必要记录完整”的 episode 用于成功示教；失败与异常仍保留在同一个主记录中，读取时过滤，不另建 train_index。至少一条成功解即可形成最小可用样本；不强制多终点、多 S0 或多示教数量。

benchmark 从配置明确的标定后端选困难 S0。若用 cuRobo 标定来评 MolmoBot，写清“cuRobo 定义的困难”，不能说它已是 MolmoBot 困难点。更换控制/传感器 profile 时重新初始化检查并重新标定，不复用不兼容的率表。

评测从 S0 开始，由被测方法产生动作；不把参考 S1 作为默认 oracle 自动送给被测策略。首版只要求任务成功率、有效 episode 数、异常数和覆盖率；路径长度、效率及可恢复性证明后续按研究问题增加。

完整评测的任务成功率以预定 episode 集为分母；有异常/未运行时标记未完成，可展示已完成有效子集的观察成功率，但不当成全量结果。没有合格 S0 或没有成功恢复解是允许保存的负结果，不靠减少分母或丢失败获得“成功交付”。

## 6. 目录清楚，但不按每个名词建包

建议保留现有包名，用六个实质领域目录；具体文件名可随真实调用关系调整。

```text
README.md / pyproject.toml
configs/                       少量增强、cuRobo、MolmoBot、bench 示例
docs/                          安装与数据准备、数据格式（不再多层 datas/archive）
reports/                       必要实验结果与本需求；不作运行输入
scripts/                       必要安装/下载脚本，无服务器专用审计队列
src/lastmile_dataflow/
├── cli.py / config.py / io.py  薄入口、配置、读写与路径解析
├── scenes/                    来源、任务、几何、增强、站位采样
├── runtime/                   仿真、机器人动作适配、连续执行、任务判定
├── planning/                  cuRobo + A*；共用位姿工具与当前约束
├── policies/                  MolmoBot；后续实际接入 PI 时再扩展
├── data/                      单套轨迹记录、按需模型转换、热图
└── workflows/                 场景生成、逐点评测、连续采集、bench 编排
tests/                         核心逻辑回归 + 一条明确的真实 smoke 路径
data/                          用户下载的数据与模型，不入 Git
outputs/                       用户实验结果，不入 Git
```

不额外建立 manager/service/repository/factory/registry 层。只有产生真实独立职责、被多个调用者使用时才拆文件；不为达到行数指标制造十几个小工具文件。

- `cli.py` 只解析参数、加载配置、调用 workflow，不放算法。
- 统一场景读取、站位初始化、rollout、任务判定和记录函数，删除 cuRobo/VLA 之间与后端无关的复制逻辑；后端动作语义不同的部分保留显式适配。
- 长函数按“准备 → 执行 → 判定 → 写入”拆成可读步骤，不顺手抽象成全阶段巨型 runner。
- 删除大段过时阶段注释与交付宣言；保留解释单位、坐标、控制和物理约束的短注释。
- 对外入口建议只围绕 `build-scenes / evaluate-stations / collect-lastmile / build-bench / evaluate-bench`，绘图作为按需操作。名称在实施时统一；不长期保留一套旧命令别名。

## 7. 环境不借相邻工程，数据位置不绑定服务器

“环境不依赖外部”按**不依赖服务器上碰巧已有的环境和兄弟仓库**理解；仍需要公开第三方库、GPU 驱动、数据资产及策略权重，不是把这些全部重写或放进 Git。

### 7.1 安装要求

- 在仓库内创建独立 `.venv`；若 cuRobo / MolmoBot 依赖冲突，可使用独立且由本仓库说明创建的后端环境，不借相邻 `.venv`，不写 `borrowed_dependencies.pth`。
- cuRobo 当前路线固定到代码实际支持的 v0.8.0/tag 或 commit；删去八个源码文件的哈希核验，不等于删版本约束。安装脚本不能要求一台已经配好 Torch/CUDA 的服务器作为前提。
- MolmoBot 必要上游代码通过明确依赖安装或文档化下载到用户指定位置；不默认指向相邻 `MolmoBot/.worktrees/...`，不把旧 `molmospaces` 仿真工程作为执行依赖。
- 核心依赖和后端可选依赖分开；仅为旧协议存在的 `jsonschema`、历史 `phase3` extra 等，在无核心调用后删除。保留真正用到的绘图/图像依赖。
- GPU 显式指定，默认单卡单 worker；用户改 GPU 参数即可运行，不写固定卡号/8 卡集合。不存在后端时清楚报错，不自动回退另一算法。
- 给出一条从干净环境到最小场景实验的完整安装路径。仅环境变量模板、启动成功或 import 成功不算该实验已复现。

### 7.2 路径要求

- 所有配置内相对路径按**配置文件所在目录**解析；CLI 显式相对路径按调用工作目录解析。规则只实现一次。
- 下载数据位置统一通过 `data_root` 配置或 CLI 覆盖；权重可有独立根目录。默认示例使用仓库内 `data/`，允许用户放到任何磁盘。
- 输出主记录的相对路径按所属 run/数据集根目录解析；跨 run 输入只在配置里引用一次。持久化资产引用用数据根目录下的相对路径或资产 ID。
- 去掉 `/home/wenyifan`、`/data0`、`/nas`、相邻 worktree 层级和必须成立的服务器 symlink。内部调用可解析成绝对路径，但不把它作为输出格式的唯一引用。
- 搬动仓库和输出目录后，只需更改数据根目录/上游位置，不能要求重新封存、补 checksum companion 或复制原服务器目录树。

### 7.3 前置下载说明要覆盖哪些输入

后续在一个 `docs/setup.md` 中给出下表每项的上游来源、获取命令或手动步骤、版本/访问条件、解压位置和最小运行命令。**本次未联网核验下载来源、授权或文件可用性；以下是待补齐清单，不是已验证下载教程。**

| 输入 | 从当前代码确认的必要内容 | 使用范围 |
|---|---|---|
| 导出的 ProcTHOR 场景 | `<split>_<house>.xml`、对应 metadata、XML 引用的 mesh/texture/include | 场景加载；仅原始 house JSON 不是当前 loader 可直接使用的输入 |
| 物体资产 | 增强使用的 THOR 对象及几何/材质依赖 | 障碍物增强 |
| RBY-1 仿真资产 | MJCF、mesh、相机/执行器配置 | 所有真实 rollout |
| RBY-1 规划资产 | URDF/meshes、`rby1m_holobase.yml`、collision spheres | cuRobo |
| 抓取候选 | 目标的 `*_grasps_filtered.npz` 及其任务/资产映射 | cuRobo pick |
| MolmoBot 上游代码与权重 | 所用代码版本、multitask checkpoint、模型配置和 tokenizer 等必要文件 | VLA rollout 与评测 |

最小示例只需要一个场景、一个目标及所用障碍物资产，不要求先下载全量库。若上游数据需要额外转换或不能直接公开下载，明确说明生成方式或限制，不伪造一条“一键可用”命令。

## 8. 去掉审计，但留下最少正确性检查

| 保留且只在必要位置做 | 删除或不默认执行 |
|---|---|
| 配置值/维度/路径存在；目标能唯一定位；输入可加载 | 全文件哈希、嵌套 seal、重复身份绑定 |
| 放置碰撞、支撑、静置；机器人初始化有效性 | 同一构造完成后再做独立全量物理重放审计 |
| 控制动作合法、有限数值、机器人限位、资源关闭 | 同样条件分散在多层 validator 反复扫描 |
| 统一真实任务判定；异常与任务失败分开 | Agent/视觉 reviewer 二次“批准”成功标签 |
| 必要轨迹字段/帧数能读取，记录失败不当完整示教 | 全视频解码、多镜头媒体验收、审计通过才发布 |
| 必要输入匹配控制/相机配置，防止错用率表 | 以递归 provenance 和 checksum 证明配置匹配 |

简化之后不保留独立 `validation/` 系统：任务判定归执行模块，场景有效性归场景构造，数据可读性归记录/读取模块。**删除冗余验证不等于删除任务判定或降低成功阈值。**

## 9. 实施顺序：先删，再整理，最后复现

1. **从核心入口固定保留范围。** 补齐关键调用者，分清共享底座与历史专用代码。以本需求为边界，不列一个全库发布审计项目。
2. **先删无关入口和专用代码。** 删除历史五阶段、Agent 审阅、兼容入口及配套配置/测试；仅删除本工作树里的对象，保留共享底座和已有实验数据。
3. **原地缩协议。** 让场景 loader、执行器、记录器和下游读取最小主记录；去掉 checksum/seal/audit/registry/重复索引及旧格式分支。此时不先搬目录、不增加迁移抽象。
4. **删除失去调用者的实现。** 重点清理 canonical 审计、容量认证重放、timeout 复核 publication、服务器专用脚本和空目录。
5. **再调整目录与可读性。** 移动剩余模块、合并重复逻辑、拆过长函数、收敛 CLI 和配置；不复制旧实现到新位置留下两套。
6. **重建环境和用户入口。** 独立安装、前置下载说明、统一路径、单卡小样本命令；更新 README，不把历史交付报告继续作为使用入口。
7. **做一次最小真实复现并报告限制。** 核心单测后跑小规模主链路；真实负结果也可交付，成功示教则必须真的执行成功。

旧数据格式不要求向后兼容，本次不造通用迁移框架。若某份既有数据确需继续用，再做明确的一次性转换；原始结果不覆盖。`AGENTS.md` 当前仍含历史目录与审计约定，本次按只读约定未修改；实施时需要所有者同步新边界，避免旧规则重新把冗余协议带回来。

## 10. 验收面向快速实验，而不是发布级完整性

- **代码范围：** 推荐入口仅涉及主链路；旧五阶段、Agent 审阅、审计封存、历史兼容不再是源码或运行依赖。不能靠移到新目录宣称已删除。
- **数据：** 每阶段一个主 JSON/JSONL；读取成功、失败和困难 S0 不要求任何审计证书、checksum 或 registry；没有重复训练索引才可入集的门槛。
- **环境：** 在干净环境按说明安装；不读相邻工程的 Python 环境；缺数据/权重有明确准备说明。
- **可搬动：** 一个实际场景及输出复制到不同位置后，按新的 data_root/上游位置可加载；无需原服务器路径或 symlink。
- **最小主链：** 一场景一任务、少量采样点，能完成增强、逐点真实 rollout、热图及连续采集；无合格 S0/S1 时明确输出负结果。验证连续采集本身可另用一个有效配对做 smoke，但不能把它冒充自动筛得的困难样本。
- **最小 bench：** 一个由真实标定选出的困难 S0，可跑一次真实策略评测并保存结果；没有标定只能叫启动 smoke，不能称困难评测。
- **必要回归：** 仅覆盖动作语义、初始化/增强、任务判定与异常分类、成功率/选择、A*、轨迹读写及两类下游的核心关系。随删除功能删掉专用测试，不为旧审计行为续写回归。
- **真实性：** 单测、简化 fixture、import、进程启动和退出码不证明真实任务成功。文档区分已运行、未运行和失败原因。

本次没有预设“删掉多少行才合格”，也不要求全场景、多卡、多策略、多个随机种子或全量视频审计。目录更少、文件更短应来自功能收敛与去重，不以牺牲必要动作/物理逻辑换取数字。

## 11. 尚需在实施中确认的少量事项

1. **任务范围：** 默认保留主链路实际使用的 pick/open，先用 pick 做最小复现；open 的任务绑定和真实执行能力单列，不把未验证范围补成“已可用”。如要首版仅 pick，需要明确删去 open。
2. **标定来源：** 逐点图支持 cuRobo 和 VLA；初版连续数据用 cuRobo；bench 必须在配置里选定困难 S0 的标定来源和控制 profile。
3. **可公开准备的数据：** 场景导出数据、机器人/抓取资产、MolmoBot 权重的实际来源与访问条件需核实；它们可能限制他人完整复现，不靠隐藏路径解决。
4. **最小场景格式：** 要检验现有 MuJoCo 导出是否能携带全部新增物体与正确资产引用；未证明 MJCF + 状态可重载前，不先删掉唯一可用的快照载荷。

这些事项不要求另建审批/审阅系统。明确默认值与已知限制，针对关键缺口做一次必要验证即可。

## 12. 本次梳理依据

以下链接描述的是基线现状，不是要求保留的旧协议。实际重构删除旧文档后，本需求仍是自足的目标说明。

- [现有 README](../README.md)、[仓库约定](../AGENTS.md)、[CLI](../src/lastmile_dataflow/cli.py)。
- [场景协议](../docs/datas/scene-datasets-v1.md)、[场景 workflow](../src/lastmile_dataflow/workflows/scene_datasets.py)、[障碍物 workflow](../src/lastmile_dataflow/workflows/obstacle_augmentation.py)。
- [逐点与连续采集底座](../src/lastmile_dataflow/workflows/no_edit.py)、[cuRobo 执行](../src/lastmile_dataflow/runtime/no_edit_execution.py)、[MolmoBot 执行](../src/lastmile_dataflow/runtime/molmobot_execution.py)。
- [LastMile 数据协议](../docs/datas/lastmile-datasets-v0.md)、[数据 workflow](../src/lastmile_dataflow/workflows/lastmile_datasets.py)、[bench workflow](../src/lastmile_dataflow/workflows/lastmilebench.py)、[S0 选择与分母](../src/lastmile_dataflow/benchmark/episodes.py)。
- [快照](../src/lastmile_dataflow/runtime/simulation.py)、[场景包](../src/lastmile_dataflow/stations/scene_package.py)、[记录器](../src/lastmile_dataflow/recording/recorder.py)、[canonical 记录](../src/lastmile_dataflow/recording/canonical.py)。
- [cuRobo 环境脚本](../scripts/setup_curobo_v080.sh)、[依赖声明](../pyproject.toml)、[MolmoBot 配置](../configs/policies/molmobot.json)、[机器人配置](../configs/robots/rby1.json)。

**本次验证范围：** 静态读码、入口/调用核对、规模统计和本文相对链接检查。未安装新环境、未执行业务测试、未运行真实模型/仿真、未删除代码或历史资产。
