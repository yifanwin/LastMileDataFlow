# 并行专项：多 GPU 规划与 CPU 独立试验流水

- 状态：draft
- 日期：2026-10-24
- 替代：无；资源探究与方案比较保留，具体规划/执行耦合已整合进总方案
- 关联：[当前需求](../requirement/02-panda-omron-time-optimization.md)、[总加速方案](01-panda-omron-time-optimization.md)、[当前状况](../info/01-current-status.md)

**建议采用“GPU 常驻规划进程 + CPU 独立 trial 进程池 + 动态站位调度”。** 当前资源足以把单场景 20 点×3 次中的物理执行并发上限提高到 60；GPU 负责同站位三次闭爪前 batch 和闭爪后的独立规划，不要求一张 GPU 绑定一个物理试验。

当前统一实现口径见[总方案](01-panda-omron-time-optimization.md)：按每次运行所选 GPU 启动 G≥1 个常驻规划进程，CPU 池独立配置且最多 60。下文机器采样中的六卡配置仅为资源示例，不是必需进程数；本专项保留资源依据与方案比较。

只增加 `--station-workers` 可以先用满站位并行，但每点三次仍串行，而且多个进程各自维护 CUDA 和 solver。更快的流程需要把 GPU 规划等待与 CPU 物理执行分开。以下是源码、资源和官方接口支持下的设计建议，尚无对应 Panda 性能实测，不声明 60 倍加速或最优进程数。

## 1. 当前机器能提供什么

2026-10-24 本轮只读查询结果；GPU 占用为瞬时状态，正式运行重新选择空闲卡。

| 资源 | 查询结果 | 对并行的意义 |
|---|---|---|
| CPU | 256 个逻辑 CPU、128 个可见物理核；2 个 NUMA 节点，各 64 个物理核 | 可使用几十个独立物理进程，优先使用不同物理核 |
| CPU 允许范围 | 当前进程可访问 CPU 0–255、NUMA 0–1；可见 cgroup 未显示有限 CPU quota 或内存上限 | 没有发现必须把并发限制在少量核的配额 |
| 内存 | 总计约 743 GiB，available 约 726 GiB，无 swap | 可以用进程隔离换取 Python 并行，不必先做复杂跨进程模型共享 |
| GPU | 8×NVIDIA RTX PRO 5000 72GB Blackwell；每卡报告 73,415 MiB | 显存总量充足，但单个 solver 的实际占用仍未知 |
| GPU 0 / 3 | 已用约 21,233 / 20,423 MiB，利用率 90% / 88% | 本轮建议资源配置避开这两张卡，不干预已有进程 |
| GPU 1 / 2 / 4 / 5 / 6 / 7 | 每卡显存使用 0 MiB、利用率 0% | 本次配置示例使用这 6 张卡；这是采样时的选择，不写死到实现中 |
| NUMA / GPU 拓扑 | GPU 0–3 对应 NUMA 0，GPU 4–7 对应 NUMA 1 | CPU 执行及模型加载尽量靠近所属 GPU 的 NUMA 节点 |
| 项目磁盘 | 本地 NVMe 文件系统，可用约 3.0 TiB | 可以优先写数值轨迹，避免在线压缩和图片目录争用；未测持续写带宽 |

来源为 `free -h`、`lscpu -p=CPU,CORE,SOCKET,NODE`、`/proc/self/status`、当前 cgroup、`nvidia-smi --query-gpu=...`、`nvidia-smi topo -m`、`df -h`。沙箱内 NVIDIA 查询失败，随后通过只读扩权查询取得 GPU 数据；未运行 GPU 计算或修改系统配置。当前 worktree 仍是 CPU PyTorch 环境，资源可用不代表 CUDA 规划依赖已准备好。

## 2. 当前并行为什么还不够

| 当前源码行为 | 限制 |
|---|---|
| `collection._run_dataset()` 外层逐场景循环 | 场景间有屏障，前一个场景尾部未结束时，后续场景不能填补空闲资源 |
| `_parallel_station_records()` 一次只处理一个场景 | 每场景重建/关闭 worker，不能跨场景保持 worker 与模型缓存 |
| `_station_records()` 在一个 worker 内循环全部 trial | 同一点的 3 次物理执行串行；20 个 worker 最多推进 20 个 trial |
| `_station_worker()` 同时拥有 GPU planner 和 CPU 仿真 | 做物理时 planner 空闲，等待规划时这个进程的物理工作停止 |
| worker 按 GPU 列表轮转分配 | 支持多个 worker 共卡，但不管理每卡的规划队列、模型生命周期或 GPU 查询排队 |
| trial 创建 `ContinuousContext`、recorder 和 planner | 需要把独立状态与可共享资源拆清，增加进程数量本身不会消除重复 setup |

源码入口：[标定编排](../../../src/lastmile_dataflow/workflows/collection.py)、[实际操作](../../../src/lastmile_dataflow/runtime/execution/manipulation.py)、[执行上下文](../../../src/lastmile_dataflow/runtime/execution/context.py)、[规划适配](../../../src/lastmile_dataflow/planning/curobo_v2.py)。现有 spawn 与共享故障停批逻辑保留，不重建另一套结果或执行链路。

## 3. 三种并行方案的取舍

| 方案 | 同时推进物理的上限，单场景 20×3 | 优点 | 代价与结论 |
|---|---:|---|---|
| A：增加站位 worker，例如 20 个，分到 6 卡 | 20 | 现有队列已支持，增量最小 | 三次仍串行，GPU/CPU 绑在同一进程；可作为现有能力，不是最终加速设计 |
| B：每个站位内 3 个 Python 线程 | 理论 60 | 可在进程内共享只读模型 | 接触扫描、判定和记录仍有 Python/GIL 开销，planner 对象也不适合线程并发修改；不选作主线 |
| C：G≥1 个 GPU 规划进程 + 最多 60 个 CPU trial 进程 | 最多 60 | 规划和执行流水，CPU 进程绕开 GIL，每卡集中拥有 CUDA/solver，减少 trial 串行和尾部等待 | 已整合进总方案，规划请求/响应适配仍待实现 |

MuJoCo 3.5 支持只读模型与独立 MjData 的并行方式，Python 绑定调用底层函数时会释放 GIL；但本项目每个子步还会回到 Python 做接触、任务判定与记录。因此本方案优先用独立进程扩展完整闭环，而不是假设 native stepping 释放 GIL 就能让全部 Python 工作同时执行。[MuJoCo 3.5 多线程说明](https://mujoco.readthedocs.io/en/3.5.0/programming/simulation.html#multi-threading)、[Python 绑定与 GIL](https://mujoco.readthedocs.io/en/3.5.0/python.html#functions)

## 4. 推荐结构：规划集中拥有，物理独立推进

```mermaid
flowchart LR
    S[父进程：场景与站位队列] --> P[站位准备：初始化和静置一次]
    P --> G[GPU 规划进程 × G：至少 1 个]
    G -->|同站位 3 次规划分别返回| C[CPU trial 进程池：最多 60]
    C -->|闭爪后实测状态与规划请求| G
    G -->|对应 trial 的后续计划| C
    C --> T[各 trial 专属目录：规划与实测轨迹]
    C --> R[父进程：唯一写 trials.jsonl]
    T --> V[标定结束后按需并行生成视频]
```

### 4.1 父进程只管理计划、分配与结果

- 预先确定场景、站位、三个 seed 和 rollout 身份；与 worker、GPU、batch slot 和返回顺序无关。
- 站位准备形成一个明确初态，三次试验各自创建新 Simulation，不各自重复静置。
- 有计划的 trial 进入 CPU 池，规划无解直接保留该 trial 的真实规划结果，不占用物理执行 slot 或生成假轨迹。
- 一个 trial 完成后立即让出 slot；不等待同站位其他两个 trial 才发新任务。站位/场景汇总在本组有效结果齐全后计算，其他场景可继续执行。
- 已构建数据集中的多个场景进入同一调度器，保持 worker 跨场景常驻。这里不要求边写未完成场景库边标定；先完成 `build-scenes` 的场景库，再对 accepted 集合统一调度。

### 4.2 每张所选 GPU 一个常驻规划进程

GPU 进程独占自己的 cuRobo 对象、CUDA 上下文和日志回调，顺序领取规划查询；不同 GPU 同时工作。它服务多个 CPU trial，CPU 进程不创建 CUDA 上下文，也不传 CUDA tensor 跨进程。

使用 spawn，子进程设置物理 GPU 可见性后才初始化 CUDA；进程内部使用其本地设备编号。不要在父进程初始化 CUDA 后 fork，也不使用多个线程并发修改同一个 `V2Planner`。这与 PyTorch 2.7 对 CUDA 子进程使用 spawn/forkserver 的要求一致。[PyTorch 2.7 多进程说明](https://docs.pytorch.org/docs/2.7/notes/multiprocessing.html#cuda-in-multiprocessing)

一次请求只传模型/准备状态引用、trial/阶段身份和必要数值状态；返回关节名、参考变换、采样时间间隔和计划点。大型模型从固定场景文件加载并缓存，完整轨迹留在所属 CPU worker，不反复在队列中复制整个 Python Simulation 或全部图像。

闭爪后的请求带该 trial 当时的实测状态、锁定关节与附着信息；GPU 进程更新的是非执行的规划视图/MjData，不能恢复或改写正在运行的 CPU Simulation。计划返回后，原 CPU 实例从当前状态继续 follow、hold 和任务判定。

规划队列优先处理已进入执行、正在等闭爪后计划的 trial，再处理新站位的闭爪前 batch；同优先级按入队顺序处理。这样减少大量“已经动起来但卡在抬起前”的尾部等待。GPU 进程不等待物理任务完成才接下一查询。

每卡先只并发一个 native 查询，复用兼容的静态资源；GPU 进程数量少不等于每卡只服务一个 trial。跨站位世界与锁定配置不相同，缓存按实际兼容条件使用，不能把显存充足当作可直接复用状态的依据。保留闭爪后必要的独立模型构造，不将其称为已消除的 setup。

### 4.3 CPU 池一进程拥有一条活动试验

每个 CPU 进程调用现有 runtime 的控制、substep check、任务 monitor 与 recorder，仅把规划调用改为上述请求/响应；仍是现有 `run_curobo_attempt` 的执行职责，不新增第二个执行器。

每个进程分别加载/缓存只读 model，试验有自己的 MjData 和控制状态。spawn 进程之间不宣称共享同一个 Python MjModel；当前内存足够，先用私有模型换取简单隔离，不增加自定义模型共享内存框架。进程复用模型，但每次独立 trial 创建新的 Simulation，绝不重置已开始的实例。

物理执行保留所属 profile 的 2/4 ms 子步与原任务证据。多个 trial 的模拟时间彼此独立，不需要每个 tick 全局同步；某个 trial 等 GPU 响应时保持其模拟时间，其他 trial 继续推进。

## 5. 原生 batch 怎么与多进程配合

**同站位三次闭爪前 batch 是规划维度的并行；三条物理轨迹进入不同 CPU 进程是执行维度的并行。两者同时使用。** batch 完成后不再在一个站位 worker 内依次执行三条轨迹。

保持 `max_batch_size=3`、共享世界 `multi_env=False`，三个独立 seed/随机样本。相同站位的初态、参考坐标、目标与锁定配置相同，适合这一分组；不同站位或不同闭爪后状态不直接拼成同一个共享世界 batch。

本地 `reference_repos/curobo` 的固定 v0.8.0 源码表明：batch `plan_pose` 默认重试为 1，单问题默认 5；`plan_grasp` 未直接暴露重试参数。项目仍须适配逐阶段预算及逐 trial 随机流，明确保持 32 IK / 4 TrajOpt seeds 和既定候选数。`multi_env=True` 会失去原生 PRM 路径搜索，不用扩大 batch 的名义静默改变搜索方法。具体接口见[总方案第 4 节](01-panda-omron-time-optimization.md#4-静态资源常驻与同站位三次-batch)。

闭爪后先按每 trial 实测状态分别请求规划。即使它们来自同一个 batch，物理接触后也不再保证锁定值、目标姿态或附着变换相同。更大的跨站位 batch、同卡多 CUDA 进程或多 stream 均不作为本轮必须项：它们不自动带来更高吞吐，且会扩大状态适配范围。CUDA Graph 继续排除。

## 6. 当前资源下的配置建议

以下为实现后的起始资源配置，不是已经存在的 CLI 参数，也不是经过性能搜索得到的最优值。

| 资源池 | 建议规模 | 说明 |
|---|---:|---|
| GPU 规划 | 按所选 GPU 启动 G≥1 个进程；采样时可选 1、2、4、5、6、7 共 6 卡 | 一进程一 GPU；单卡和多卡沿用同一队列/协议，不强制占用全部空闲卡 |
| 单场景活动站位 | 最多 20 | 三次初态共用，任务身份稳定；跨场景允许继续补队列 |
| CPU 物理执行 | 最多 60 进程 | 对应单场景最多 60 个独立 trial；只有实际可执行的任务占 slot |
| 公共准备/构建 | 最多 12 进程 | 构建阶段使用；标定阶段用于站位准备，最终候选数较少时不强制开满 |
| Python/数值库内部线程 | 每个执行进程先设 1 | `OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`；GPU 进程的 PyTorch CPU 线程也显式限制，避免进程×线程超配 |
| 离线视频 | 标定完成后按所选渲染 GPU 启动 1 个或多个 renderer 进程 | 一 GPU 一进程，按 rollout 分任务；可与规划使用不同卡数，编码线程限量，不占标定计时 |

以六卡示例计算，60 个物理 worker 加 6 个 GPU 提交进程和最多 12 个准备进程，通常仍少于 128 个可见物理核；G 改变时重新核算并保存实际资源配置。这只是初始核预算，不保证每进程恒定只用一个核。亲和性按 `lscpu` 的 CORE/SOCKET 映射分配，不能把 128 个逻辑编号直接当作 128 个独立物理核。

NUMA 0 有两张采样时空闲的卡，NUMA 1 有四张：可先分别安排约 20 / 40 个 CPU trial worker，准备进程按节点剩余核分配。尽量在设置 CPU 亲和性后加载模型/分配数组，让内存靠近实际使用节点；不要为了固定比例让一侧有空闲而另一侧排长队，跨节点调度仍允许。

### 内存与写入用在哪里

充足内存优先用于模型常驻、准备初态和完整数值轨迹。若一条轨迹保存 6,000 帧、每帧 2,000 个 float64 状态量，则积分状态约 91.6 MiB，60 条约 5.36 GiB；这是尺寸示例，不是当前 Panda 的实测规模，也不包含模型、接触缓存、动作和诊断。

CPU worker 在专属 attempt 目录写自己的规划/实测轨迹，父进程只收小型结果并写 `trials.jsonl`。避免把 60 条完整轨迹汇集到父进程再串行压缩。NVMe 和空间充足时，数值 NPZ 可以先采用不压缩保存，减少试验收尾 CPU 成本；具体字段沿用唯一轨迹文件，不额外存重复全状态副本。实际写完才报告记录完整，排空计入总墙钟。

GPU 进程不无限缓存所有场景/站位 solver。按正式运行实际分配的显存保留活动资源，结束站位及时释放重型对象，兼容的静态内容继续复用。没有单 solver 显存测量，不能承诺每卡固定装下多少个完整 planner；本方案用多个 CPU 进程增加执行宽度，无需按 trial 数复制 GPU planner。

## 7. build-scenes 与视频怎样并行

构建优先按房屋/任务/变体分工作单元，用最多 12 个 CPU 进程独立处理；同一变体内的放置事务保持明确顺序，避免“哪个提案先返回就接受哪个”改变原 seed 的选样含义。目标发现可并行算独立候选，但按原任务顺序汇集后执行既有空间选择。

候选物理资格可按固定候选索引分片，最终汇集全部物理结果后再覆盖选点。可见性仅对最终点记录，用独立 EGL renderer worker 处理，不让所有纯物理进程创建 renderer。父进程唯一分配最终目录/ID 并写 `scenes.jsonl`，worker 的事务和 rejected 原因沿用现有实现。

离线视频按 rollout 分任务，每个 renderer 进程复用同场景模型和相机资源，从完整实测状态逐帧 `mj_forward` 后渲染；不重新规划或积分。每个输出视频只有一个写入者，不按帧跨进程来回复制 RGB。成功、失败和超时的实际执行均可回放；无执行的 trial 不补执行视频。视频源数据的写入计入标定就绪，事后渲染与编码单列时间。

## 8. 能快多少，哪些不能并行

对物理时间相同的 60 条轨迹，假设每条执行耗时为 E，忽略规划与启动：

| 调度方式 | 理想执行时间 |
|---|---:|
| 1 个 worker 逐 trial | 60E |
| 20 个站位 worker，每点三次串行 | 3E |
| 60 个 trial worker | E |

这里的额外空间是**相对已经开满 20 个站位 worker，物理执行部分最多再减少到约三分之一**；不是整条链再快 3 倍。单条轨迹内部仍按时间顺序执行，所选 G 张 GPU 仍需处理全部规划请求，单卡供给计划较慢时也可能无法同时填满 60 个物理 slot，长 trial 仍决定尾部。

整个场景时间至少受以下量共同约束：`GPU 总规划工作 / 可用 GPU 数`、`CPU 总执行工作 / 活动执行进程数`、最长单 trial 的阶段依赖、初始化和必要 I/O。流水可重叠不同 trial 的规划与物理阶段，不能把所有分项都简单除以 60，也不能把 GPU batch 的倍率再与进程数机械相乘。

`mujoco.rollout` 的原生线程池适合已给定初态和控制序列的 rollout；当前链路有闭环跟踪、逐子步接触判定、闭爪后重新规划和提前终止，直接换成整段开环调用会丢掉这些职责。本轮保留 CPU MuJoCo 原执行语义，不迁移 MJX/MJWarp，也不跳过逐步判定换吞吐。[MuJoCo 3.5 rollout 接口](https://mujoco.readthedocs.io/en/3.5.0/python.html#rollout)

## 9. 实现位置与统一验收

| 改动 | 放置位置与约束 |
|---|---|
| 跨场景常驻队列、站位准备、独立 trial 分发 | `workflows/collection.py`；复用现有 spawn/编号/汇总与故障停批 |
| GPU 查询拥有者、规划视图、数值请求/响应 | `planning/` 内的现有 V2 适配职责；保持 cuRobo 0.8.0，不创建独立外部服务或新 CLI |
| 物理与规划等待解耦 | `runtime/execution/`；继续原 follow、close、hold、monitor 和执行回调 |
| 唯一实测轨迹与回放依赖 | `data/recorder.py`，视频后处理放 `data/rendering/`；不新增第二套 trial/轨迹 |
| 构建独立单元并行 | `workflows/scene_datasets.py` 配合现有 construction 事务；父进程写主记录 |

排队等待从每 trial 的 300 s 活跃操作预算中单列；每个原生规划调用的完整耗时计入参与该 batch 的每个 trial，不按 batch 大小摊薄。真实墙钟包含准备、排队、执行、写入和收尾。共享 GPU/模型故障沿用停批，不继续消费失效规划资源；已执行的记录仍保留。

每点仍为 3 次，低率是 0/3、1/3，高率仅 3/3，2/3 为中间组。GPU 查询完成、CPU 进程退出与任务成功分别报告，不完整结果不补失败。

按用户既定顺序，完成全部流程优化和软件回归后直接统一全量实验，不先跑 Panda 基线、局部试跑或进程数 A/B。正式运行一次性保存 GPU/CPU 活跃时间、规划排队、ready-to-run 等待、执行和 I/O 时间、每点有效覆盖与全场墙钟。没有对照数据就报告绝对耗时；若某池耗尽资源或发生实际错误，再依据正式记录修复，不预设加速倍率。

## 本轮探究结果

本轮完成资源只读查询、现有实现与固定版本接口核对、官方并行资料检索，提出方案 C 作为利用当前 CPU/内存/GPU 的进一步实现方向。新增本专项说明并从总方案和当前状况链接；检查文档链接及格式。未改运行代码、安装依赖、启动并行试验或执行 Panda rollout；规划/执行拆分与 60 个活动 CPU trial 均是待实现能力。
