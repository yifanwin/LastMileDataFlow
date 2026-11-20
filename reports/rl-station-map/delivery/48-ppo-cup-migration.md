# Cup_30 随机站位 PPO 迁移与修正

- 状态：active
- 日期：2026-11-20
- 时间戳：2026-11-20T00:45:00+08:00
- 替代：无
- 关联：[PPO 失败排查](../analyze/ppo-cube-s0246-failure-diagnosis.md)、[PPO 接入](36-ppo-integration.md)、[运行指南](running.md#ppo-训练与评估)

## 目标

把 rl-cup（Cup_30、随机站位、物理固定底盘、类型化碰撞/卡住/掉落奖励）的 SAC 任务接到 PPO 流程，并按失败排查修正 PPO 的优化、网络和输入。

## 修改

1. 合并 `codex/rl-cup`（e389498）到 `codex/rl-ppo`。保留 rl-ppo 的共享模块结构（`networks.py`、`checkpoint.py`、`training_common.py`），并把 rl-cup 的改动移进去：动作维度/动作定义、按 `state_dim` 的 flat 输入定义、`training.best_checkpoint` 选模窗口、抓取/碰撞/底盘回合字段、`grasp-subset.json`（SAC/PPO 共用 `record_grasp_subset`）。
2. PPO 优化：去掉逐 minibatch 的 KL 硬停；改为每个 minibatch 用精确高斯 KL 自适应 Actor 学习率（`desired_kl` 0.01，×/÷1.5，范围 1e-5–1e-2）；`minibatch_size` 4096，梯度裁剪 1.0，`value_lr` 1e-3。
3. PPO 网络：新 `GaussianActor`，log std 为状态无关参数（初值 1.0），正交初始化、输出层增益 0.01；V 输出层增益 1。熵奖励 0.005（tanh 前高斯熵）。SAC 的 Actor 不变。
4. 输入：flat 模式 Actor/V 共享运行均值/方差归一化（std 下限 0.01、裁剪 ±10），每轮 epoch 后用本轮样本更新，随 checkpoint 保存并下发 Sampler。
5. 参数：`gamma` 0.99；新配置 `configs/station_map/rl/val103_cup30_random_stations_facing_ppo.yaml` 与 SAC 配置只差 `algorithm: ppo`、`ppo: {}`、`episode_s: 20`（rl-cup SAC 最近3000回合成功回合步数中位数102、p90 351）。
6. 工作区：`outputs/experiments/grasp-fps-all` 软链接到 rl-cup 同名目录。合并前工作区里 2026-11-18 的未提交抓取可视化改动（rl-cup 已有后续提交版本）已 `git stash`：`stash@{0}`。

## 验证

- 离线：`tests/station_map/test_rl*.py` 128 项通过（新增自适应学习率、精确 KL、状态无关 std/初始化、归一化统计与更新时机测试），`tests/test_scripts.py` 通过。
- 真实冒烟：`outputs/experiments/scratch/20261120-002833-ppo-cup-smoke`，GPU4（与其他训练共卡）、128 环境、10 万条。25 轮更新、125 次梯度步（每轮 5 epoch 全部完成），minibatch KL 0.006–0.024，学习率在 4.5e-4–1.0e-3 间调整，EV 由 0.24 升到 0.82，69 维输入、7 维动作，latest/best/grasp-subset 正常保存与加载，归一化计数 100000。129 个回合 0 成功（预算过小，不代表效果）。
- 吞吐：共卡情况下约 107 条/s/卡，是正式训练的主要瓶颈。

## 注意

- 本工作区 `.venv` 是指向 actor-critic 环境的软链接，其 `.pth` 让 `lastmile_dataflow` 从 actor-critic 源码导入。运行时必须加 `PYTHONPATH=$PWD/src`。
- 未做正式训练，PPO 能否学会 Cup 任务尚未验证。

## 第一轮正式训练与评估（2026-11-20T10:15:45+08:00 记录）

- run 20261120-082753-cup30-ppo-20m-e512-g4567（每卡512环境，吞吐约2750条/s；128环境时约1050条/s）。1212万条时按用户指令停止：0成功，接触率≈0。
- 评估（latest.pt，20站位随机策略，视频在 run 的 evaluation-latest/）：20/20 到达抓取位（最佳误差0.4–2.4 mm），之后停留至超时；夹爪开度在全开的约0.5–0.8之间晃动，从未双指接触，全程无碰撞。夹爪维 std 1.14（7维中最大，未学习）。
- 原因：夹爪动作是绝对开度，零均值噪声平均成半开；且闭爪奖励要求先接触（closing_requires_contact），接触前合拢没有任何奖励。

## 方案A与增量夹爪（提交 6fde5ba）

- reward.closing_distance_m / closing_angle_rad：闭爪进度单独的对准门槛；未配置时沿用 grasp_distance_m / grasp_angle_rad。Cup 配置改为 closing_requires_contact=false、1 cm、10°（双指接触事件仍用 3 cm、20°）。
- action.gripper_step：夹爪动作改为每步闭合比例增量（|a|=1 时 20% 行程），截断在[0,1]，reset 为全开；未配置时仍为绝对开度。
- SAC 与 PPO 的 Cup 随机站位配置同时启用两项，旧配置与 checkpoint 不受影响。
- 验证：130 项离线测试通过；PPO（512环境）与 SAC 真实冒烟无报错；真实环境中 +1 每步闭合约20%、0 保持、-1 全开，脚本移到抓取位后合拢，4/4 环境指腹受力 2–8 N，闭爪奖励与双指接触 +3 正常发放。
- 正式训练：run 20261120-101117-cup30-ppo-closeA-incgrip-20m-e512-g4567，CUDA4–7、每卡512环境、2000万条，约2780条/s。

## 独立验证（2026-11-20T11:10:53+08:00）

训练进行中（约880万条）对两个冻结模型做随机策略评估，CUDA4–7 并行，每个模型2个seed × 20站位，全部录视频，结果在 run 的 evaluation/<模型>-seed<seed>/。

| 模型 | 成功 | 无碰撞成功 | 成功回合平均步数 | 碰撞类型（出现的试验数） |
| --- | --- | --- | --- | --- |
| best.pt（504.6万条，首个200/200窗口） | 40/40 | 25/40 | 59–78 | palm_target 10，finger_outer 6 |
| ckpts/step-008454143.pt（845.4万条） | 40/40 | 38/40 | 44–49 | finger_outer 2 |

845万快照明显更好：同样全部成功，碰撞更少、更快。best 按"严格提高才覆盖"保留了第一个满分窗口，后续同为100%的更好模型不会替换它。
