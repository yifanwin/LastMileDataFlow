# 方块双指接触与抬升 v2 正式训练启动

- 状态：active
- 日期：2026-11-16
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
