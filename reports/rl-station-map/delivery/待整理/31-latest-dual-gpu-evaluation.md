# latest模型CUDA0–1独立并行评估

- 状态：done
- 日期：2026-11-17
- 时间：2026-11-17T15:56:27.125057+08:00
- 替代：无
- 关联：[并行评估实现及best结果](30-parallel-independent-evaluation.md)

## 目标与执行

按用户要求，评估训练run `20261117-131857-s0246-cube-stage-peak-success-3m-b512-g0123/latest.pt`。模型对应3,000,000条健康transition、23,281次更新；严格使用匹配的训练配置，固定S0246/Home/方块初态，随机Actor、50s上限、同一物理子步双指承力且相对抬升至少0.0086m成功。总试验数仍为20。

执行源码固定于`950533d41924a067fae6256591d7f3c7a091485c`；本次没有修改评估或训练源码。两个独立进程调用已有公共`run_evaluation_trials`，物理CUDA0负责trial00–09，CUDA1负责trial10–19，各10个环境向量执行，逐回合随机流使用seed20261109–20261128。两个进程实际PID分别1034723、1034724，已通过nvidia-smi的PID/UUID映射核对物理GPU；各自进程内可见卡为0，CUDA_VISIBLE_DEVICES分别为0、1。分片调度的完整命令及环境记录于launch.json；evaluate.py的单卡CLI未新增多卡参数。

开始2026-11-17T15:53:22+08:00，结束2026-11-17T15:54:00.291872+08:00，两个进程均退出0。含初始化和全部视频的总墙钟耗时37.434s；20段视频渲染耗时总和17.849s，两卡的视频阶段实际并行。该模型回合很快成功，与best评估的回合长度不同，不能据此计算多卡加速倍数。

## 结果

| 指标 | 结果 |
| --- | --- |
| 总回合 / 健康回合 / 异常回合 | 20 / 20 / 0 |
| 成功率 | 20/20（100%） |
| 无非法碰撞成功率 | 0/20（0%） |
| 成功但曾记录被监测非法碰撞 | 20/20 |
| 失败 / 超时 | 0 / 0 |
| 平均回合回报 | 36.093272 |
| 回合策略步数 | 41–52 |

这是固定训练场景和初态下，冻结latest模型的随机策略表现，未测试新站位或新场景。全部回合满足任务成功协议，但均曾发生被监测非法碰撞；成功与全程无非法碰撞必须分别评价，不能描述为20次无碰撞抓取成功。

## 产物与核查

- [汇总summary.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/summary.json)、[逐回合trials.jsonl](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/trials.jsonl)、[试验分配selection.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/selection.json)。
- [全部20段视频与回放](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/videos.md)，CUDA0与CUDA1的原始日志、视频、回放分别位于本run的gpu0、gpu1目录。
- [启动及命令launch.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/launch.json)、[实际GPU绑定核查](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/gpu-binding-verification.json)、[完整性核查verification.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-155322-latest-parallel20-g01/verification.json)。

全部20段视频已实际解码并核对帧数；每段对应唯一trial/seed，状态回放包含steps+1帧，保留终态，排除结束后的新回合，跨两张卡的20份初始qpos逐元素一致。核对了checkpoint步数、两进程配置检查、20个不同seed、10+10分配、成功率分母和无碰撞成功数。抽看第0回合终态，视频中的总览与夹爪近景可见目标方块。

best的既有评估及相关报告修改全部保留。本次仅新增latest评估运行与交付记录，不重写历史结论。
