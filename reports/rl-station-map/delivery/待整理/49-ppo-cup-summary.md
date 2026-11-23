# PPO 迁移到 Cup_30 随机站位：总结

- 状态：done
- 日期：2026-11-20
- 时间戳：2026-11-20T11:21:09+08:00
- 替代：无
- 关联：[失败排查](../analyze/ppo-cube-s0246-failure-diagnosis.md)、[迁移与验证记录](48-ppo-cup-migration.md)、[运行指南](running.md#ppo-训练与评估)

## 目标

分析 S0246 cube PPO 失败原因；把 rl-cup（Cup_30、随机站位、物理固定底盘）的 SAC 任务迁到 PPO 流程；PPO 学会该任务并验证。

## 过程与结论

1. **cube PPO 失败原因**：逐 minibatch KL 硬停（0.02）使每轮只做 1–3 步，300万样本仅 666 次梯度步；再叠加无熵约束、状态相关 std 引起的熵塌缩，策略停在"靠近不接触"。
2. **PPO 修正**（提交 c5352e4）：KL 自适应学习率（精确高斯 KL，目标 0.01）替代硬停；状态无关可学习 log std、正交初始化（输出增益 0.01）；熵系数 0.005；flat 输入运行均值/方差归一化；γ 0.99；minibatch 4096；Cup PPO 配置回合 20 s。同时合并 codex/rl-cup。
3. **第一轮正式训练**：优化正常（每轮跑满 epoch、KL≈0.01、EV 0.8–0.99），但 1212 万条 0 成功。评估视频显示 20/20 精确到达抓取位（误差 0.4–2.4 mm），随后夹爪半开晃动、从不合拢。原因：夹爪是绝对开度命令，零均值噪声平均成半开；闭爪奖励要求先接触，接触前合拢无梯度。
4. **方案A + 增量夹爪**（提交 6fde5ba，SAC 与 PPO 的 Cup 随机站位配置同时启用）：闭爪奖励在 1 cm/10° 对准门槛内发放、不要求接触；夹爪动作改为每步闭合比例增量（20% 行程/步）。默认值保持旧行为。
5. **第二轮正式训练**（run 20261120-101117-cup30-ppo-closeA-incgrip-20m-e512-g4567，CUDA4–7、每卡512环境、约2750条/s）：约 360 万条成功率 91%，约 460 万条 99%；按用户指令于 1062 万条（162 轮、12,960 次梯度步）停止。最近 2000 个训练回合成功 99.9%、无碰撞 91.1%、平均 40 步（2 s）。14.4 万回合仅 1 个异常回合。
6. **独立验证**（随机策略，2 seed × 20 站位，覆盖 37 个站位，全部录视频）：ckpts/step-008454143.pt 成功 40/40、无碰撞成功 38/40、平均 44–49 步；best.pt（504.6万）40/40、25/40、59–78 步。
7. **站位价值质量图**（提交 f709735）：仿真 reset 每站 16 个抓取候选，取 V(s0) 平均。845万模型 V 20.5–31.4，最低 S0095、S0078 在验证中也最慢（85/106 步）；V 与验证步数 Spearman -0.39。

## 推荐模型

`outputs/experiments/ppo-cup/runs/20261120-101117-cup30-ppo-closeA-incgrip-20m-e512-g4567/ckpts/step-008454143.pt`（验证最好）。latest.pt 为停止时的 1062 万条模型，未单独验证。

## 修改清单

- PPO：ppo/agent.py、ppo/sampling.py、ppo/rollout.py、ppo/training.py、networks.py（GaussianActor、ObservationNormalizer）、config.py（PPOConfig）。
- 共享：training_common.py（rl-cup 选模窗口、回合字段、grasp subset）、checkpoint.py（按维度的输入定义）、evaluation.py / soft_value.py（flat PPO V 评分与统一质量图）。
- 环境：env/environment.py（gripper_step）、env/terms.py（closing_distance_m / closing_angle_rad）。
- 配置：val103_cup30_random_stations_facing.yaml（SAC）、val103_cup30_random_stations_facing_ppo.yaml（PPO）。
- 测试：test_rl_ppo、test_rl_dls、test_rl_rewards、test_rl_best、test_rl_evaluation，共 130 项离线测试通过。

## 遗留问题

- 本次 PPO 与 rl-cup SAC 的对比不同条件（奖励门槛、夹爪控制、γ、回合长度不同）；改动后的 SAC 配置尚未训练，需同配置 SAC 才能单独比较算法。
- best 选模"严格提高才覆盖"，同为 100% 时保留最早窗口，选出的 best 不如后期模型；可考虑改为无碰撞成功率或加入碰撞指标。
- 验证全部成功，V 只能与完成步数对比，尚无与成功率的相关性。
- 运行需 PYTHONPATH=$PWD/src（本工作区 .venv 链接到 actor-critic 环境）；grasp 库通过软链接引用 rl-cup。
- 合并前工作区的未提交抓取可视化改动在 git stash@{0}。
- 冒烟与 scratch 输出（outputs/experiments/scratch/2026112*-ppo-cup-*、*-sac-cup-*）可按需清理。
