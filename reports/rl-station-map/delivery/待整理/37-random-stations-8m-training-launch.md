# 随机站位方块800万条训练启动

- 状态：active
- 日期：2026-11-17
- 时间戳：2026-11-17T21:13:46.120734+08:00
- 替代：无
- 关联：[实现与短验证](36-random-stations-physical-base.md)

用户要求停止CUDA0–3的PPO，使用rl-cube-random-stations工作区运行800万条训练。现场核对PPO已完成300万条/184次更新并退出，GPU0–3空闲；无需发送终止信号，旧产物保留。

源码提交`3e3b4ec13cbcca982698f206b47e0db7311f5bb5`；源代码无未提交修改，保留已有报告修改与本地环境链接。从零SAC，随机正对目标站位、物理固定底盘、69D输入；每卡128环境，总512，Learner与Sampler0共用GPU0。配置仅覆盖training.max_transitions为8,000,000；seed42，不加载旧checkpoint。预热20,000条，每128条更新一次，batch512，预计62,343次更新。TensorBoard每30秒汇总，禁用周期评估；本次仅负责启动训练与核查真实采样/更新，不将启动成功视作Actor学会抓取。

Run：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123`。命令、有效配置、进程、初始工作区状态见launch.json/input.yaml。运行期间不修改源码。

## 启动验证

2026-11-17T21:15:09.100672+08:00：主进程3673115和四个Sampler（3674356–3674359）存活，nvidia-smi确认分别占用物理GPU0–3。已采样60,032条健康经验，完成312次SAC更新；最新日志random_actions为False。latest.pt实测输入69维，候选站位233个，预算8,000,000，四卡各128环境。验证结果见run内startup-verification.json。当前仅确认训练正常采样与更新，未验证最终抓取效果。
