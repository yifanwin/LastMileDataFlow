# Cube PPO CUDA0–3 正式训练启动配置

- 状态：active
- 日期：2026-11-17
- 记录时间：2026-11-17T20:15:51.472849+08:00
- 替代：无
- 关联：[PPO实现与短验证](36-ppo-integration.md)、[运行指南](running.md)

## 启动配置

按用户指令使用物理 CUDA0–3，从零初始化 PPO，不传 resume，不复用短验证模型。工作区 `/home/wenyifan/.codex/worktrees/rl-ppo/LastMileDataFlow`，分支 `codex/rl-ppo`；源码与本文一同提交后再启动，运行期间不修改源码、脚本和配置。固定提交号、实际启动命令、进程号与实时核查结果记录在 run 的 launch.json / startup-verification.json。

场景为 val103、4 cm / 0.1 kg cube、S0246 面向目标，flat 65维观测、7维动作。沿用 cube 当前成功与奖励协议：双指各>0.5 N、最低点距桌面≥1 cm、脱离支撑连续保持0.25秒；episode 50秒，20 Hz策略步，物理步长0.002秒。

每卡128环境，总512；seed42，累计300万健康transition，rollout每环境32步，每轮至多16384条，共预计184轮（最后1728条）。PPO每轮最多5个epoch、mini-batch512，KL>0.02提前停止；最多29300次Actor/V成对更新，实际更新数以日志为准。Actor/V学习率均0.0003，gamma0.999、GAE lambda0.95、clip0.2、value系数1、entropy系数0，Actor/V分别以0.5裁剪梯度。全局有效rollout统一标准化优势。无Replay、随机预热或alpha学习。

best按30秒训练成功率窗口选择，latest随日志保存，累计每40万条另存checkpoint；训练启动和周期不评估、不录视频。flat训练结束不自动评估，完成后的独立策略效果验证另行开展。

## 运行产物与验收入口

- Run：`outputs/experiments/ppo-cube-s0246/runs/20261117-201551-cube-ppo-3m-g0123`。
- 输入：run/input.yaml；展开默认值后由训练写入run/config.json。
- 代码、设备、命令、启动时间、进程：run/launch.json。
- 实际四worker绑定、采样和更新进度、有限数值及吞吐：run/startup-verification.json。
- 进程输出：run/train.log；训练事件：run/metrics.jsonl；实时曲线：run/tensorboard/。

启动前GPU0–3均0%利用率、各72831 MiB空闲；GPU4–7约88%–89%利用率，未使用或干预。首次真实四卡正式规模训练会编译相应仿真图，剩余时间在观察数轮健康采样和学习更新后按实际吞吐估算，不能从两环境短验证外推。本报告记录配置与验收入口，是否成功启动以startup-verification.json的实际结果为准，不将启动等同于训练完成或策略成功。
