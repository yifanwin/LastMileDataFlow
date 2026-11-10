# mjlab / SAC 实施记录

- 状态：active
- 日期：2026-11-09
- 替代：无
- 关联：../plan/01-implementation.md、../requirement/01-fixed-base-sac.md

## 最新接手状态（2026-11-10）

完整训练统计与新尺度结果见[训练接手报告](05-training-handover.md)：已有12轮训练、约511万条保存交互，尚无策略抓取成功；`123bc88` 的4cm / 7.5° / 60s脚本批次成功43/128、无碰撞成功35/128。尚无新尺度训练结果。本次根据延迟成功奖励问题将工作区配置γ调为0.999，未做新训练。以下为此前分阶段记录，时间相关表述以接手报告为准。

## 历史当前结果（2026-11-09早期）

已在 actor-critic 工作区安装独立研究环境并实现 val103-cup30 场景准备、mjlab manager 环境、Base Frame DLS、连续夹爪、共享 strict-pick 保持窗口、图观测、GAT SAC / Replay、训练 / 评分 / 评估入口。上游与项目单环境真实 GPU stepping 已通过，32 环境和训练验收正在推进，**尚未获得新策略真实抓取成功、训练 checkpoint 或有效 Q 图结论**。

主 checkout 及其 Python 环境未混装研究依赖。原有五个公开 CLI 不变。用户已有 reports/README.md 和本主题规划文档修改已保留。

## 实施过程与选择

1. 检查两个 checkout 的修改，读取实施计划及本地 mjlab 源码。上游基线 revision 为 `936f7a125`，本项目实现开始时为 `e917cf5`。
2. 默认执行权限看不到 GPU；完整主机权限能够访问 CUDA 0–7。实测 0–3 利用率 99–100%，4–7 利用率 0%、空闲显存约 72 GB，首轮选择物理 GPU 4，未干预其他进程。
3. 原数据路径 `/data1/wenyifan/molmospaces_data` 不存在，实际资产位于 `/data1/wenyifan/lmdf/molmospaces_data`。研究配置显式使用后者。真实目标 body 为 `cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2`，资产为 Cup_30。
4. 用现有 Simulation / task_local_model 恢复原始 XML，局部半径 2 m；准备时源模型 150 bodies，裁剪后含机器人 105 bodies，去除机器人供 mjlab 单独挂接后的场景 84 bodies。保留原目标及周边真实家具，不载入旧 MJB。
5. 保持窗口成为 NumPy / Torch 共用函数；CPU evaluate_pick 的公开接口与证据检查保留。双指力、支撑、滑移与固定部件漂移在 GPU 子步监测，普通碰撞作为惩罚和遥测。
6. SAC 使用独立 Actor/Q1/Q2 GAT、两个 target、tanh logπ 修正和温度更新。Replay 拥有独立存储，终止样本先入库、再局部 reset；20 秒时限不 bootstrap。恢复为参数 / 优化器热恢复，Replay 重新 warmup。
7. 评分恢复每个真实站位初态，使用在线 min-Q 的 16 次动作采样。评估先保存空间验收集合的分组及 seeds，再运行 rollout；保留初始化 Actor 基线入口、分母、失败原因和状态 / 动作回放数据。
8. 真实 val103 上的 0.1 m / 16 yaw 候选共 5,072 点，初始化检查合法 2,263 点：训练 1,486、验证 291、验收 486；非法点为 Home 穿插 2,795、地面支撑 14。筛选仅用物理初始化约束，不使用 IK / FOV / 规划成功过滤。该检查使用原 CPU 环境，GPU reset 的稳定性仍需单独确认。

## 已运行的验证

| 检查 | 实测结果 |
|---|---|
| 原始 val103 / Cup_30 场景重编译和统一局部裁剪 | 通过，使用真实资产 |
| 旧抓取判定局部回归 | 19 项通过 |
| SAC 数值、图排列、Replay 所有权、checkpoint 恢复 | 6 项通过（原 torch 2.7.1 的 CPU 软件测试，未替代研究环境验证） |
| 图几何分块、动态目标、Base Frame yaw、共享保持窗口及旧判定 | 与 SAC 合计 27 项通过 |
| 站位分组、空间 split、基础设施失败分母 | 3 项通过 |
| 全套离线回归 | 407 项通过、2 项跳过；此后新增图几何 / 站位统计测试在局部通过 |
| 独立研究环境 | 151 包依赖兼容检查通过；MuJoCo / Warp 后端 3.16.0、warp-lang 1.19.0、torch 2.14.0+cu130、PyG 2.7.0 |
| 研究环境新增软件测试 | 11 项通过；后续圆柱枚举兼容回归另通过 |
| 上游 YAM lift-cube | GPU 4，1 环境真实 5 步，qpos 有限；不代表 Panda 抓取 |
| 项目早期单 / 32 环境 | 软件 stepping 跑通，后查有碰撞缓冲溢出，不计物理验收 |
| 修复后 32 环境 | 160 条真实 transition，预热后 stepping 1.698 s；overflow=0，固定部件失败=0，实际碰撞环境=1；两次 SAC 更新有限，局部 reset 后其余 31 环境 qpos 和任务缓存不变 |
| 六指标 / 四组图真实 smoke | GPU 5，40 条采样 / 2 次诊断更新；8 validation 站位×2 rollout，16 healthy、0 基础设施失败、0 success；Entropy=4.539 nats，平均 episode return=-36.098，Spearman=null；四组 PNG 已输出 |
| 监控与控制实现后的软件回归 | 独立研究局部 15 项通过；原环境全套 415 项通过、4 项跳过（含仅研究环境可跑的 DLS 测试） |

全套首轮的子进程失败来自主机缺少 libEGL.so.1；从 Ubuntu 官方包提取用户态库至 `/tmp/lastmile-gl/lib` 后复测通过，未修改系统驱动。测试设置 OMP/MKL 单线程，图网络临时依赖在 `/tmp/lastmile-pyg`，没有写入主 Python 环境。

## 当前环境问题与后续检查

官方 CUDA 13 PyTorch 包可用，固定选择 2.14.0+cu130。整包传输在本会话会停滞，使用 Range 请求和镜像完成下载及安装；安装脚本支持显式包源和本地 wheel 目录。参考 mjlab 源码未修改。

已处理两个真实兼容问题：NumPy int 在 MuJoCo 3.16 枚举 tuple 中的反向比较导致圆柱被误判，现将公共几何类型转为 int；Warp 不支持非零 mesh margin 与 MULTICCD 同开，研究配置显式关闭 MULTICCD，保留原 margin、碰撞形状与物理接触。DLS 的目标名按本地 mjlab 实际 API 绑定到 Panda 工作臂关节。

MuJoCo 3.16 下实际初始化合法点为 1,596：训练 1,124、验证 182、验收 290（旧 CPU 3.5 的 2,263 不作为当前分母）。图容量 435，arm/task 状态 65 维。有效 32 环境单元为 `outputs/experiments/scratch/20261109-mjlab-panda-smoke-220024/`，源码 `5a6f4d3`，最大底盘 XY / yaw / 升降漂移为 0.722 mm / 0.000134 rad / 0.133 mm，实际机械臂关节位移范数 0.0052–0.0209 rad。

早期单 / 32 环境单元日志存在 broadphase overflow，已撤回其物理通过结论。原因是 mjlab 默认每环境 96 个接触缓冲不足以容纳真实房间，现显式使用原场景量级的 nconmax=5,000 / njmax=5,000，运行中 overflow 单列基础设施失败并排除 Replay。初次枚举、MULTICCD、绑定及缓冲诊断均保留在独立 scratch 单元，没有覆盖原日志。

仍需完成：脚本抓取抬升保持正例、子步接触时序实测、128 / 256 环境吞吐、六项训练指标和四组图、训练、checkpoint 选择、验收与初始化基线、图和视频。P0–P6 不能因源码存在或软件测试通过而标为全部完成。

## 新增监控要求与提交

用户于本轮追加 Actor Loss、Critic Loss、Entropy、Evaluation Success Rate、Episode Return、Value-Success Correlation 六项指标，以及四组图（Loss、探索、训练效果、站位评价）。已接入 Entropy、日志间隔均值、独立 validation 向量 rollout、冻结 Q 全图、Spearman、best checkpoint 和四组 PNG；配置固定为每 100,000 transition、8 站位×2 rollout。软件检查与真实监控 smoke 分开，尚无正式训练学习曲线结论。

真实监控验证单元：`outputs/experiments/scratch/20261109-mjlab-panda-monitor-gpu5-222243/`，源码 `75da668`。四组图位于其 `runs/views/`：`loss-curves.png`、`exploration.png`、`training-effect.png`、`station-evaluation.png`。底层 metrics、逐 trial 结果、实际全图 Q 和 seeds 同时保存。该 Actor 仅做了两次软件诊断更新，不是已训练策略；全零成功率下相关性不可计算，图内标 N/A，不以热图外观通过地图验收。

P2 首个原始抓取脚本 20 s 超时，轨迹显示伺服目标只领先实测关节约 0.005 rad，导致收敛过慢。`2874119` 改为按上一命令限制变化速度，并从 reset 实测位置初始化命令；两项针对性控制测试通过。修复后的实际脚本在接近杯子时达到 3.26 cm 位置误差，但触发 2.05 mm 底盘漂移，保持失败，未将双指接触当作成功。后继脚本采用预抓取接近，并对齐现有 Panda 执行配置的 π/2 抓取 roll 与 1 cm 接近偏移，继续验证。

实现按冻结验证迭代提交：`f61db62` 为研究主干，`1eea7e4` 修复几何枚举并记录冻结 revision，后继修复 MuJoCo 3.16 碰撞选项 / 关节绑定，`5a6f4d3` 修复真实接触缓冲并拒绝溢出样本。每个真实 smoke 从对应提交导出的源码启动，运行单元 README 保存提交、参数、结果和失败原因。

当前抓取 TCP 输入为数据抓取库的固定 row 0；其 Panda TCP 对齐与实际可抓取性仍须在 P2 实测。当前 reset 从统一准备阶段的 settled 物体状态恢复，需继续核对各站位伺服初始化后的稳定性及局部 / 全房间代表 rollout。

## P2 脚本抓取实测（2026-11-09 晚）

`tests/smoke/rl.py --scripted-batch` 让每个环境各走一套预抓取、接近、闭合、抬升状态机，一次覆盖 128 个随机训练站位，动作走与 Actor 相同的 7D / DLS / 伺服路径，不调用规划器，并按传感器记录碰撞来源。同一批站位（seed 42）的结果：

| grasp.approach_offset_m | strict-pick 成功 | 无非法碰撞成功 | 手掌碰杯站位 | 单元 |
|---|---|---|---|---|
| +0.01（原配置） | 38/128 | 0 | 39 | scratch/20261109-mjlab-panda-scripted-batch-233718 |
| 0 | 38/128 | 36 | 4 | scratch/20261109-mjlab-panda-scripted-offset0-234644 |
| -0.01（现配置） | 39/128 | 38 | 1 | scratch/20261109-mjlab-panda-scripted-offsetm1-234644 |

原配置下每次成功都被记为碰撞，来源是手掌碰撞网格 `gripper0_right_hand_collision` 与杯子的真实接触，属于抓取过深，不是传感器误判；现配置改为 -1 cm 后基本消失。CPU 判定中 `nonfinger_target` 同样只作遥测、不决定成败，与 GPU 一致。

成功站位覆盖 15 个 yaw 档，说明 Base Frame 动作在多朝向下可用。约三分之二站位停在预抓取阶段，主要是直线 TCP 接近在这些站位够不到目标或被自身限制，这是脚本策略的上限，不等于这些站位不可抓；按方案，这些合法站位保留在训练池中。碰撞来源另有少量自碰撞和家具（environment_14 / 31 / 41），属于真实接触。

P2 的脚本正例由此成立。保持窗口的 NumPy / Torch 一致性由 `tests/runtime/test_pick_state.py` 用同一段合成事实序列覆盖；真实轨迹的 CPU 回放对照尚未执行。

## GPU 隔离与吞吐

入口脚本在导入 torch / Warp 之前调用 `lastmile_dataflow.gpu.isolate_from_argv`，把进程限制在 `--gpu` 指定的物理卡上。此前未隔离时，单次运行在 GPU 0 上额外占用约 338 MiB；先在解析参数后再设置环境变量的做法无效，曾导致两个诊断进程落到 GPU 0 并因显存不足退出。隔离后用 nvidia-smi 持续采样确认进程只出现在指定卡上。

| 环境数 | 向量步/s | transitions/s | 峰值 torch 显存 |
|---|---|---|---|
| 32 | 2.97 | 95 | 0.15 GB |
| 256 | 1.90 | 488 | 0.97 GB |

以上为随机动作 40 步、预热后的 stepping 口径；256 环境 overflow 为 0。SAC 单次更新约 31 ms（batch 256），远小于约 0.5 s 的向量步，因此增加了 `training.updates_per_step`，默认仍为方案的 1。正式训练含碰撞与复位，实际吞吐更低，以 `metrics.jsonl` 为准。
