# π0.5 接入 Panda-Omron 与 RBY-1 构建方案

状态：active  
日期：2026-10-18  
替代：无  
关联：[接入需求](../requirement/01-pi05-integration.md)、[Panda profile 评估](../info/01-panda-robocasa-alignment.md)、[路线 B 链路评估](../info/01-panda-shared-control.md)、[方案编写总结](../delivery/02-build-plan.md)

## 1. 构建目标与本轮边界

**采用一套项目内 π0.5 推理核心、两个本体适配；Panda 按路线 B 构建共用 RoboCasa controller，RBY-1 先建立与现有记录一致的适配协议。** π0.5 与 MolmoBot 为平级后端，分别运行，通过配置选择。

本方案用于实施与验收；当前进度见[构建结果](../delivery/03-implementation.md)。用户已授权开始构建并要求从已有 RBY-1 示教计算本体统计。Panda 默认 position profile 的切换仍以完整路线 B 对照为依据；当前新增显式 robocasa-v1 配置。

2026-10-18 首轮实施已完成：共用环境/推理、两本体适配、RBY 数据与统计、工作流分派已落地；真实验证覆盖两权重、两本体 5 s 短 rollout、路线 B 固定抓取、移动参考跟踪与 A* 绕障。短 rollout 均未抓取成功。全量 profile 迁移、完整 S0→S1→移动抓取、批量评测和正式微调继续按本方案分别验收。

| 交付部分 | 本次实施目标 | 能力判定 |
|---|---|---|
| 共用推理 | 在单仓库自有环境恢复两份本地权重，提供同一模型服务接口 | 真权重推理与独立启动分别验证 |
| Panda π0.5 | RoboCasa 三相机、16D 状态、12D 动作、配套统计与控制 | 先 zero-shot，真实报告任务表现，不设虚构成功率目标 |
| Panda 路线 B | cuRobo → FK → 反馈跟踪 → RoboCasa action，与 π0.5 共用 controller | 固定底盘、移动协调、实际碰撞与任务对照支持后再确认默认迁移 |
| RBY-1 π0.5 | 基于 pi05_base，明确 22D 状态、20D position 动作及本体统计 | 基础推理接通不代表已有 RBY-1 技能；统计和任务闭环单独验收 |
| 工作流 | evaluate-stations、evaluate-bench 可选择 π0.5；cuRobo 示教适配路线 B | 沿用五个主 CLI、现有任务判定与记录链 |

正式微调、批量重采、π0.5 连续恢复采集、双模型同时驻留和实机部署不在首期范围。路线 B 下 cuRobo 的示教执行与数据布局适配属于首期，不能因“暂不训练”而遗漏动作和观测记录。

## 2. 运行结构与环境

主进程使用项目 `.venv`，负责场景、相机、规划、controller、物理 stepping、任务判定和记录。π0.5 子进程使用项目 `.venv-pi05`，负责 tokenizer、模型恢复、输入输出变换和推理。每次运行选择一个后端；模型在 run 内按需加载一次，episode 之间清空动作缓存和随机状态。

```text
Panda 示教：cuRobo → FK参考位姿 → 反馈跟踪 → RoboCasa 12D action ─┐
                                                              ├→ 同一 RoboCasa controller → Panda
Panda 策略：观测 → π0.5 → 反归一化 → RoboCasa 12D action ────────┘

RBY-1 策略：观测 → 同一 π0.5 核心 → RBY-1 20D action → position 控制
MolmoBot：  保留当前 RBY-1 multitask → native 控制
```

目录和解释器都相对项目根目录解析；当前 `model-train` worktree 只是开发位置。运行不 import、source 或临时加入 `reference_repos`，不使用其他 checkout 的 venv。权重与资产可采用配置中明确的 NAS 路径。

### 依赖仍由 pyproject.toml 声明

主工程当前固定 Transformers 5.4.0，参考 openpi 固定 4.53.2，并使用 JAX/Flax/Orbax。拟在根 `pyproject.toml` 增加独立的 `[dependency-groups].pi05`，由 `scripts/install_pi05.sh` 安装到 `.venv-pi05`；不将互相冲突的模型依赖直接叠加到主环境。

项目 `scripts/install_pi05.sh` 已按以下顺序实现：

```bash
uv venv --python 3.11 .venv-pi05
uv pip install --python .venv-pi05/bin/python --group pi05
uv pip install --python .venv-pi05/bin/python --no-deps -e .
```

最后一步只安装本项目代码；模型环境需要的依赖由 pi05 组完整声明。模型服务入口不能导入主环境专用的 cuRobo、仿真或 MolmoBot 模块。既有 `scripts/install.sh` 继续安装主环境，另在说明中加入 π0.5 安装步骤。

首版选择 **JAX 推理**，直接读取两份权重的 `params/`，不先做权重转换。以参考的 JAX 0.5.3、Flax 0.10.2、Orbax 0.11.13 为依赖核对起点，最终按实际恢复与导入需要固定版本；不整套带入训练、LeRobot、RLDS、W&B 等依赖。

本轮只读比对中，两个本地参考仓库的 `pi0.py`、`gemma.py`、`siglip.py`、`tokenizer.py` 和 `policies/policy.py` 内容相同，`pi0_config.py` 的差异为 PyTorch compile 配置。这支持共用 JAX 核心，但不能替代两份权重的真实恢复验证。

## 3. 模型服务与策略选择

### 一套核心，按权重配置变换

从参考仓库移入必要的 π0.5 JAX 模型、恢复、SentencePiece tokenizer、图像及归一化变换，保留许可和来源说明。模型配置以 action_dim=32、action_horizon=50、max_token_len=200 为参考起点，并与指定 checkpoint 核对；这些是架构约定，不任意开放给运行者调整。

两本体共用加载与采样，分别配置相机映射、状态布局、输出动作布局及统计。Panda 使用 RoboCasa mean/std；RBY-1 适配默认采用原版 π0.5 的分位数变换，使用本体 q01/q99；后续微调权重必须携带匹配配置。不能依据“都是 π0.5”统一套用统计。

tokenizer 使用显式本地 `paligemma_tokenizer.model` 路径，避免启动时下载。资源准备与安装可以按需联网，模型启动与推理不依赖远端服务。未找到资源时报告实际缺项，不以空目录或其他 tokenizer 替代。

### 最小服务接口

沿用本地子进程 IPC 方式；不新增 WebSocket 服务、端口管理和远程部署体系。复用既有进程所有权、超时与关闭方式；仅在确有共用代码时提取现有 IPC 启停/收发，不先重构整个 MolmoBot 服务。

| 调用 | 输入与输出 | 责任 |
|---|---|---|
| 启动/ready | 已解析配置 → 实际 checkpoint、布局、统计模式、设备、加载耗时 | 子进程完成恢复后才报告 ready |
| episode 开始 | attempt_id、seed | 主进程清空旧动作块；服务更新采样随机状态 |
| infer | 三路 RGB、未归一化 state、prompt → 反归一化动作块及推理耗时 | 预处理和模型计算在服务内完成 |
| 执行动作 | 主进程取块中动作，逐周期交给本体 | 只有 runtime 能推进仿真 |
| 关闭 | 正常退出或已有失败处理 | 主进程拥有并关闭子进程 |

动作块只在主进程缓冲，避免两端分别维护 buffer_index。模型预测 50 步，首版 `replan_steps=5`，执行前 5 步后重新观测；采样步数先沿用参考默认 10。模型加载、单次 RPC、episode 墙钟预算分别配置；同步推理期间仿真时间不前进，墙钟仍计时。

策略 JSON 增加显式 `backend`，使用一个简短加载/创建入口分派 `molmobot` 与 `pi05`，不使用插件注册系统。现有 MolmoBot 配置随实现更新并验证。`ExecutionConfig.operation_backend` 扩展为 `curobo / molmobot / pi05`，`policy_config` 由相应后端解析，避免 profile 保存逻辑仍固定调用 MolmoBot 配置类。

## 4. 配置与资源契约

以下是**拟新增配置示意**，不是当前可运行配置。解释器相对项目根目录，checkpoint/tokenizer/norm_stats 按现有 data_root 规则解析；绝对 NAS 路径可直接使用。

```json
{
  "backend": "pi05",
  "embodiment": "panda_omron",
  "data_root": "data",
  "python": ".venv-pi05/bin/python",
  "checkpoint": "/nas/wenyifan/models/pi05_pretrain_human300/multitask_learning/75000",
  "tokenizer_path": "models/paligemma_tokenizer.model",
  "norm_stats": "/nas/wenyifan/models/pi05_pretrain_human300/multitask_learning/75000/assets/norm_stats.json",
  "normalization": "mean_std",
  "replan_steps": 5,
  "task_horizon": 400
}
```

RBY-1 使用同样的配置结构：`embodiment=rby1m`、`checkpoint=/nas/models/pi05_base`、`normalization=quantile`，`norm_stats` 指向项目准备的 RBY-1 position 统计。**该统计当前没有现成已确认文件，配置示例不能假装它存在。**

| 策略/执行 | 本体与控制 profile | 相机/时序 | 数据语义 |
|---|---|---|---|
| cuRobo 对照 | Panda position-v1 | 保留既有 profile | 既有关节目标布局 |
| cuRobo 路线 B | Panda 候选 robocasa-v1 | RoboCasa 三相机，候选 20 Hz / 2 ms | state16 / RoboCasa action12 |
| π0.5 RoboCasa | 同一个 Panda robocasa-v1 | 与路线 B 相同 | 同一 state16 / action12 |
| π0.5 base/后续微调 | RBY-1 position-v1 | head 与双腕；首版沿用该 profile 20 Hz / 4 ms | state22 / position action20 |
| MolmoBot | RBY-1 molmobot-native-v1 | 保留 1024×576 及 100/20/4 ms | 既有 state22 / native action20 |

候选 `robocasa-v1` 名称在实现中统一使用，避免再把所有非 MolmoBot profile 都视为 position。几何/传感器配置、执行配置、策略配置分别承担自己的字段，不在三处重复定义同一状态布局。

## 5. Panda：构建 RoboCasa 本体与控制 profile

### 观测对齐

补齐 `robot0_agentview_left`、`robot0_agentview_right`、`robot0_eye_in_hand`，以 RoboCasa 的安装参数为依据。先核对原始相机的方向和分辨率，再在服务内按参考缩放填充至 224×224；不重复翻转或套用 MolmoBot fisheye。

状态按权重顺序构造：相对末端位置 3、相对末端旋转 4、底盘位置 3、底盘旋转 4、两指关节位置 2。按参考分别处理末端 site 位置、body 旋转及 xyzw 四元数，核对 base center 与项目 pivot；该观测变换与 controller 使用的 TCP/site 变换分别明确，不能混用。

相机和 16D 状态只提供权重约定的输入。规划抓取位姿、目标世界坐标、物体 mask 和任务判定信息留在工程侧，不额外塞入 π0.5 输入。语言复用明确的英文任务描述。

### 控制与物理参数

新建显式 profile，保留原 factory 动力学和对应 actuator 的起点，移入必要 OSC、底盘速度、torso 和夹爪逻辑。核对 RoboCasa 环境最终施加的部件顺序、legacy 底盘变换、mode/夹爪阈值，而非只复制默认 JSON。

目标动作顺序为 `[eef_pos(3), eef_rot(3), gripper(1), base(3), torso(1), mode(1)]`。每个策略周期设置一次目标；随后每个物理步从真实状态更新反馈和 ctrl。首版候选使用 20 Hz / 2 ms；与 checkpoint 实际版本核对后固定。2 ms 下每周期 25 个物理步，使用同一 Simulation stepping 和停止回调。

position 路径清零摩擦、修改伺服、body gravcomp 的逻辑不自动应用到 RoboCasa profile；匹配参考的摩擦、偏置补偿与执行器限制，防止补偿重复。controller 保留末端目标、mode、夹爪内部目标和底盘参考等必要状态，执行前可快照复制，begin 后不重置。

旧 position 快照不能在 rollout 中切换为新 profile。按候选模型、相机与 timestep 在准备阶段建立新的场景/快照，再进行站位和实际标定。

## 6. Panda 路线 B：cuRobo → FK → action → 同一 controller

cuRobo 继续规划关节与底盘轨迹，沿用其时间重采样。FK 使用 cuRobo 模型或独立计算数据提供参考位姿，不修改执行中 qpos。夹爪意图来自示教阶段，不由 FK 推导。

每个动作周期，跟踪器完成四件事：

1. 读取同一参考点的臂、底盘和高度，求世界末端与臂安装中心参考，转换至控制器坐标系，避免底盘运动被计入两次。
2. 按 mode 选择实测位姿或 controller 上一期目标作为增量基准；按实际尺度求平移和旋转 action，旋转使用轴角与正确乘法顺序。
3. 同步求底盘速度反馈、torso 命令及夹爪意图，产生 12D action；超出可执行步幅时分段或减慢参考推进。
4. 根据实际末端、底盘和高度误差推进参考，受现有 attempt 预算约束；关节误差只作为诊断，不继续使用原关节到达判断卡住 OSC 跟踪。

控制器不区分动作来自 cuRobo 还是 π0.5，不给示教端添加额外增益或规划关节零空间参考。当前 7D 关节参考经 FK 后丢失冗余姿态约束，因此不能直接继承原关节路径的碰撞判断；同时检查实际 TCP 跟踪、关节姿态、障碍物间距与接触结果。

原有 pregrasp/approach 等串联计划在阶段边界核对实际状态；偏离影响连接时，用既有规划预算重连或重规划。闭爪和 hold 生成明确保持 action，不以全零向量代替；不能重置仿真来消除偏差。

A* 路径仍可复用，导航执行改为同协议的底盘 action，并按基座参考保持末端、torso 和夹爪意图。原保持关节姿态的整机包络需核对实际臂运动，不能只验证操作段而漏掉导航。

若代表性验证显示原 OSC 零空间行为无法满足 cuRobo 所需路径，保留真实负结果，不直接确认默认迁移。届时先保留可用的 Panda π0.5 RoboCasa 接入，再明确示教方案调整；不悄悄仅给示教增加部署时没有的控制输入。详细推导见[链路评估](../info/01-panda-shared-control.md)。

## 7. RBY-1：先固定待微调协议，再补齐统计与能力验证

首版采用现有 `position-v1` 的 state22 与 action20，作为**本项目拟定的 RBY-1 π0.5 适配协议**，不宣称这是 pi05_base 原本学过的布局。采用该协议可复用现有 cuRobo 记录，避免把 native motor 夹爪与 position 单位混在一起。

### 状态与动作字段

| 范围 | state22：实测量 | action20：本周期命令 |
|---|---|---|
| `[0:3]` | 底盘 XY/yaw | 世界 XY/yaw 增量，m / rad |
| `[3:10]` | 左臂七关节，rad | 左臂关节增量，rad |
| `[10]` | 左夹爪第一指关节，m | 左夹爪绝对第一指目标，m |
| `[11:18]` | 右臂七关节，rad | 右臂关节增量，rad |
| `[18]` | 右夹爪第一指关节，m | 右夹爪绝对第一指目标，m |
| 状态 `[19:22]` / 动作 `[19]` | torso 实测关节 `[1,2,3]`，rad | 绝对耦合参数 h，展开为 `[0,h,-2h,h,0,0]`；不是世界高度或米 |

该 state22 与当前 `state22()` 一致；固定头部不加动作维度，按初始化配置保持。action 已经包含底盘/臂增量，不再额外套一遍通用 DeltaActions。position 夹爪沿用当前负值开口约定，不能当作正的总夹爪宽度或 native motor 指令。

相机映射先固定 head → base 图像、左腕 → left_wrist、右腕 → right_wrist；读取实际 profile 图像后按模型规则处理至 224×224。不复用 MolmoBot 的鱼眼和目标点 prompt。state22/action20 分别补至 32D 送入核心，输出仅按**已明确定义的布局**恢复为 action20；截取本身不证明基础权重具备该控制语义。

### 统计是闭环前的实际输入条件

先查找已有且完整、profile 匹配的 RBY-1 position 示教；从将用于训练的数据中计算本体统计，保存字段、控制周期和来源引用。没有合适示教时，先通过既有 cuRobo 采集链准备小规模真实示教并判定结果。不从 franka、arx 等其他本体借统计，不伪造 identity 统计。

暂时缺少统计时，可完成基础权重恢复和归一化输入上的模型 smoke；它只证明模型核心可计算。RBY-1 实际观测到动作的闭环、evaluate-stations 和 benchmark 验收仍标记未完成，不能用合成输入填补。

取得统计后运行真实观测推理与小规模 rollout，区分推理失败、非法动作、物理失败和任务成败。即便数值与接口正确，base 也可能缺乏对应技能；下一步微调使用同一字段、相机和控制周期，不将该问题伪装成接口已完全验证。

## 8. 工作流、benchmark 与记录

### 后端分派与共用执行

`evaluate-stations` 和 `evaluate-bench` 依据策略配置选择服务，并依据本体/profile 选择观测与 stepping。移除“非 MolmoBot 就必为 cuRobo position”和“VLA 必为 RBY-1 native”的隐式假设，改成明确的支持组合；不增加兜底后端。

VLA 的循环、资源生命周期、接触采样和结果收尾复用现有实现；本体数量与手指查询使用适配器的 `arms / finger_bodies()`。MolmoBot 的目标点、fisheye 和 10 Hz native stepping 保留策略专属处理，不让 Panda 执行固定的双臂 RBY 代码。先提取确有复用的段落，不先整体重写 MolmoBot。

`build-scenes / build-bench` 接受明确的候选本体输入。`collect-lastmile` 首期继续选择 cuRobo，其 Panda 操作可使用路线 B；π0.5 的连续恢复采集支持不在此次顺手扩展。

### benchmark 不自动改造旧 Panda 快照

当前 benchmark 固定调用 RBY-1 `native_scene()`。后续仅 MolmoBot 分支保留该适配；RBY-1 π0.5 position 分支加载匹配的 position 场景；Panda π0.5 分支加载新 RoboCasa profile 场景。

Panda 路线 B 的新 profile 需要相应参考标定和 benchmark 输入，不把旧 position 标定解释为同一控制条件下的结果。已发布的原 benchmark 输入保持只读；若开展 profile 变化对照，分别报告其执行配置和参考来源。Recovery 区域仍由 benchmark 原有构建协议定义，不从策略预测或平滑图反推成功区域。

### 一条真实记录链，按布局导出

继续使用 AttemptRecorder：Panda 记录 state16/action12，RBY-1 记录 state22/action20；原始 qpos/qvel、时间与实际控制诊断保留。需要同时保留原始请求 action 与实际提交 action，训练标签选择真实提交值，完整预测块只作诊断。

现有 recorder 的 ctrl 是静态命令数组；新增反馈控制后要能保存实际控制随物理步的变化，不能仅重复写周期起点的 ctrl。逐物理步数据写入既有 physics trace，周期边界仍只记录一次真实状态/action，不新增平行轨迹文件。

沿用现有 materialize 和 batch 读取入口，使用 layout 区分状态/动作维数；不同布局不直接混为一个 batch。仅有视频不构成可微调图像样本，需明确开启 record_images，保持 `obs[t] / action[t] / obs[t+1]` 对齐；部分执行和不完整记录保留实际标记，不能当作正常完整周期样本。

## 9. 文件与模块改动范围

以下是拟改动位置，实施时在这些职责内保持最小实现，不按每个术语新增目录。

| 位置 | 计划内容 |
|---|---|
| `pyproject.toml`、`scripts/install_pi05.sh`、`.gitignore` | pi05 依赖组、独立 venv 安装、忽略环境 |
| `policies/pi05/` | config/client/service、共用最小 JAX 核心、本体变换及许可；不移入完整训练仓库 |
| `policies/` 的配置/客户端创建入口 | 显式分派 MolmoBot 与 π0.5，复用必要 IPC 逻辑 |
| `config.py`、`runtime/control/panda_omron.py`、拟增 `panda_robocasa.py` | profile、原生控制准备、controller 与 16D 观测/12D 记录 |
| `runtime/simulation.py` | 一次设目标、逐物理步反馈、按 profile 时钟执行并回调记录 |
| `runtime/execution/context.py`、`navigation.py`、拟增 `robocasa_tracking.py` | cuRobo FK 参考跟踪、A* 动作适配、到达与保持语义 |
| VLA execution 与 `workflows/collection.py` | 后端/本体适配，保留共同生命周期与判定 |
| `workflows/lastmilebench.py`、`runtime/construction/benchmark_scene.py` | 匹配的 scene/profile 分派，保留原 MolmoBot 场景处理 |
| `data/recorder.py`、现有 export | 动态 ctrl 诊断、布局、观测/动作对齐和训练样本读取 |
| `configs/policies/` | pi05_panda_omron、pi05_rby1 两份策略配置；更新 MolmoBot backend 字段 |
| `configs/robots/`、`configs/execution/`、`configs/scene_datasets/` | Panda RoboCasa 本体/场景配方、两本体 π0.5 执行配置及 cuRobo 路线 B 配置 |
| `tests/`、`tests/smoke/pi05.py` | 相关局部回归与显式真实验证入口 |
| 项目运行说明与各目录 README | 实现完成后同步真实可执行步骤和支持矩阵 |

## 10. 实施顺序与阶段出口

| 阶段 | 工作与依赖 | 结束时必须交付的事实 |
|---|---|---|
| P0：补齐输入 | 核对权重参数、tokenizer、Panda 最终控制与相机；调查 RBY 统计数据来源 | 必要资源实际可读，版本/参数差异与缺项明确；RBY 数据缺项不阻塞 Panda 建设 |
| P1：共用推理 | 实现 pi05 组、最小核心、本地 IPC、两种配置；依赖 P0 的权重/tokenizer | 两份真权重完成恢复和参考输入 smoke；合成/归一化输入结果只证明模型核心可计算 |
| P2：Panda 控制 | 新 profile、观测、执行器、反馈 stepping；独立于模型推理调试 | 目标更新与 ctrl 对照、静止/平移/旋转/夹爪/torso 的实际结果；模型控制稳定性明确 |
| P3：Panda 路线 B | 接入 FK/跟踪、阶段保持与 A*；依赖 P2 | 固定底盘 Cup pick、联合轨迹、短导航的实际路径与任务结果；给出是否支持默认迁移的结论 |
| P4：RBY 适配 | state22/action20、相机、统计和真实 rollout；依赖 P1 与合格数据 | 统计来源清楚，实际观测闭环结果或明确缺项；不虚构技能与成功率 |
| P5：工作流 | 策略分派、Panda/RBY 逐点与 bench，布局导出；依赖相应本体阶段 | 两本体软件路径可选 π0.5、MolmoBot 回归、匹配 profile 的真实小规模评测与训练读取 |
| P6：交付 | 汇总事实、条件与失败，更新说明 | 可在普通单仓库安装启动；已验证能力、未验证能力和后续微调输入分别列明 |

Panda zero-shot 在 P1/P2 接通后即可开展，不必等 cuRobo 跟踪完全迁移。RBY 统计/示教准备也可在实施中独立推进，但本方案不要求并发模型或额外 agent。

P3 前原 position profile 保留为真实对照。P3 支持迁移后，才将 Panda 对应场景、标定、示教配置和说明切换到 RoboCasa；不把验证前的候选配置写成全项目默认能力。

## 11. 验证与验收

### 软件检查只覆盖实际改动

新增针对性检查：配置路径和后端分派；两本体布局/统计模式；旋转与 mode 的目标更新；动作块跨 episode 清空；逐物理步控制与实际记录；profile 不匹配的标定不被复用。先跑相关领域，再按仓库要求跑全套回归；不穷举低概率异常或为配置示例另建测试框架。

真实 smoke 放在显式入口，不混入默认 unittest。GPU 使用前查看 0–7 的利用率与空闲显存，按运行参数选卡；实际推理记录设备、显存和耗时，不在文档承诺未经测量的显存占用或吞吐。

### 最小真实评测集合

- 控制核对：Panda 同一本体/状态/action 下对照参考目标与 ctrl，并运行少量平移、旋转、底盘、torso 和夹爪动作。
- Panda 示教：现有 Cup pick 固定底盘对照，再加一段联合移动与短导航；检查实际关节路径、接触及原有任务条件。
- π0.5 rollout：两本体各选一个明确场景/目标/初态开始，保存实际输入、action、轨迹与视频。Panda 不更新权重；RBY 若统计未就绪则该项未完成。
- 工作流：同一小规模输入经 evaluate-stations 和匹配 benchmark 入口运行；需要困难评测时使用完整参考标定，截断 smoke 不冒充正式 Recovery 验收。
- 数据：从真实完整记录读取一个训练窗口，核对图像、state、action 和时间，不用合成记录证明真实采集可用。

不新增统一“成功”开关。模型恢复、推理响应、物理有效、任务成败、记录完整和 benchmark 覆盖率沿用现有分别报告。共享模型/GPU 故障停止当前批次；任务失败保留负结果。接口验收通过后仍可能需要 RBY 微调或 Panda 适配调整。

## 12. 完成标准与剩余技术决定

构建完成应具备：项目内两环境的安装和本地启动；一套 π0.5 核心；Panda RoboCasa 接入及支持路线 B 的对照结果；RBY 明确协议、匹配的本体统计与真实观测闭环；两本体工作流配置；MolmoBot 行为回归；可读取的真实布局记录与执行报告。任务能力按实际结果报告，不将最低成功率作为未经约定的接入条件。

尚需在实施中确认的技术项集中为：Panda checkpoint 对应的控制/相机参数与体步长、RBY 合格示教和统计来源、路线 B 零空间姿态对实际路径的影响。不要求用户手工推导映射；这些是实施调查和验证任务。

若 RBY 无合格统计或路线 B 物理对照不支持迁移，报告明确未完成项及原因；不能将“方案已写完”或“模型能生成数组”标为双本体接入全部完成。正式微调另行基于真实示教规模、任务表现和资源预算制定计划。
