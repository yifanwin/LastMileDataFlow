# PandaOmron 时间优化实施记录

- 状态：active
- 日期：2026-10-24
- 替代：无
- 关联：../plan/01-panda-omron-time-optimization.md、../plan/03-execution-schedule.md

## 当前结果

本 worktree 已完成 P00–P11 软件实现与回归，以及 P12 首阶段正式全量；P13 估时完成，P14 百来源构建已完成（183 accepted/37 rejected、54.29 min），全量 8,466 trial 标定运行中。首阶段 20×3 全覆盖，20 成功/38 执行前无解/2 执行后无解，225.458 s；成功与失败离线视频均解码通过。

实际复现命令与结果见[首阶段交付](2026-10-24-stage1-experiment.md)和[百来源交付](2026-10-24-val100-experiment.md)。以下分项为开发执行记录，早期“待完成”以当前结果及后续追加记录为准。

### 配置和环境

- 首阶段：`configs/scene_datasets/panda_omron_val103.json`，position-v1、val103/Cup_30/Case2、1 变体。
- 最终批次：`configs/scene_datasets/panda_omron_val100.json`，固定 val0–val99 共 100 来源，Case1–3、每房最多 1 个 pick/1 个 open、每任务/Case 1 变体；没有跨房复制 Cup_30 限定。实际无合格目标及 rejected 原因必须保留，运行时使用 `--allow-partial-targets`。
- Panda 两种 profile 的 execution/lastmile 配置均为每点 3 次，阈值 1/3、1.0；另提供 `configs/benchmark/panda_omron.json`。RBY/通用配置未覆盖。
- 数据根改为本机 `/data1/wenyifan/molmospaces_data`。首阶段及最终 100 来源 XML、metadata、机器人 XML 与资产库路径检查通过。
- 独立 `.venv`：MuJoCo 3.5.0、cuRobo 0.8.0、PyTorch 2.7.1+cu128。沙箱外依赖加载检查确认 CUDA 可用、8 张 GPU 可见；采样时各卡空闲。此检查没有创建 planner 或执行任务。
- 本机 EGL 动态库需要 `LD_LIBRARY_PATH=/data1/wenyifan/opt/lib`；CUDA 编译工具目录为 `/data1/wenyifan/opt/cuda-12.8`。实际求解/JIT 尚待正式运行验证。

### 状态、准备与记录

沿用 AttemptRecorder 和实际控制步回调，增加整场景积分状态及 state_spec、完整控制目标、控制时钟余数、相机位姿/fovy/intrinsic/sensorsize；保存 MuJoCo 版本、固定模型引用和准备资料引用。修复 RBY 快照漏存 held targets 的问题。

`SimulationState` 只向新建、未开始实例初始化；`TrialIdentity`、规划请求/响应与活跃预算结构供后续双池使用，当前尚未接上累计预算。

Panda 启用每站位一次真实静置，再由三个 trial 分别创建独立 MjData。准备资料保存在 `preparations/<scene>/<station>/`，记录请求点、实际底盘、实测全场景状态与耗时。准备失效单列，没有伪造三次操作失败。连续导航采集仍沿用实际到达后的执行。

### 构建

Panda 配方启用固定任务局部模型。裁剪半径为站位半径＋机器人臂展＋底盘半径＋0.35 m 局部底盘移动＋0.5 m 余量。保留世界几何、完整支撑/相交对象、全部关节结构和结构几何；删除范围外完整物体根及不再引用的 mesh。房屋几何边界一次计算供所有任务复用，按名称迁移保留状态。

发现结果携带任务、支撑信息及已检查候选进入资格检查；资格扫描继续全部剩余候选，不再次 discover，也不重复检查前缀。增强后模型/状态已改变，重新检查全部候选。物理资格与覆盖选点完成后，只对最终站位记录可见性；完整候选和排除原因保留在场景记录中。

房屋发现及任务/Case/变体构建可使用 spawn 进程池，`--build-workers 1–12`。父进程统一写 `scenes.jsonl`；变体 seed/目录由任务身份确定，构建事务仍顺序执行。沿用最多 4 个资产组合的编译缓存；恢复局部模型后，增强从保存的局部 XML 接续，不重新展开整屋。临时构建目录改用 mkdtemp。

### 离线视频

`evaluate-stations --replay-from` 选择已有 run 和 rollout，使用独立 MjData 恢复完整实测帧并 mj_forward；没有规划或 mj_step。输出时钟按实测时间戳重采样，保留每个视频帧对应的原始帧索引。无执行步拒绝生成执行视频；回放输出使用新目录，不修改原始结果。

已修复浮点时间边界导致最后一帧重复的问题。当前 fixture 已验证机器人/物体状态、相机、时间戳与原活动实例独立；正式轨迹的实际视频验收尚未运行。

## 已执行的软件检查

- `tests/scenes`：37 项通过。
- simulation/recording/Panda recording/config/CLI 的一次组合检查：32 项通过。
- 公共准备及 collection：14 项通过。
- 实测回放：2 项通过；无 mj_step、原活动实例保持不变、非均匀时间戳正确。
- 构建编排及 construction：24 项检查通过，含 1 项既有 opt-in OpenGL 测试跳过；后续新增的实际 spawn 构建返回与候选前缀复用两项均通过。

以上为局部软件检查，代码仍在集成中，不代表 P11 全套回归或任何真实 Panda 成功。

## 剩余工作

P14：完成已启动的 8,466 trial 全量标定，生成该轮真实轨迹视频，汇总覆盖、原始结果、实际耗时及估时差异。首阶段结论不扩大到百来源。

## P06 适配进展

V2Planner 增加共享世界原生 BatchMotionPlanner 入口，闭爪前 goalset/pregrasp/grasp 各显式保留 5 次原生重试，CUDA Graph 关闭。每个 trial seed 创建独立原生 SeedManager，按 batch slot 组合随机样本；32 IK / 4 TrajOpt seeds 不变。批量结果按 slot 拆开，失败 slot 无可执行轨迹，整次批量调用时间记录到每条参与 trial，不除以 3。

原生 CPU SeedManager 检查验证：改变 batch 顺序后，每个 trial 的首批及后续随机样本仍相同；不同 trial 的样本不同。固定版本默认启用 LM-IK，已补上其逐 trial Halton 样本；保留原有 LM 搜索规模。batch fixture 还验证原生 GoalToolPose/JointState 张量维度、slot 归属和失败 lane 无占位执行轨迹。与 planner reuse/curobo 局部测试合计 14 项通过。这不证明真实 CUDA batch 求解通过；静态资源拆分和 GPU/CPU 池接入尚未完成。

离线回放另用简化 MuJoCo 场景实际渲染/编码/解码通过，产物为 `outputs/diagnostics/curobo-time/replay-fixture-_l2bth4e/`，4 帧、64×64，source 为 measured_state_replay；未使用 Panda 资产或规划器。

## P07–P10 双池接入

- 常驻规划进程按 GPU 列表 spawn；闭爪后 pose 查询优先，同级 FIFO，空闲资源优先复用相同模型。进程间只传数值状态、固定输入引用与 trial/query 身份。
- 准备任务有界，CPU 执行容量独立且最多 60；候选规划无解只通过原 recorder 保存初态和事实，不占物理执行 slot。父进程唯一追加 trials.jsonl，无逐场景完成屏障。
- CPU 原仿真实例经 PlanningClient 携实测状态请求后续规划，返回后复用原跟踪、monitor 和 recorder，不恢复/传送活动实例。CPU worker 的 CUDA_VISIBLE_DEVICES 为空。
- 预算从首次 batch 接续，GPU 整次调用耗时不按 batch 大小摊薄，物理执行单独计时；队列、I/O 和准备不消耗活跃预算，保留原墙钟与新协议版本。
- GPU 进程最多保留两个 solver family；同形资源通过原生 kinematics 参数更新锁定关节、限位和默认值，更新 LM/PRM 采样范围，刷新世界并保留 FK 检查。Panda 静态 URDF/球描述及场景局部几何缓存，目标球拟合保存在目标体局部坐标并按实测位姿变换。动态锁定参数仍由实际状态生成。
- evaluate-stations 与 collect-lastmile 的内置标定已共用全局调度；配置保存实际资源和标定记录模式。数值/视觉观测完整性分别报告，缺观测的数值成功不自动进入视觉训练索引。

局部检查：规划池单/多进程与共享停批 3 项；活跃预算和实测 RPC 连同原 outcomes/lifecycle 34 项；两场景单 GPU/3 CPU 的 12 条实际简化物理轨迹、无解仅初态和共享故障三项；命令接入与数值模态分离两项。资源复用与模型传播 fixture 检查通过。尝试在 CPU 原生构建锁定机器人配置时，固定版本要求 CUDA，不能把该检查写成通过；其真实数值和 solver 行为留到 P12 验证。

P11 全套回归正在执行，日志位于 `outputs/diagnostics/curobo-time/p11-software-regression.log`。尚未运行真实 Panda 构建或 rollout。

## P11 全套软件回归

第一轮 274 项有两个 FOV 测试夹具缺少新增的 base/控制 dt 元数据字段，补齐夹具后 12 项 FOV 局部检查通过。第二轮全套 `p11-software-regression-02.log`：274 项，272 通过，2 跳过（pi05-data 可选依赖、既有 opt-in OpenGL 构建检查）。两份日志均保留。

代码检查与命令帮助通过，P12 开始首阶段正式构建；尚无真实 CUDA batch 或 Panda 执行结果。

P12 的实际资源、简洁复现命令和结果统一见 `2026-10-24-stage1-experiment.md`。用户指定 GPU 6、7；构建使用 6，标定使用 6、7，不使用当前忙碌的 0–5。
