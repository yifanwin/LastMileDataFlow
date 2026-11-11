# mjlab 多卡支持与当前 SAC 提速判断

- 状态：done
- 日期：2026-11-11
- 替代：无
- 关联：../docs/01-running.md、../delivery/04-implementation.md、../delivery/05-training-handover.md、../delivery/07-staged-reward-and-lift.md

## 结论

mjlab 支持多卡分布式训练；actor-critic 当前自定义 SAC 尚未接入。多卡并行采样有提速潜力，具体收益未测，不能承诺按卡数线性加速或更快学会抓取。

## 核查过程与依据

核对目标 worktree、已有修改、本地 mjlab 源码与官方分布式文档，阅读训练、GPU 隔离、SAC 更新代码及已有实验报告和日志；查询 GPU 与训练进程。没有启动训练或修改算法。

- 本地上游 `reference_repos/mjlab/docs/source/training/distributed_training.rst` 与[官方文档](https://mujocolab.github.io/mjlab/main/source/training/distributed_training.html)一致：torchrunx 为每卡启动进程，各自运行环境，RSL-RL 在更新时同步梯度。官方 `--gpu-ids` 属于其训练入口。
- 当前 `scripts/experiments/train_sac.py` 在导入 CUDA 库前调用 `gpu.isolate_from_argv`，只暴露 `--gpu` 指定的一张卡。`workflows/rl_training.py` 在同一设备顺序执行采样、Replay、SAC 更新和验证；`policies/sac/agent.py` 没有跨卡同步。增加可见卡或直接套用 torchrun 不会自动形成一个共同训练的策略。
- 默认配置为 128 环境、batch 256、每向量步更新 1 次；当前训练代码的更新/transition 为 `updates_per_step / num_envs`。扩展采样时必须明确保持或调整这一比例、全局 batch、预热与总 transition 预算。

## 速度依据及限制

历史实现报告记录：随机动作预热后，32 环境约 95 transitions/s，256 环境约 488 transitions/s；batch 256 的 SAC 更新约 31 ms，256 环境一个向量步约 0.53 s。这说明当时每步只更新一次时，网络计算占比较小，采样更值得并行；更新 8/16/32 次时网络耗时占比会增大。

交接报告中完整训练累计吞吐约 78–187 transitions/s，包含验证等停顿，不能与纯 stepping 口径混用。现有训练同步执行独立验证、Q 图、视频和曲线，因此多卡采样不能自动消除这些停顿。

11 月 11 日新奖励／升降协议的短训练已完成 2400 transitions、2144 更新，但日志没有分阶段耗时；旧协议的速度不是新协议实测。当前没有多卡对照结果。

本次即时查询未见 train_sac 相关进程。GPU 0 查询时空闲，其他卡利用率约 89–100%；此快照不代表持续可用资源，也不能据此测量本任务利用率。

## 建议顺序

1. 在当前协议下短测单卡 128/256 环境，分别记录有效采样、SAC 更新和验证耗时，避免只看累计吞吐。
2. 若采样占主导，优先尝试两卡采样并向共同 learner/Replay 供数，定期同步 Actor；若更新已占主导，再考虑 learner 并行。均需实际实现，不是已有功能。
3. 用相同全局 transition 预算与明确的更新比例比较单卡／双卡耗时及成功率。吞吐提高与达到相同成功率所需时间分别判断。

若目的是同时比较 seed 或奖励方案，可直接在不同 GPU 启动独立单卡实验，提高实验总吞吐；每个策略本身仍是单卡训练。

## 本次交付

仅新增本分析文档；未改变已有修改，未安装依赖、运行训练或测试多卡性能。
