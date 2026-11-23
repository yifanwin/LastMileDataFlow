# 方块双指接触与抬升 v2 正式训练启动

- 状态：done
- 日期：2026-11-17
- 替代：无；新run，不复用旧v1结果或短训练模型
- 关联：[v2实现与验证](20-cube-contact-lift-v2.md)、[需求](../requirement/01-fixed-base-sac.md)、[配置](../../../configs/station_map/rl/val103_cube_s0246_facing.yaml)
- 启动准备时间：2026-11-16T22:49:23.207828+08:00

用户授权启动正式训练。工作区为 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact`，运行前固定源码提交；保留19号报告已有未提交修改，训练期间不改源码。确切commit、进程、命令、环境和启动后核查写入下述run的 `launch.json` 与 `startup-verification.json`。

- 任务：val103固定S0246，0.04 m/0.1 kg方块，65维flat输入；同一物理子步双指力各>1e-6 N且中心相对初态抬升0.0086 m即成功，正值闭爪，七项奖励。
- CUDA0–3启动前均空闲；每卡一个128环境Sampler，总512环境，Learner与Sampler0共用GPU0。保留其他卡进程。
- 从头训练，seed42；全局1,000,000条健康transition、预热20,000、batch256、Replay100,000、每128条新增健康经验更新一次，预计7,656次更新。沿用原SAC配置与50s回合。
- 无启动/周期评估或站位Q图扫描；每30s刷新TensorBoard，按近期非预热健康训练回报选择best.pt。此启动任务不附加训练结束后的独立策略成功率评估。
- 解释器复用actor-critic已有RL环境，`MUJOCO_GL=disable`，不安装依赖。设置2小时运行超时，防止意外无限运行；参照此前同规模四卡训练预计15–30分钟，实际以日志为准。

原始产物：`outputs/experiments/rl-cube-lift-v2-s0246/runs/20261116-224923-s0246-cube-lift-v2-1m-g0123`；run内保存config.yaml、launch.json、console.log、metrics.jsonl、tensorboard/、checkpoint及启动核查。单元README记录状态和源码版本，不复制源码，不生成文件指纹。历史运行不修改、不搬迁。

复用上一轮85项station_map回归、GPU0物理抓取抬升及四卡1024条/7次更新验证，不重复跑相同测试。启动后核查真实采样计数、预热后SAC更新、四卡进程、checkpoint输入定义和TensorBoard事件，具体结果以run内核查记录为准；启动成功不等于策略学会任务。

## 2026-11-16T23:09:08.755354+08:00 从头训练500万条健康样本

用户要求重新从头训练500万条健康样本。前一run已完成1,000,000条健康transition、7,656次实际更新，采样阶段895.983s；进程已退出，原始结果和checkpoint保留。控制台返回的updates=7422对应重新加载的best模型，不是训练实际总更新数，实际以sampling_complete为准。

新run：`outputs/experiments/rl-cube-lift-v2-s0246/runs/20261116-230908-s0246-cube-lift-v2-5m-g0123`。源码仍固定 `551ac842913d8c91d9fc0735d5192ca443fe7a1f`，不修改运行源码和仓库默认预算。run内config.yaml将training.max_transitions显式设为5,000,000，命令同样传 `--max-transitions 5000000`；**不传--resume、不加载任何旧checkpoint**，seed42重新初始化Actor、Critic、优化器和Replay。

CUDA0–3启动前各0%利用率、约3MiB占用；每卡128环境，共512环境，GPU0同时放Learner。预热20,000、Replay100,000、batch256、每128条更新一次，预计38,906次更新。其余任务、奖励、50s回合、每30s监控与按训练回报选best规则不变。不增加启动/周期评估。按前轮吞吐估算约75分钟，设置4小时运行超时以容纳实际波动，非耗时保证。

命令、PID、有效配置和环境见新run的launch.json，启动核查写startup-verification.json；任务完成后以metrics.jsonl中的sampling_complete确认实际数量。不重复执行已通过的85项回归和物理短验证，不将启动或旧run结果作为新模型效果结论。

## 2026-11-17T09:15:31+08:00 完成核查

100万和500万run均完成，实际分别7,656和38,906次更新；采样阶段分别895.983s和4515.156s。训练结果、奖励偏移及best/latest区别见[对比总结](22-cube-lift-v2-1m-vs-5m-results.md)。没有新增独立策略评估或视频，不把训练成功率作为checkpoint验收结果。
