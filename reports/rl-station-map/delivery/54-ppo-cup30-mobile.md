# PPO Cup_30 移动底盘：合并 rl-mobile 与按 PPO 定奖励和参数

- 状态：active
- 日期：2026-11-20
- 时间戳：2026-11-20T11:40+08:00
- 关联：[PPO固定底盘总结](49-ppo-cup-summary.md)、[SAC移动底盘合并](50-cup30-mobile-base-setup.md)、[SAC移动熵奖励根因](52-cup30-mobile-budget30-e512-results.md)、[SAC移动当前训练](53-cup30-mobile-noenttarget.md)、[运行指南](running.md#ppo-训练与评估)
- 工作区：.worktrees/rl-ppo，分支 codex/rl-ppo

## 合并

codex/rl-mobile（64aec57）合入 codex/rl-ppo（提交 d4b34af）。冲突4处：

- `env/terms.py`：PPO 的闭爪严格门槛（`closing_distance_m/angle_rad`）与移动的一次性 failure 罚分并存；移动侧不再使用的 `near` 删除。
- `evaluation.py`：用移动侧“忽略 evaluation 段再比对配置”，加载改回 PPO 侧的 `load_agent`（SAC/PPO 通用）。
- 两个测试文件：两边测试都保留。

带进来的移动修复：底盘相对实测位姿增量、failure 罚分只给没有专门罚分的失败、`collision_budget`、SAC `entropy_in_target`。PPO 的 GAE 本来就把超时当截断、继续 V 引导，与移动侧的 SAC 修复一致；动作维度和观测维度由环境自动得到（10D、75D）。

## 奖励和参数：按 PPO 定，不照搬 SAC 移动配置

新配置 `configs/station_map/rl/val103_cup30_random_stations_mobile_ppo.yaml`，以固定底盘 PPO 配置（已验证40/40）为基：

| 项 | 取值 | 理由 |
| --- | --- | --- |
| 底盘动作 | 0.03 m、0.15 rad | 与 SAC 移动同尺度 |
| 闭爪门槛 + 增量夹爪 | 保留（1 cm/10°，20%/步） | PPO 零均值噪声下绝对夹爪平均成半开，是固定底盘 PPO 学会的关键 |
| 碰杯分档 | 保留固定底盘 PPO 值，不减半 | SAC 移动减半是为“回避杯子”，报告52查明根因是 SAC 熵奖励流，PPO 熵只在损失里，没有这条流；报告52也显示减半无效 |
| `failure` | 20 | 越界是移动底盘新增失败，与掉落−20持平，越界不比退开划算 |
| `collision_budget` | 30 | 卡死判定管不住底盘边动边碰；PPO 是在线采样，磨碰撞的回合直接浪费 rollout |
| `episode_s` / gamma | 20 s / 0.99 | 固定底盘 PPO 平均40步完成，400步足够；SAC 的30 s、0.995、`entropy_in_target`、`target_entropy`、replay 都是 SAC 专用，不用 |
| `ppo.init_std` | TCP与夹爪1.0，底盘三维0.3 | std 1 时底盘每步约0.09 rad 随机转动，400步随机游走约1.8 rad，会破坏1 cm闭爪门槛；0.3 起步，log std 仍可学 |
| best 门槛 | 85万 | PPO 无预热：4卡×512环境×400步=81.9万后所有第一回合都已结束 |
| `max_transitions` | 2000万 | 与固定底盘 PPO run 相同 |

代码改动只有一处：`PPOConfig.init_std` 可以是一个数或每维一个数（`networks.GaussianActor` 据此初始化 log std），提交 d0753b4。

## 验证

- `tests/station_map` 下 `test_rl_*` 135项通过（新增逐维初始 std 断言）。
- GPU 冒烟 `outputs/experiments/scratch/20261120-ppo-mobile-smoke/`：GPU4、64环境、3万条正常结束；checkpoint 75D观测、10D动作，动作顺序 tcp_dx..tcp_rz、gripper、base_forward、base_side、base_yaw；底盘 std 0.30；64回合中位底盘位移17 cm、转角0.11 rad，无越界，7回合触发碰撞上限；碰撞时长 robot_scene 0.33 s、palm_target 0.17 s。只验证流程。

## 正式训练

Run：`outputs/experiments/ppo-cup/runs/20261120-113701-cup30-mobile-ppo-basestd03-20m-e512-g4567/`，提交 d0753b4，GPU4–7、每卡512环境、seed42、2000万条，命令见 launch.json（timeout 24 h）。

启动检查（2026-11-20T11:45+08:00）：约1分钟后开始更新，无报错；约2740条/s（与固定底盘 PPO 相同，预计约2小时跑完2000万条），每卡显存约20 GB；每轮80个 minibatch 全部跑满，KL约0.012，EV 0.29→0.44，学习率在 KL 自适应下的起步波动与固定底盘 run 一致。
