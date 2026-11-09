# 固定底盘 SAC 站位质量图：核心需求

- 状态：active
- 日期：2026-11-09
- 替代：无
- 关联：../plan/01-implementation.md、../delivery/03-mjlab-build-plan.md

## 1. 目标与已确认范围

通过环境交互训练共享机械臂 Actor，以双 Critic 对统一初态下各候选站位的动作价值进行评分，减少手工设计站位难度规则。首版输出 **给定机器人、任务、控制器和策略版本下的操作质量代理图**；是否能用于难度排序，要以独立真实 rollout 验证。

用户已确认首版为 **Panda-Omron 单臂、固定底盘、抓取并稳定抬起**。首版不同时训练 RBY-1，不扩展到开门、抽屉、放置或导航策略。现有五命令、cuRobo 标定、MolmoBot / π0.5 流程继续保留；研究入口的拟议组织见方案。

2026-11-09 进一步确认：采用 **mjlab 并行环境 + 自定义 SAC 双 Q + DLS**。只要求通过观测、真实碰撞反馈与奖励学习避障，不要求控制器在动作执行前保证轨迹无碰撞。最终验收场景为 **val103-cup30（val103 / Cup_30）**；上游使用主 checkout 中的 `reference_repos/mjlab` 本地源码，安装参考用户提供的 [mjlab 安装指南](https://mujocolab.github.io/mjlab/main/source/installation.html)。

GPU 资源为 CUDA 0–7，运行前根据实时利用率和空闲显存选择，通过 `--gpu` 显式指定；首轮单卡，不固定卡号。

同一模型覆盖训练分布内的所有站位与场景，不按站位训练独立 Actor。不同机器人未来分别建模，首版不宣称跨本体通用。

本轮交付是需求与技术方案；以下明确区分用户要求、源码事实和建议初值，不表示已经实现或完成训练。

## 2. 对 Grand_RM 的借鉴边界

核对本地 PDF 第 4–6 页（正文 IV-A/B/C）、第 7 页 Table I，以及参考仓库实现：

| 项目 | 论文 / 参考实现 | 本项目需求 |
|---|---|---|
| 任务动作 | 连续底盘动作与离散机械臂触发组成 Hybrid Action | 固定底盘，7D 连续 TCP 增量及夹爪宽度 |
| 图结构 | Goal、Tabletop Obstacle、Ground Obstacle、Container；Goal 为中心，无独立机器人节点 | 保留四类节点、Base Frame 和 Goal 中心读出，加入机械臂及任务动态状态 |
| 网络 | GAT 后接 Actor / Critic；公式 (9) 已含观测向量拼接 | 相同结构思路，明确加入 q、速度、TCP、夹爪和相对抓取目标误差 |
| 地图动作 | 论文 IV-C 构造底盘 Gaussian 动作并设置机械臂触发 | 从当前训练 Actor 采样 K 个 7D 动作，再取双 Q 较小值 |
| 候选半径 | 正文公式 (15) 附近采用 0.8 m；参考可视化脚本另有 1.0 m | 采用用户提出的 1.0 m 可配置初值，不称为论文原值 |
| GAT / 优化 | Table I：1 层、1 head、ReLU、Adam、Actor/Critic LR 3e-4、KL weight 1e-3 | 前几项作为初值；标准 SAC 首版不引入 KL 正则 |
| 技术栈 | Fetch、Isaac Sim 2022.1.0、DGL、定制 MushroomRL、Pinocchio | mjlab / MuJoCo Warp、现有 Panda 资产、自定义连续 SAC 与批量 DLS |

论文 Table I 的 input dimension=28 与 `utils/scene_graph.py` 的 4+8+7+7+7=33 维编码不一致；本项目按明确字段定义重新确定输入维数。该源码仍有 robot 命名的注释，但索引 0 实际存 Goal，不能按注释另建机器人节点。`train_task_fetch.py` 依赖固定节点数和固定设备的批量索引，不直接迁移。

参考配置 `FetchMultiObjFetchingGAT.yaml` 中 `use_kl_on_pi: False`。KL 权重不是标准 SAC 的温度 α，不应混用。借鉴表示和算法结构，重新训练，不直接加载原有策略权重。

## 3. 环境和公平比较

每个 episode：选择或随机生成合法场景 → 采样 `(x, y, yaw)` → 固定底盘与升降自由度 → 重置统一 Home、关节速度和打开的夹爪 → 从准备好的状态开始 Actor rollout。

训练时随机化目标、容器、桌面与地面障碍物；同一张地图评分时固定场景、物体姿态和任务，仅改变候选底盘位姿。场景生成与物理 settling 可在场景池准备时完成，每回合从池中采样并恢复，不要求每回合重新编译模型。

首轮实施先固定 val103 / Cup_30 与现有家具，训练站位变化；不以随机桌面替代最终验收。按 XY 空间块预先划分训练、验证与验收站位，同一 XY 的 yaw 属于同一 split。场景随机化后续在已验证的同构模板内增加；单场景验收不宣称跨场景泛化。

所有站位必须一致：Home、夹爪宽度、升降高度、抓取目标定义、控制器、动作尺度、控制频率、时间预算、奖励和成功条件。实际初态读自准备后的仿真；相同命令值不能替代对初态一致性的确认。

候选区域首先使用目标周围半径 1 m 的圆盘并排除容器占据区；进一步检查整个底盘占地、地面支撑、初始机器人与场景接触。Home 初态与物体相交的点标为 `invalid_initialization`，不作为学习到的低 Q。**不得通过 cuRobo / IK 成功、FOV 或手工难度分数预先过滤合法但困难的站位。**

训练阶段的底盘冻结必须有物理保持；仅禁用动作中的底盘分量不够。发生超过任务容差的底盘漂移应记录失败，不能每个物理 tick 改写 qpos 消除漂移。固定底盘具体实现先复用现有位置保持，若达不到漂移要求，再加物理锁定并重新验证。

## 4. Observation 与图编码

所有几何统一转入 Base Frame；世界系用于保存和绘图。姿态显式约定内部 quaternion 为 `wxyz`，网络可用连续旋转 6D；相对姿态误差使用旋转向量，禁止直接相减 Euler 角。目标物体原点与目标抓取 TCP 不是同一坐标：通过固定于物体的抓取变换得到后者。

图保留四类 one-hot。建议采用统一的几何字段（中心、尺寸、方向、距离），而非四类互斥的大段零填充；精确维数在实现时由字段定义产生。障碍物描述需要底部高度和垂直尺寸，容器至少包含工作相关的支撑面、侧壁或开口信息。单一外包围盒无法区分实心障碍与可伸手进入的柜体；首版先覆盖桌面 / 简单容器，不据此声称任意柜体泛化。

每个桌面障碍、地面障碍和容器向 Goal 连边，增加显式 self-loop；边类别进入 GAT 的 edge feature。1 层 GAT 后读取 Goal 的 embedding，确保方向能把障碍物信息送达 Goal。节点数量可变，按图 batch 的实际索引读取 Goal。不存在额外 Robot 节点。

基础向量遵循用户定义：

`arm = [q, qdot, TCP_position_base, TCP_rotation, gripper_width, target_to_TCP_position_error, target_to_TCP_rotation_error]`

为抓取动态和连续控制补充真正影响后续转移 / 奖励的量：目标线速度与角速度、夹爪速度、相对初始支撑面的抬升高度、接触 / 支撑状态、稳定保持计时；如果控制器使用上一周期速度命令做加速度约束，也将该命令纳入状态并在 reset 清零。可动障碍物的速度随节点更新。静态物体特征可缓存，目标及被碰动物体不能沿用初始图。

建议首版按“20 秒内完成抓取”定义有限时域任务，输入剩余时间；具体时长是建议初值。这样超时是任务终止，不 bootstrap；外部采样中断才作为 truncation，保存真实 final observation 并 bootstrap。此区分遵循 [Gymnasium 时间限制说明](https://gymnasium.farama.org/tutorials/gymnasium_basics/handling_time_limits/)。

Actor 与 Critic 使用相同观测字段。首版使用仿真真值，不接视觉估计或 VLA 特征。此表示是工程上的紧凑观测，不宣称严格包含完整物理 Markov 状态。

## 5. 动作和 Cartesian Controller

Actor 产生 `mu, log_std ∈ R^7`，通过重参数采样和 tanh 得到 `a ∈ [-1,1]^7`。旋转 3 维定义为 Base Frame 下的旋转向量增量，不作为三个互相独立的 Euler 绝对角。

| 分量 | 含义 | 建议初值 |
|---|---|---|
| a[0:3] | TCP 平移增量 | 每轴 ±0.01 m / policy step |
| a[3:6] | TCP 旋转向量增量 | 每轴 ±5° / policy step |
| a[6] | 目标夹爪宽度 | `w_min + (a[6]+1)/2 * (w_max-w_min)` |

夹爪约定 -1=闭、+1=开，宽度单位 m，范围来自机器人配置。20 Hz 下平移每轴上限对应 0.2 m/s、旋转每轴 100°/s；这些是动作上限，不保证机械臂可实现，实际关节限制由控制器执行。

建议采用直观的 TCP 原点增量语义：

`p_cmd = p_TCP + Δp_base`

`R_cmd = Exp([Δω_base]×) R_TCP`

每个 policy step 从实测 TCP 建立一次目标，底层若有多次求解，跟踪同一个目标，不反复叠加增量。用户给出的 `Exp(Δξ^) T_TCP` 若直接把平移增量当作 spatial twist 的线速度项，旋转还会改变 TCP 原点位置，与独立 XYZ 增量语义不同；首版建议采用上述分离更新，并在实现前固定此约定。

DLS 放在此层，采用 mjlab GPU 批量 Jacobian 与阻尼最小二乘，将 TCP 目标变为关节位置命令；Gaussian Policy 由 PyTorch 实现。用户已选择学习层面避障，不再将 Mink 碰撞 QP 或轨迹预检作为前置要求。参考 [mjlab 动作说明](https://mujocolab.github.io/mjlab/main/source/actions.html)，实现以本地源码接口为准。

控制器需要满足：

- 只优化工作臂自由度，底盘、升降及场景物体自由度不参与 IK；当前实测关节状态作为每次求解起点。
- 关节位置、命令速度及执行器力限明确配置；限幅用于机器人控制，不提供轨迹避碰保证。首版不额外引入碰撞约束 QP 或通用加速度约束求解器。
- 机械臂与环境、自碰撞保持真实物理反馈；合法指尖—目标抓握及底盘正常支撑接触不罚，非法接触用于奖励和统计。普通碰撞不在控制器前阻止动作，也不回滚仿真状态。
- 有限残差或一次触及限制不直接等于 IK 无解；记录跟踪误差、限幅及持续无进展，依据实际失败再处理。
- IK 的 q 结果送入关节位置伺服，再由 MuJoCo stepping 执行。禁止直接把 IK 积分结果写进正在运行的仿真状态当作物理动作。

Replay 的 `action` 存 **Actor 提交到环境的归一化 7D 指令**，Q 学习该指令经固定控制器和约束后得到的回报。另存实际关节命令、TCP 位移及限幅信息用于诊断，不用实际位移替换 replay 动作，否则会使 Actor loss、log probability 与 Q 的动作语义不一致。

## 6. 网络、奖励与 SAC

Actor：`MLP(GAT(G) || arm_and_task_state) → mu, log_std`。

双 Critic：`MLP(GAT_i(G) || arm_and_task_state || action) → scalar Q_i`。

首版 Actor、Q1、Q2 使用相同网络结构但独立参数；两个 target Critic 连同各自 encoder 一起软更新。共享“同一 Actor”是指所有站位共用一套策略参数，不要求 Actor/Critic 共用 encoder 权重。

按标准 Continuous SAC 实现用户给出的 target、critic loss、actor loss，并自动更新 α；需要 tanh 后 log probability 的 Jacobian 修正。现代 SAC 不必额外训练一个 V 网络。[SAC 公式说明](https://spinningup.openai.com/en/latest/algorithms/sac.html)

基础奖励按用户提出的六项：位置进度、姿态进度、非法碰撞、限位、控制失败、真实抓取成功。权重在控制闭环后调整，不搬用原论文的底盘任务奖励系数。

抓取目标变换明确后，固定其定义用于进度奖励；抓住后只靠接近奖励不能提供抬升方向信号。先保留终局成功奖励验证可学习性，若策略停在接触而不抬升，再针对实际失败增加抬升进度项；不可直接把“靠近 / 闭爪 / IK 解存在”当成功。稳定保持成功奖励仅发放一次并终止。

复用现有 `strict-pick-v3` 的任务事实：双指有效接触、物体脱离支撑、抬升至少 5 cm、稳定保持 2 s、滑移与固定部件漂移符合阈值；训练的在线成功判定与最终评估应复用同一逻辑。违法环境接触与合法抓握接触分别处理。

普通非法碰撞产生惩罚并继续；另行记录 episode 是否全程无非法碰撞。验收同时报告真实抓取成功率、无非法碰撞抓取成功率及碰撞率，不把现有 strict-pick 的接触遥测规则静默改为一碰即失败。

需正视奖励与站位评分的混杂：`d_prev-d_now` 的累计值依赖初始距离，直接比较 shaped Q 可能奖励“初始更远但走了更多距离”。折扣下该差分也不是自动满足策略不变性的势函数塑形。首版保存各奖励分量并做 Q—成功率验证；若相关性受塑形主导，再比较稀疏任务回报或显式势函数方案。即使用势函数塑形，跨初态比较仍需考虑初态势值偏移。

## 7. 地图定义与解释

固定 checkpoint、观察归一化、控制器、Home、场景和剩余时间初值。对每个合法站位恢复统一初态，Actor 采样 K=16 个动作：

`q_k = min(Q1(s_i, a_k), Q2(s_i, a_k))`

`q_mean = mean(q_k)`，`q_std = sqrt(mean((q_k-q_mean)^2))`。

这里 K 是同一初态的动作 Monte Carlo 次数，不是 K 次完整 rollout。训练完成后出图以批量网络推理为主，首次实现保留必要的初态物理检查，不对每个动作跑 IK / 抓取来替代 Critic。

以下解释边界必须保留：

1. `q_mean` 是策略条件下的 soft-Q 代理分数。Q 已受未来熵、奖励尺度、时间预算、折扣及控制器影响；它不是成功概率，也不是策略无关的物理可达性证明。
2. `E[min Q]` 与 SAC 的 `V_soft = E[min Q - α logπ]` 不同；不额外减去当前动作熵也不意味着 Q 中没有未来熵。
3. `q_std` 衡量同一 Critic 对策略所采动作的分数离散程度，不等于模型认知不确定性或真实抓取稳定性。高均值低方差仍可能整体估计错误。
4. 低 Q 表示当前策略 / 价值估计不看好该点，不能推出其他策略不可能成功。地图随 checkpoint 变化；单张地图不能在生成中更新模型。
5. 相同 checkpoint 内可比较原始分数；跨奖励、控制器或训练阶段不能直接比较数值。图内 min-max 仅用于颜色，常量图单色；跨场景对比应使用固定标尺，禁止把归一化值标成成功率。

每点至少保存 `(x,y,yaw), valid, invalid_reason, q_mean, q_std, K`；地图元数据保存场景 / 目标引用、checkpoint、配置、seed、Home、任务与动作约定。不同朝向保留完整三维数据；二维图明确展示某一 yaw 或 `max_yaw(q_mean)`，后者附带最佳 yaw，不能无说明平均朝向。

## 8. 验收条件与仍待实验确定的内容

| 层次 | 必须取得的证据 |
|---|---|
| 固定底盘环境 | 真实物理 stepping；可重复 Home / 夹爪 / 目标状态；底盘保持；连续宽度有效 |
| Cartesian 控制 | 同一 TCP 基坐标增量在不同底盘 yaw 下语义一致；关节位置/命令速度与执行器力限有效；DLS 真实完成抓取抬升，不要求轨迹预检无碰撞 |
| SAC 软件 | 动作与 logπ 有限，双 Q / target 更新正确，终止 mask 正确，变长图 batch 对齐 |
| 策略学习 | 小规模简单场景学出真实抓取成功，再训练场景随机化；失败原因、回报分量和成功率可追踪 |
| 地图有效性 | 首轮在 val103-cup30 的独立验收站位，对高、中、低分及均匀站位做重复 rollout；报告 Q 与成功率 / 回报排序相关性、Top-k 成功率和覆盖率；多场景扩展另按源场景/物体拆分 |
| 证据边界 | Q 推理速度、物理准备时间、训练吞吐分别计时；失败结果如实保留，不以热图外观代替验收 |

实测验证使用冻结 Actor 的随机策略，与 K 次采样口径一致；若另外展示 `tanh(mu)` 确定性执行，单独报告。简单几何距离排序和均匀选点作为最低成本对照；cuRobo 结果可用于辅助对照，但不是监督标签或候选过滤器。

建议首先采用 20 Hz policy、0.002 s physics、decimation=25、20 s episode、1 m 半径、0.1 m 网格、16 个 yaw、K=16。物理步长相对旧方案有调整，需要重验伺服与接触。这些不是已验证最优参数；完整初值、实施顺序与建议验收目标见方案，本轮不承诺训练时长或最终成功率。

## 9. 本地核对入口

- [Grand_RM 项目说明](../../../reference_repos/Grand_RM/README.md)；论文文件位于同目录，名称为 `Jiang 等 - 2026 - Graph Reinforcement Learning-Based Reachabil.pdf`。
- [参考图编码](../../../reference_repos/Grand_RM/learned_robot_placement/utils/scene_graph.py)、[参考 Actor/Critic](../../../reference_repos/Grand_RM/learned_robot_placement/scripts/train_task_fetch.py)、[参考训练配置](../../../reference_repos/Grand_RM/learned_robot_placement/cfg/train/FetchMultiObjFetchingGAT.yaml)。
- [Panda 位置控制](../../../src/lastmile_dataflow/runtime/control/panda_omron.py)、[RoboCasa 控制](../../../src/lastmile_dataflow/runtime/control/panda_robocasa.py)、[当前抓取判定](../../../src/lastmile_dataflow/runtime/outcomes/pick.py)。

## 10. 训练监控补充（2026-11-09 用户确认）

训练过程记录六项核心指标，并随有效 transition 数更新：

| 指标 | 观察什么 | 目的 |
|---|---|---|
| Actor Loss | 策略优化变化 | 观察 Actor 更新 |
| Critic Loss | TD 误差变化 | 观察 Critic 学习稳定性 |
| Entropy | 策略随机性 | 观察探索程度；口径为 tanh 策略动作的 `-E[logπ]` |
| Evaluation Success Rate | 独立 rollout 成功率 | 判断 Actor 是否学会操作，保存实际分子、分母与基础设施失败 |
| Episode Return | 每回合累计奖励 | 判断整体奖励表现，保留原始 episode 记录 |
| Value-Success Correlation | 冻结 Critic 预测值与站位实测成功率的相关性 | 判断 Critic 是否学会评价站位，使用 Spearman；常量结果记不可计算 |

必须输出四组图：

1. Loss 曲线：Actor Loss、Critic Loss。
2. 探索曲线：Entropy。
3. 训练效果曲线：Evaluation Success Rate、Episode Return。
4. 站位评价图：Critic Value Map + Value-Success Scatter。

训练期间的独立评估使用未参与训练采样的 validation 站位，固定点和 seeds，用于观察与选 checkpoint；最终 acceptance 集合继续留给独立验收。Value Map 保留 yaw 语义、invalid mask 和固定色标，Scatter 使用该 checkpoint 的真实 rollout 结果；未运行评估时不填造成功率或相关性。
