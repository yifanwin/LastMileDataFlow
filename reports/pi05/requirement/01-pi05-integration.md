# π0.5 接入 Panda-Omron 与 RBY-1 的需求

状态：active  
日期：2026-10-18  
替代：无  
关联：[本轮梳理结果](../delivery/01-requirements-review.md)、[Panda 控制对齐评估](../info/01-panda-robocasa-alignment.md)、[Panda 现有全流程需求](../../panda-omron/requirement/02-panda-omron-full-flow.md)、[benchmark 需求](../../benchmark/requirement/01-lastmile-bench-v0.1.md)

实施依据见[双本体构建方案](../plan/01-pi05-integration-build.md)，当前结果见[构建报告](../delivery/03-implementation.md)。RBY 使用 state22 / position action20，Panda 按路线 B 接入共用 controller。

## 1. 目标与范围

在 LastMileDataFlow 项目内接入 π0.5，使 Panda-Omron 和 RBY-1 都能通过项目自己的策略入口加载本地权重、获取真实观测并执行动作。π0.5 与 MolmoBot 作为平级策略后端保留，尽量复用现有工作流、记录与评测逻辑。交付按单仓库、单工作区设计；`LastMileDataFlow/.worktrees/model-train` 仅为当前开发位置。

本需求已进入构建阶段；以下保留需求形成时的接口现状与技术约定，实际实现和验收以构建报告为准。

| 本体 | 指定权重 | 参考实现 | 能力目标 |
|---|---|---|---|
| Panda-Omron | `/nas/wenyifan/models/pi05_pretrain_human300` | `reference_repos/openpi-robocasa` | 首先按 RoboCasa365 权重的本体与数据约定做 zero-shot 评测 |
| RBY-1 | `/nas/models/pi05_base` | `reference_repos/openpi` | 接入基础权重，建立明确的本体输入输出约定；后续按结果决定微调 |

Panda 的 zero-shot 指不使用当前目标任务数据更新权重；仍需要匹配权重的观测、控制和归一化。接口接通、模型产生动作、机器人发生运动和任务成功分别验收，不预设成功率。

## 2. 已明确的约束

1. **运行独立**：运行时不导入相邻参考仓库，不借用其他工程或 worktree 的环境、启动脚本、`.pth` 或 `PYTHONPATH`。参考仓库用于阅读、提取必要实现和核对语义。
2. **依赖归项目管理**：所需推理代码、依赖声明、安装与启动方式由本项目维护；可以使用正常安装的第三方库。必要移入的上游代码保留许可说明。指定 NAS 权重属于允许的外部数据输入。
3. **π0.5 尽量一体化**：共用模型加载、推理服务、tokenizer、归一化机制和动作分块处理；本体差异由明确的适配配置或模块承担。目标是一个 π0.5 核心支持两种本体，不按两个参考仓库各复制一套服务。
4. **与 MolmoBot 并列**：用户已确认“平级后端，分别运行即可”。使用配置选择策略，保持现有 MolmoBot 行为；上层工作流和结果字段能表达实际策略与本体。本次不要求两个模型同时驻留或同批并发推理。
5. **项目内独立环境**：采用项目内独立 π0.5 venv，与主仿真环境分别管理依赖，由项目启动并关闭模型子进程。建议为项目根目录的 `.venv` 与 `.venv-pi05`；解释器相对项目根目录配置，不依赖 worktree 拓扑或其他 checkout。普通单仓库工作区也应按同一说明运行。
6. **Panda 以 RoboCasa 为准**：相机、状态、动作、参考系、控制频率、夹爪及底盘行为均以该权重对应的 RoboCasa 约定对齐。进一步评估 cuRobo Panda position profile 向同一约定迁移，以适应后续可能的微调权重；是否统一切换执行控制须先评估再确认，不能预设永久保留两套控制，也不能直接视为已对齐。

这里的本体接入首先指当前 MuJoCo 项目的仿真本体；实机通信、驱动部署和实机验收尚未纳入本轮范围。

## 3. 已核对的现状

### 3.1 项目接口

- `runtime/execution_config.py` 目前只接受 `curobo` / `molmobot`；`workflows/collection.py` 按后端分派。
- `policies/molmobot.py` 已有项目内部子进程服务，可参考其生命周期处理；无需先建设通用插件平台。
- `runtime/execution/molmobot.py` 的 rollout 绑定 RBY-1 三相机、双臂监测与 20D 动作；`Simulation.step_policy()` 只接受 MolmoBot native profile。
- `workflows/lastmilebench.py` 的评测目前直接使用 MolmoBot，遇到 Panda 会记录 `unsupported_policy`。π0.5 要在 benchmark 中可选，需要修改该分派。
- Panda 现有 mobile 配置只有 `front_camera` 与 `robot0_eye_in_hand`；适配器使用绝对底盘/关节位置目标，lift 保持初始化值。

因此，本次需求包含模型推理与本体观测/控制两部分；单独新增模型类不足以完成接入。

### 3.2 Panda 权重及参考数据协议

已在指定权重目录发现实际 checkpoint：

```text
/nas/wenyifan/models/pi05_pretrain_human300/multitask_learning/75000/
├── assets/norm_stats.json
├── params/
└── train_state/
```

推理配置应指向含 `params` 与 `assets` 的 `75000` 目录；训练状态不作为推理输入。本轮仅检查目录及统计文件，未执行权重恢复。

本地参考代码 `openpi-robocasa/examples/robocasa/main.py`、`src/openpi/policies/robocasa_policy.py` 和 `src/openpi/groot_utils/groot_openpi_dataset.py` 给出的约定如下：

| 项目 | 参考实现中的约定 |
|---|---|
| 图像 | `robot0_agentview_left`、`robot0_eye_in_hand`、`robot0_agentview_right` 三路；π0.5 transform 要求右侧图像存在 |
| 输入尺寸 | 示例按比例缩放并填充到 224×224；具体图像方向仍需核对真实渲染与数据预处理 |
| 原始状态 | 16D：相对末端位置 3、相对末端旋转 4、底盘位置 3、底盘旋转 4、夹爪关节位置 2，按此顺序拼接 |
| 原始动作 | 12D：末端位置 3、末端旋转 3、夹爪关闭 1、base_motion 4、control_mode 1，按此顺序拼接 |
| 模型内部维度 | 参考配置默认 state/action 补齐至 32；checkpoint 统计文件的 state/actions mean/std 均为 32 项 |
| 分块执行 | 参考默认预测 horizon 为 50；评测示例默认每次执行前 5 步后重新观测 |

不能把 Panda 现有记录的 12D 绝对关节目标与上述 12D 策略动作等同。已补充核对官方 `convert_action` 与 Gym 映射，确认命名动作如何分派；checkpoint 对应版本的最终控制参数、参考系和实际响应仍需核对，不能仅凭维数实现映射。具体依据与差异见[控制对齐评估](../info/01-panda-robocasa-alignment.md)。

相邻 robosuite 的 `default_pandaomron.json` 使用末端 `OSC_POSE`、底盘速度和 torso 位置控制，进一步说明当前 position 适配器存在控制语义差异。但该文件本身不能证明与 checkpoint 对应 RoboCasa 版本完全一致。

### 3.3 RBY-1 基础权重

`/nas/models/pi05_base` 下存在 `params/` 与 `assets/`。本地 openpi README 将该权重列为用于微调的基础模型。所附统计目录包括 `arx`、`arx_mobile`、`droid`、`fibocom_mobile`、`franka`、`trossen`、`trossen_mobile`、`ur5e`、`ur5e_dual`，未发现 RBY-1 对应统计。

现有 RBY-1 20D 动作为：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`；各段增量/绝对目标含义以现有控制 profile 为准。

建议以现有 RBY-1 控制和记录接口为适配起点，但 π0.5 的状态字段、动作训练目标、尺度和归一化需明确制定。把模型输出截取 20 维或补零不构成有效本体适配；其他本体的统计不能直接当作 RBY-1 统计。若先运行基础权重做探索，必须记录实际采用的假设，其结果只描述该探索配置。

### 3.4 环境及本地资源

当前主工程固定 `transformers==5.4.0`，两个参考仓库固定 `transformers==4.53.2`，并依赖 JAX/Flax/Orbax；按用户要求采用项目内独立 π0.5 venv。具体最小推理依赖在实施时确定，无需整体引入训练与数据处理依赖。

参考 tokenizer 默认从远端获取 PaliGemma tokenizer。为满足本地独立启动，需显式准备 tokenizer 路径及必要资源，使模型启动与推理无需访问远端服务。本轮未检查 tokenizer 是否已在本机准备。

## 4. 建议的功能边界

### 4.1 统一推理与本体适配

- 策略配置明确 backend、embodiment、checkpoint、tokenizer、归一化来源、解释器、推理参数和分块执行步数。
- 归一化模式按权重约定配置：本地 RoboCasa 分支默认 mean/std，原版 openpi 的 π0.5 默认分位数；共用核心不等于强制同一归一化模式。
- π0.5 共用一套推理核心；Panda RoboCasa 与 RBY-1 分别定义观测打包和动作转换。保留权重对应的不同输入输出约定，不强求两本体共用同一个状态向量。
- 模型加载一次、按 episode 清空动作缓存和推理状态，动作块不跨任务或机器人使用。
- 明确动作步、控制步和物理步的对应关系，按实际时间执行；GPU 由运行参数选择，不固定卡号。

### 4.2 Panda RoboCasa 对齐

- 补齐权重使用的左外视、右外视和腕相机；核对安装位姿、内参、图像方向与预处理，不用重复同一帧替代缺失视角。
- 从真实仿真状态构造 16D proprioception，明确末端的相对参考系、四元数顺序及底盘原点。
- 按参考 body/site 约定分别取位置与旋转，核对 base center 与项目 pivot；不能直接将现有 TCP site 位姿整体代入权重观测。
- 实现 12D 动作到本体控制的对应关系，核对末端姿态、夹爪符号、底盘、升降和 control_mode 的语义及频率。
- 使用 checkpoint 配套归一化统计，保留原始预测和实际提交控制的区别。
- 评估 cuRobo 规划与 π0.5 共用 RoboCasa 执行层的可行性，使后续示教动作与部署协议一致。保留 cuRobo 规划能力；position profile 的迁移方式在跟踪、碰撞和任务对照后确认。详见[评估](../info/01-panda-robocasa-alignment.md)。
- 共用控制层以未归一化 RoboCasa 12D 动作为边界，保留同一目标更新、mode、夹爪与低层反馈语义；cuRobo 和 A* 在上游转换参考轨迹，π0.5 在上游完成观测处理、反归一化和动作分块。Panda 的这一约定不强加给 RBY-1。
- 当前按路线 B 细化：cuRobo 轨迹经 FK 提供位姿参考，由反馈跟踪器结合实测状态和 controller 目标生成 action；π0.5 提供相同协议 action，两路进入同一个 RoboCasa controller。FK 不修改执行中的仿真状态，不给示教端设置额外的下游控制辅助。
- profile 对齐需同时评估 actuator、摩擦、重力补偿和物理步长；参考 OSC 与当前 position 响应不因共用动作字段就等同。cuRobo 实际关节路径和导航包络需重新核对。详见[共用链路评估](../info/01-panda-shared-control.md)。
- 控制或相机 profile 变化后重新构建适用的场景输入并标定；不能直接把旧 Panda cuRobo 标定当作 π0.5 标定。benchmark 的参考区域仍按其原有构建协议解释。

### 4.3 RBY-1 适配与后续微调

- 明确相机选择、state/action 字段、单位、相对或绝对语义，以及归一化来源。
- 先完成基础权重真实加载、推理和小规模闭环，分别报告接口可用性及任务表现。
- 预留同一输入输出约定下更换微调 checkpoint 的能力；本轮不自动启动数据采集或训练。
- 后续微调前再确定数据来源、转换格式、统计计算、训练范围、资源预算与任务验收标准；不为推理接入扩建完整训练平台。

### 4.4 工作流及记录

建议首期覆盖 `evaluate-stations` 与 `evaluate-bench`：π0.5 能在两本体上运行，MolmoBot 保持 RBY-1 支持；共用 episode 生命周期、实际 stepping、任务判定与记录接口。

保持现有五个主 CLI 命令，按配置扩展后端选择。`build-scenes` / `build-bench` 只调整所需的本体及传感器 profile；`collect-lastmile` 的策略连续采集扩展建议另列后续范围。现有 cuRobo 采集链与未来微调数据的控制协议对齐纳入本次可行性评估；是否迁移默认执行、批量重采或训练均待后续确认。

结果保存实际策略、本体、权重目录、归一化来源、相机/控制 profile、seed、推理与执行参数，以及必要的原始预测和实际动作。继续区分任务成败、执行终止、仿真问题、模型服务问题和记录完整性；视频来自真实 rollout。

## 5. 验收建议

| 层次 | 验收内容 | 结论边界 |
|---|---|---|
| 独立启动 | 在单仓库、单工作区中，通过项目主环境与项目内独立 π0.5 venv，用本地权重/tokenizer 启动两种 π0.5 配置 | 无相邻仓库 Python 运行依赖；不等于任务成功 |
| 真权重推理 | 两份指定权重均完成恢复并处理真实本体观测，返回动作块 | 不能用 mock、随机权重或 import 成功替代 |
| 语义对齐 | Panda 三相机、16D 状态、12D 动作与参考控制逐项对齐；RBY-1 明确自己的字段、控制及统计 | 对关键坐标和控制做针对性检查，不穷举低概率异常 |
| 实际闭环 | 两本体各完成小规模真实 stepping，产生轨迹、视频及任务判定 | 无法证明 RBY-1 base 已具备有效策略能力 |
| 工作流 | 两本体可通过既有逐点与 bench 入口选择 π0.5，MolmoBot 相关回归通过 | 相同模型动作缓存不跨 episode；不混用标定 |
| zero-shot 结果 | Panda 不更新权重，记录实际任务、样本数、seed、成功/失败/未知和延迟 | 未约定最低成功率，不以调整成功判定追求通过 |

## 6. 尚需补齐的事项

1. **技术核对与迁移判断**：已找到官方动作拆分和 Gym 映射；仍需核对 checkpoint 对应的 RoboCasa/robosuite 最终控制配置、相机标定、状态参考系及旋转顺序。cuRobo Panda 能否采用共用执行层，按[评估中的最小步骤](../info/01-panda-robocasa-alignment.md)验证后确认。
2. **RBY-1 协议**：为后续训练保持一致的 state/action 与归一化方案；不能默认 base 权重已经学过现有 20D 控制布局。
3. **评测样例**：建议优先使用现有 Cup pick 做最小闭环，再扩展 open；具体样本和规模在实施方案中列明。暂不将未覆盖任务或连续采集列为已验收能力。

以上技术核对属于后续实施工作，不要求用户自行提供控制映射。需求状态保持 draft，建议范围不视为已完成的实现。

## 实施阶段补充：RBY-1 训练数据

用户已确认存在 RBY-1 position 示教，优先从当前或 refactor-light outputs 定位 materialized_lastmile；只有引用数据时先物化。直接从训练集计算 OpenPI state/action mean/std 与 q01/q99，并随 Rby1DataConfig 保存，不等待外部 norm_stats.json。转换保留 state22、rby1-20d、head/wrist RGB 和 task。本轮只完成数据准备与推理适配，正式微调另行执行。

## 全量参考标定补充

Panda-Omron 与 RBY-1 均需显式配置固定肩枢轴至 TCP 工作臂展、底盘及车轮外接圆半径；采样比例与 RBY 原规则统一。全量 cuRobo 标定覆盖所有物理合法站位，可见性只记录。具体依据、术语区别和配置职责见[双本体全量标定需求](02-embodiment-full-calibration.md)。
