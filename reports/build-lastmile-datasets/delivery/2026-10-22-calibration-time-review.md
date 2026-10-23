# cuRobo 标定耗时复核与 30 分钟优化建议

- 状态：done
- 日期：2026-10-22
- 替代：无
- 关联：[原耗时分析](../analyze/2026-10-20-scene-datasets-and-calibration-cost.md)、[更新方案](../plan/2026-10-22-time-optimization-plan.md)

**建议把主线改成“成功轨迹保留视觉数据 + 单场景内并行 + 保持逐物理步判定的采集优化”。仅复用规划器，无法达到每场景 30 分钟。** 原方案把部分重叠收益相加，规划器调用结构也判断错了；1.5–1.8 小时只是旧估算，当前没有对应的优化后实测。

本轮用户确认：仍按 **20 站位 × 5 trial** 评估 30 分钟目标，成功 rollout 需要用于视觉训练。暂按“统计和成功轨迹图像全部就绪”计完整交付时间；同时单列统计就绪时间，不能把图像生成移出计时后宣称达标。

## 1. 原始记录说明，瓶颈会随执行比例变化

重新汇总 trials.jsonl、逐 rollout 的 execution_diagnostics.json 和规划器目录，得到以下事实。每行是一次既有运行，没有把不同配置当成配对优化实验。

| 运行 | trial / 成功 | 总耗时 | 规划器构造 | 抓取 + 运动规划 | 执行、证据与同步图像 | 其余 |
|---|---:|---:|---:|---:|---:|---:|
| small3 / RBY，FOV 开 | 100 / 18 | **8,272.9 s** | 2,216.0 s，26.8% | 1,624.7 s，19.6% | 2,840.2 s，34.3% | 1,592.0 s |
| full-calibration-02 / RBY，FOV 关 | 100 / 78 | **11,449.2 s** | 825.7 s，7.2% | 1,765.5 s，15.4% | **8,601.2 s，75.1%** | 256.7 s |
| full-calibration-02 / Panda | 40 / 0 | 2,773.5 s | 159.8 s，5.8% | 295.3 s，10.6% | 2,255.7 s，81.3% | 62.7 s |
| eval_ds / 4 个 open 场景 | 225 / 0 | 3,984.5 s | 3,522.8 s，88.4% | 425.4 s，10.7% | 0 s | 36.2 s |

“其余”包含任务校验和分项计时之外的时间。总耗时含场景收尾；执行项不能再称为纯物理耗时。open 这一批实际仿真推进为 0，主要测到了规划失败成本，不能代表真实开门执行。

RBY 两轮的站位、头部姿态和 FOV 配置等有差异，**不能把成功数或构造时间变化归因于某一项优化**。原分析中的单一占比不能直接外推所有场景。读取成功数必须使用 trials.jsonl：当前 summary.json 的 successes 是连续采集成功数占位，evaluate-stations 中固定为 0，不是标定成功数。

原始输入：

- [small3 标定](../../../.worktrees/refactor-light/outputs/station_evaluations/small3/curobo_v2_001/summary.json) 的仓库相对实际路径为 `.worktrees/refactor-light/outputs/station_evaluations/small3/curobo_v2_001/`。
- RBY：`.worktrees/model-train/outputs/rby1-full-calibration-02/`。
- Panda：`.worktrees/model-train/outputs/panda-full-calibration-02/`。
- open：`outputs/station_evaluations/eval_ds/curobo_v2_001/`。

以上路径均相对 LastMileDataFlow 根目录。工作区产物不作为可搬迁报告附件。

## 2. 原文需要纠正的地方

| 原判断 | 核对结果及影响 |
|---|---|
| 每 trial 构造两次，闭爪前后各一次 | small3 实际是 **174 次闭爪前 + 24 次闭爪后**。失败会切换手臂，多数 trial 根本没进入闭爪后阶段。 |
| 缓存后构造次数约 198 → 118 | small3 实际涉及 40 个站位/手臂组合；若每组合只构造一次，理论计数为 **40 + 24 = 64**。按全体构造均值粗估可省约 1,500 s，未扣缓存切换与重新设 seed 的成本。另一轮 RBY 是 118 + 80，理论降到 120，粗估仅省 325 s。两者均未验证。 |
| 仅重新绑定 planner.sim 即可 | PlanningModel.sim、启用 FOV 时的 validator.sim 和 scratch data 也关联 trial；还需隔离日志、回调、附着状态及随机数生成器。原缓存 key 不含 seed，不能直接沿用构造时的随机流。原生 reset_seed() 无 seed 参数，只重置原有生成器。 |
| P2：4 ms → 50 ms，再比较差异 | strict-pick-v3 的 max_sample_gap_s 固定为 **0.0041**，50 ms 与 10 ms 都会触发 physics_trace_gap。维持现协议就必须保留逐物理步任务证据。 |
| P2 与 P3 分别省时，可以相加 | sim.step 的 _advance 在 finally 调用 recorder.executed → capture → render/PNG，它们均被外层 simulation_and_physics_evidence 包住。证据微基准也来自另一场景，不能把两项比例直接套入这一总项。 |
| record_physics=False，JSON 成本为零 | 没有压缩文件写入，但 canonical(row) 在传给 NullTrace.write 之前仍执行；全接触字典、名字查找和列表也照常构造。 |
| 闭爪后状态不同，因此任何内容都不可复用 | 必须更新实测状态、锁定关节、碰撞世界和附着变换；静态几何、球拟合和部分 solver 资源仍可研究复用。不能直接复用整对象，也不应据此排除部分复用。 |
| 按场景分 8 卡能让单场景时间 /8 | 只能提高多场景吞吐。单场景需要按站位或 trial 分任务；当前 CLI 的 --samples 只筛场景。 |
| 动态世界更新必然与 CUDA graph 冲突 | 原生支持更新世界和预留碰撞缓存；当前实现每次换网格名字、清 BVH 缓存，以及自定义代价中的 Python 状态分支，才是具体障碍。不能直接把开关改为 True。 |
| 527.5 + 8,272.9 s 是同一样本端到端实测 | build 来自 val534，标定来自 val103；相加只是跨场景示意估算。22 样本批次构建均值又是另一口径。 |

另外，small3 原始记录的 planning_no_solution / success 墙钟中位数分别是 **26.25 / 129.27 s**，goalset 中位数为 **3.58 s**。原文的 16 / 113 / 0.92 s 不属于这组汇总。超时中位数为 304.19 s，另一个 open 冷启动达到 413.71 s：300 s 是调用边界检查的软预算，不能写成严格上限。

原 P6 的“端到端下降 2%–4%”也不成立：若用批次构建 3,886 s、降至 1,000–1,500 s，再配标定 1–2.3 h，算术降幅约 **20%–39%**；这仍是跨样本估算，不是验证过的收益。

源码依据：[执行与计时](../../../src/lastmile_dataflow/runtime/execution/context.py)、[实际步进](../../../src/lastmile_dataflow/runtime/simulation.py)、[记录器](../../../src/lastmile_dataflow/data/recorder.py)、[任务证据约束](../../../src/lastmile_dataflow/runtime/outcomes/pick.py)、[规划器](../../../src/lastmile_dataflow/planning/curobo_v2.py)、[模型绑定](../../../src/lastmile_dataflow/planning/rby1m_mobile_torso.py)。

## 3. 30 分钟需要同时考虑延迟和资源

以 11,449.2 s 的 RBY 运行作为较接近“较多成功轨迹”的参考：

- 单进程需要 **6.36 倍**整体加速。
- 只把规划器构造完全消除，还剩 **177.1 分钟**。
- 即使把整个执行、证据和图像项变成 0，仍剩 **47.5 分钟**；再消除全部规划器构造，仍剩 **33.7 分钟**。

这些是时间账本的算术边界，不是实际可达到的配置。它说明单进程仅做一两个局部改动不足以达标。

下表把既有 trial 的墙钟时间作为任务长度，按原顺序放入空闲 worker。场景总时间与 trial context 时间之差按 trial 均摊；站位任务包含原来的 5 次 trial，seed 保持原 index 计算，不按 worker 重编号。

| 参考运行 | 4 worker，按站位 | 8 worker，按站位 | 8 worker，按 trial |
|---|---:|---:|---:|
| small3 / RBY | 36.7 min | 20.1 min | 17.8 min |
| full-calibration-02 / RBY | 50.4 min | **29.1 min** | **26.1 min** |

**以上仅为调度估算，假定并发不拖慢单个任务，未加入多进程启动、CPU/GPU/磁盘争用。** 按站位更容易保留 5 次 trial 间的资源复用，但 8 worker 的余量只有约 1 分钟；按 trial 平衡更好，缓存命中则更差。

建议先做简单的站位任务队列，每 worker 连续执行该站位原有 5 次 trial。先测 2 worker 的单任务耗时是否被拖慢，再决定用 4 卡还是 8 卡。4 worker 要达标，还需约 **1.7 倍**的单任务加速。8 worker 应留出余量，开发目标先定完整就绪 **25 分钟**，不是让 29 分钟估算贴着 30 分钟验收。

MuJoCo 主要执行在 CPU，增加 GPU 不会自动使 mj_step 加快。当前环境可见 208 个 CPU affinity 核；默认沙箱中 nvidia-smi 无法连接驱动，后续通过已授权的只读查询取得显存与利用率，其中 GPU 2 利用率为 N/A。不能据此承诺 8 卡实际可用或线性扩展；新工作区的 EGL 枚举也需单独核对。

## 4. 保留视觉训练数据，先改记录方式

### 4.1 优先评估：真实状态记录完成后，按成功结果生成图像

当前每个控制步已保存整场景 qpos、qvel、ctrl 和时间戳。可以让真实试验先完成物理与数值记录，再由专门的渲染任务读取**该次真实 trial 的状态序列**，仅为成功 trial 生成训练所需的全部相机帧。

这与“缓存一个计划代替后面 5 次试验”不同：100 次真实试验、seed、物理判定都保留，渲染不规划、不积分、不提供新的成败证据。渲染器使用独立 MjData，不操作已经 begin 的在线 Simulation。

实施条件：

1. 核对冻结模型、相机、光照及状态记录覆盖实际渲染所需的数据；不能未验证就认定现有 NPZ 足够。
2. 取同一真实 trial 的少量状态，对比现场采集与状态渲染的机器人相机图像、帧数和时间戳；先确认可还原，再切换主路径。
3. 成功 trial 的全部帧仍输出为原数据格式，图像来源明确记录；失败 trial 保留结果和数值诊断。
4. 渲染任务和后续物理 trial 流水执行，最终统计等待图像完成的总时间，不能只报告物理阶段时间。

收益取决于失败比例。small3 中失败/超时执行占 **4,071 / 8,838 tick，46.1%**；新 RBY 中失败占 **5,196 / 28,588 tick，18.2%**。这部分帧可避免渲染和编码；成功帧仍需生成，计算和 I/O 没有凭空消失。全局“关闭图像”的原 P3 不满足本轮要求。

### 4.2 若状态渲染不能保持图像一致，先保留现场渲染、延后 PNG 编码

在每个 trial 内暂存原始 RGB，判定成功后交给独立编码 worker；失败丢弃图像缓存。编码与下一个 trial 的规划/执行重叠，保持像素和帧序。原始缓存需有明确容量，不能在主进程无限排队。

以 4 路 320×240 RGB、约 300 tick 为例，每个 trial 约 **276 MB** 原始图像；这只是尺寸计算。评估低压缩级别 PNG 时，同时报告编码时间和体积，不假设只降压缩级别就足够。末尾编码排空时间也计入交付。

## 5. 逐物理步证据保留，但去掉重复工作

优先做三项不降低采样频率的调整：

1. **不写 physics trace 时，不构造完整 JSON 行、不序列化。** 任务 monitor 和仿真健康检查照常每 4 ms 执行。
2. **接触扫描合并。** 目前 contact_observations 与 PickMonitor.sample 都遍历全部 contact、读取接触力。每 substep 共用一次接触数值读取；geom/body 分类、固定名字和目标/手指集合预先计算。任务判定需要的承力、支撑、滑移及现有接触统计单位保持不变。
3. **把任务事实与详细遥测分开。** 任务事实保留 4 ms；文本化的全部接触名称和坐标只在实际需要 trace 时生成。统计若保留逐物理步口径，就仍在每步更新，不能偷偷降频。

不建议直接删 mj_forward：mj_step 先算动力学再积分，积分后的 qpos 与接触/派生位置未必对应同一时刻；mj_forward 还可能影响下一步使用的求解状态。重排采样位置可以作为后续单独实验，但须验证时间戳、力和姿态对齐，不能先报“可省一半”。[MuJoCo 官方仿真说明](https://github.com/google-deepmind/mujoco/blob/main/doc/programming/simulation.rst)

## 6. 规划器复用和 CUDA graph 放在第二层推进

先缓存不依赖 trial 的资源：机器人配置与派生模型、目标局部坐标中的球拟合、固定几何和可复用 BVH。目标拟合不能缓存当前“底盘坐标中的球”再直接用于另一个站位。

完整 planner 先限定在同站位/同手臂的闭爪前复用，显式处理模型绑定、seed、阶段状态和日志；每个站位结束即释放，避免缓存 40 个重型对象常驻 GPU。闭爪后的实测状态继续更新，部分 solver/几何资源复用另做验证。原生 Kinematics.update_kinematics_config 提供同形状参数更新入口，但要同步所有相关 IK/TrajOpt/FOV 模型，当前集成没有实现。

CUDA graph 可以改善规划部分，但需要先修自定义代价：contact_allowed、attached 目前是 Python 布尔分支，捕获后可能固定在捕获时的状态；仅切换 True 存在碰撞判定错误风险。应使用稳定地址的张量状态或明确区分捕获的阶段，再验证目标接触与附着切换。

cuRobo 的 use_cuda_graph 控制 solver 的 CUDA graph；warmup(enable_graph=...) 中的 graph 是 PRM 路径搜索，二者不可混淆。原生支持预留 collision_cache 供 update_world 使用，但当前逐次换网格名字并清缓存的 refresh_world 仍需调整。[cuRobo 官方 MotionPlanner 文档](https://nvlabs.github.io/curobo/latest/api/curobo.motion_planner.html)

## 7. 下一轮只验证三个会改变决定的问题

| 顺序 | 关键问题 | 最小验证 | 决定 |
|---|---|---|---|
| 1 | 能否推迟成功图像生成而保持训练数据一致？ | 一个真实成功 trial，检查实际所需状态和相机帧；分别计 render、PNG、收尾 | 成立则用状态渲染；否则现场采集 + 异步编码 |
| 2 | 执行项里到底谁占大头？ | 一个能进入执行的代表 trial，拆分 mj_step、额外 forward、monitor、遥测、render、encode；嵌套计时不相加 | 编码为主则先流水化；证据为主则合并接触扫描 |
| 3 | 单场景并行会不会明显拖慢单任务？ | 保留全局 station index/seed，用 1 / 2 worker 做同站位任务；确认 CPU 与磁盘争用，再扩大 | 决定 4 worker + 单任务优化，还是 8 worker |

每项跑前固定配置、seed 和比较口径，不需要先跑两套完整 100 trial。小验证支持方案后，再做一次完整 20×5 验收，报告统计就绪、图像全部就绪、worker 数、分配的 GPU 时长及原始 trial 结果。

GPU 非确定性和原有墙钟 timeout 要单独解释：去掉记录等待后，同样 300 s 能推进更多物理时间，原先 timeout 的 trial 可能继续完成。不能机械要求所有 trial 状态 99% 一致，也不能通过缩短超时或减少 trial 来掩盖耗时。

## 8. 本轮执行与交付

文档复核阶段完成了两份文档与当前源码核对、**240 条**既有 RBY/Panda pick trial（small3 100 + 新 RBY 100 + Panda 40）和 225 条 open trial 的分项复算，合计 **465 条**，并用既有时长做了调度估算。这一阶段只读取产物，没有重新运行物理试验，也没有修改运行代码或清理用户文件。

更新原耗时分析的错误口径，并将原方案改为本轮确认的目标。没有把已有 dirty/untracked 文档回退。验证采用原始记录复算、调用链检查和文档链接检查；纯文档修改不运行软件回归。

用户随后授权新工作区探索，已创建 .worktrees/curobo-time-optimization，分支 exp/01-curobo-time-optimization。[E01 探针](../../../.worktrees/curobo-time-optimization/reports/build-lastmile-datasets/delivery/E01-visual-recording-probe.md)在独立环境完成 15 张图像还原与内存 PNG 编码：压缩 1 比 6 的总编码时间约快 4.71 倍、体积增加 21.09%，解码无损；状态渲染未全部逐像素一致，先推进现场 RGB 与编码优化。它不构成完整标定加速或 30 分钟目标验收。

| 视觉候选 | 选择 | 原因 |
|---|---|---|
| 阶段对比与并行估算表 | 选中 | 精确展示不同配置和实测/估算边界 |
| 新的饼图或堆叠图 | 拒绝 | 执行项尚未拆开，画成独立组件会重复原来的归因问题 |
| 大型架构图、HTML 阅读版 | 拒绝 | 本轮是一次工程复核，表格和具体修改点足够 |
