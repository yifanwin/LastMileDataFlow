# Panda position profile 向 RoboCasa 对齐的评估

状态：draft  
日期：2026-10-18  
替代：无  
关联：[双本体接入需求](../requirement/01-pi05-integration.md)、[共用控制链路详细评估](01-panda-shared-control.md)、[需求梳理记录](../delivery/01-requirements-review.md)

## 结论

**工程上可以向 RoboCasa 对齐，并保留 cuRobo 规划；但需要调整执行控制与采集协议，不能将现有 position profile 直接改名后视为等价。**

建议的候选方向是：Panda 共用本体、三相机和观测定义；cuRobo 继续输出规划轨迹，经轨迹跟踪转换成 RoboCasa 动作；π0.5 直接输出同一动作协议，两者通过同一个 RoboCasa 控制层执行和记录。这样后续微调使用的动作目标与模型部署时解释动作的方式一致。

本轮只完成源码评估。是否将现有 cuRobo Panda 默认执行切换到该控制层，须在小规模跟踪、碰撞和任务对照后确认，尚未决定替换当前 position 实现。

进一步的[链路评估](01-panda-shared-control.md)已明确：两路在未归一化的 RoboCasa 动作处汇合；需要同步改 cuRobo 跟踪、A* 导航、mode/保持语义及逐物理步反馈。物理 profile 还涉及 2 ms 对 4 ms、摩擦与重力补偿差异。原 position profile 暂保留为对照，不意味着最终永久保留两套控制。

## 1. 环境约束修订

交付按**单仓库、单工作区**设计。项目根目录管理主环境 `.venv` 与独立推理环境 `.venv-pi05`，二者安装同一项目各自所需的组件；π0.5 依赖与安装入口由项目维护。这里目录名是建议命名，核心要求是项目内独立环境。

当前在 `model-train` worktree 作业只是开发位置，不是运行架构。环境不依赖 `.worktrees/` 路径、共享环境服务或其他 checkout；配置中的解释器从项目根目录解析，例如 `.venv-pi05/bin/python`，不固化开发目录的绝对路径。普通 clone 与该开发 checkout 应能使用同一安装和启动说明。

两种策略分别运行；选择 π0.5 时由主进程启动项目管理的模型子进程。参考源码仍只用于开发核对与必要代码移入，不成为启动依赖。模型、tokenizer 和统计使用显式本地资源路径。

## 2. 本地实现与官方协议的差异

本地依据为 `runtime/control/panda_omron.py`、`runtime/simulation.py`、`runtime/execution/context.py`、`planning/panda_omron.py`，以及两个参考仓库：

- `openpi-robocasa` 确定 checkpoint 配置、三图像输入、16D 状态、12D 动作和预处理顺序。
- `robosuite` 的 `default_pandaomron.json`、`controllers/parts/arm/osc.py`、`controllers/composite/composite_controller.py`、底盘控制器和 Panda 夹爪实现确定控制机制。

| 层次 | 当前 Panda position | RoboCasa 对齐需处理 |
|---|---|---|
| 本体与规划 | 真实 PandaOmron factory 资产，cuRobo 使用独立 URDF 与碰撞球 | 可保留规划器；核对底盘轴、pivot、TCP、运动学与动力学参数 |
| 相机 | 前视与腕视；当前采集分辨率不同 | 权重要求左外视、右外视、腕视；统一安装标定、图像方向和模型预处理 |
| 状态 | 12D 关节/底盘/lift/宽度记录 | 从真实状态构造权重要求的 16D 状态；原始关节记录仍保留 |
| 臂动作 | 7 个绝对关节目标，高刚度关节位置伺服 | 6D 末端增量输入，OSC 根据状态求力矩，含姿态和零空间控制 |
| 底盘 | 世界坐标的绝对 XY/yaw 位置目标 | 底盘速度控制，需处理机体参考系及输入尺度 |
| lift / torso | 初始化后锁定；规划锁住 torso | 需要支持协议中的 torso 通道；cuRobo 可以继续在一次规划中固定高度，控制层仍须能执行策略的 torso 命令 |
| 夹爪 | 以米为单位的绝对开口宽度 | RoboCasa 关闭指令、阈值和 robosuite 的内部夹爪目标更新；不等价于直接给宽度 |
| 时序 | 20 Hz position 更新，4 ms 物理步 | 匹配策略周期、低层反馈更新与物理步；相同 20 Hz 不能证明动态响应一致 |

补充核对官方源码发现：`convert_action` 只将 12D 向量拆成命名字段。Gym 映射将 `base_motion` 的前三项传给底盘、第四项传给 torso，并对夹爪与 mode 做 0.5 阈值处理；状态中的相对末端字段来自 `robot0_base_to_eef_*`。这些是当前官方实现的依据，尚未证明与本地 checkpoint 训练版本完全相同。[动作拆分](https://github.com/robocasa/robocasa/blob/main/robocasa/utils/env_utils.py)、[Gym 映射](https://github.com/robocasa/robocasa/blob/main/robocasa/wrappers/gym_wrapper.py)

官方 Kitchen 默认策略频率为 20 Hz，且会进一步指定 Panda 的部件排列和 legacy 底盘控制器。因此只抄本地默认 JSON 不足以复现最终控制配置；需要连同环境施加的配置一起核对。[Kitchen 配置](https://github.com/robocasa/robocasa/blob/main/robocasa/environments/kitchen/kitchen.py)

本地 `HybridMobileBase` 根据 mode 选择以当前实测末端或上一期目标更新下一目标；OSC 使用雅可比、质量矩阵与反馈求力矩。现有 `Simulation._advance()` 在一次提交后保持相同 ctrl，不能直接承担这种反馈控制循环。对齐时要扩展共享 stepping 中必要的控制更新，不能另外建立一套仿真和记录链路。

还有两个需要照参考实现处理的细节：

- 本地 robosuite `robots/mobile_robot.py` 的 `base_to_eef_pos` 取末端 site 位置，而 `base_to_eef_quat` 保留末端 body 旋转；后者在注释中明确保留了旧数据约定，四元数使用 xyzw。项目当前 TCP site 位姿和自定义 `base_site` pivot 不能直接代替这些观测；必须对照 base center 和末端 body/site 分别构造。
- 两个 openpi 分支归一化默认不同：RoboCasa 分支 `DataConfigFactory.create_base_config()` 保留 `DataConfig` 默认的 mean/std 路径；原版为非 PI0 模型启用分位数归一化。Panda checkpoint 已检查到的统计只有 mean/std。统一推理必须按权重配置归一化模式，不能统一套用原版 π0.5 默认值。

## 3. 三种可选路线

| 路线 | 可行性与代价 | 对后续微调的影响 |
|---|---|---|
| A：保留 position，π0.5 动作经 IK/速度积分转为位置目标 | 可作为接入探索；改动中等，存在控制动态差异 | 可以在这一执行器上重新采集并微调，但不能宣称复现 RoboCasa 控制；zero-shot 结论需注明差异 |
| B：cuRobo 与 π0.5 共用 RoboCasa 控制执行 | 对统一目标最合适；需新增控制适配并改 cuRobo 轨迹跟踪，改动较大 | 新采集的观测、动作和部署语义一致，适合继续使用 RoboCasa 动作协议微调 |
| C：保留 position，将未来微调目标直接定义为关节位置 | 能保留当前 cuRobo 采集链，通常改动较小 | 可从 base 或其他权重初始化微调，但需使用对应的新动作变换和统计；不能把现有 RoboCasa 动作解释直接用于该权重 |

**当前按用户提出的 B 继续细化，A/C 保留为比较背景。** 默认执行是否切换仍待验证确认。

B 保留 cuRobo 的关节与底盘轨迹输出；下游跟踪器通过 FK 得到参考位姿，再结合实测状态、controller 目标、mode 和尺度生成 12D action。底盘与 torso 同步跟踪，夹爪按协议发送开闭意图。FK 只提供参考，不能单独完成 action 转换。

需区分两个目标：新的示教在部署用的控制层上采集，可以建立一致的数据协议；重现原关节 position 轨迹的每个关节姿态，则不能仅靠末端轨迹保证。

## 4. 与后续微调直接相关的约束

1. **7D 关节轨迹转为 6D 末端轨迹会失去冗余姿态约束。** OSC 零空间姿态可能不同于 cuRobo 规划的肘部姿态，实际碰撞表现必须重新检查。若需要额外姿态跟踪，须评估其对 RoboCasa 控制一致性的影响。
2. **真实位移不是原始动作标签。** 不能用相邻实测末端位姿差直接代替当时下发的动作；接触、跟踪误差、裁剪和 mode 都会影响结果。新采集应记录真实发送的 RoboCasa 动作、对应观测与时间；原始关节状态和实际 ctrl 用现有记录链保留。
3. **旧 position 数据不能无条件改列名后用于 RoboCasa 动作微调。** 由旧轨迹导出的动作只能作为转换候选，需按目标执行器重放并检查；缺失的相机观测和未记录的控制状态不能凭空补齐。优先用统一控制层重新采集最小有效示教。
4. **明确动作目标再选统计。** Panda zero-shot 使用配套 checkpoint 统计；后续微调按数据和训练方案选择统计并随权重保存。字段相同不自动证明分布相同。RBY-1 单独建立自己的统计和动作定义。

## 5. 最小确认步骤

后续验证只针对实际差异，先做少量有代表性的动作和任务，不扩建验证框架：

1. 核对 checkpoint 对应的 RoboCasa 控制配置、base/TCP 参考系与相机配置；补齐 torso、夹爪及 mode 语义。
2. 在同一本体、初态和物理条件下，对照 position 与候选 RoboCasa 执行：末端平移/旋转、底盘移动、torso 保持/变化、夹爪开闭。记录实际位姿、跟踪偏差和控制时序。
3. 用一条现有 cuRobo pick 规划尝试共同控制层，检查实际路径、肘部与障碍物接触、抓取和保持结果。路径变化导致的问题按真实结果修正，不先假定规划碰撞约束仍然成立。
4. 用 Panda 真权重做小规模 zero-shot；检查同一观测和动作定义能否服务于后续数据采集与模型回放。
5. 根据这些结果确认是否将 cuRobo Panda 统一到 RoboCasa profile。确认前保留现有实现，不将候选方案写成默认能力。

采集链的**控制与训练数据对齐可行性**已纳入本次评估；批量采集、批量重放和正式微调仍需单独确定任务与规模。本轮没有启动这些运行。

## 本轮执行与结果

已修订单仓库环境约束，对照本地两个参考仓库与项目控制实现，并补充官方 RoboCasa 动作映射和最终环境配置；进一步定位了 body/site 观测约定及两个 openpi 分支的归一化默认差异。结论是可保留 cuRobo 规划、评估统一执行层；暂不能保证 position 等价或直接切换成功。只更新文档，未安装环境、改源码、运行模型或做物理验证。
