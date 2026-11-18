# 双窗口训练监控与成功率选模

- 状态：done
- 日期：2026-11-17
- 替代：旧 train 区间统计与 return / 30 秒区间成功率选模
- 关联：[运行指南](running.md#tensorboard-实时监控)、[初版监控](17-tensorboard-monitoring.md)

## 目标与实现

2026-11-17 23:30 +08:00：先在 actor-critic 实现，再向 rl-cube-contact、rl-cube-random-stations 和 rl-ppo 合并。

- 默认每 30 秒刷新；loss / entropy 为该区间全部 SAC 更新均值，Alpha、Replay、Updates、吞吐沿用现有统计。PPO 保留 ppo 指标。
- `train_interval/{success,return,steps,collision_free,reward/*}` 统计本区间完成的健康、非随机预热回合；`episode_count` 和 `success_count` 是区间计数。
- `train/` 使用同名标签统计最近最多 100 个合格回合，跨刷新保留；两项 count 分别是实际窗口大小与成功数。按 Learner 收到全部 Sampler 事件的顺序入窗，普通失败计入分母。
- 空区间仅将区间 count 写零，不伪造均值；已有滚动趋势继续展示，没有新增回合不参与选模。warmup 维持独立区间统计。
- `best.pt` 与 `train/success` 使用同一窗口、相同分母，严格提高才替换，同分保留；不足 100 回合使用实际数量，结束尾段参与比较，无合格回合才以最终模型兜底。
- 恢复训练新建空窗口，只继承标识为 `recent_100_episode_success_rate` 的已有 best 排名；旧指标不混比，不回填历史日志。目标分支的周期权重和 PPO 保存时机保持原有行为。

## 验证与边界

actor-critic：31 项相关离线测试通过（monitor、best、evaluation、sampling、SAC 与分层测试）。真实 TensorBoard 事件读写覆盖双窗口、100 回合淘汰、空区间、预热与故障过滤、奖励分项、选模、结束尾段、恢复与累计横轴；训练流程使用 mock，不代表实际策略效果。

仅更新后续启动或恢复的训练；未重启正在运行的任务，未修改历史日志、权重或运行产物。未执行长期训练或真实 GPU rollout。

2026-11-17 23:34:15 +0800：rl-ppo 合并完成；51 项相关离线测试通过，新增流程验证确认滚动成功率在 PPO 更新前选择产生回合的策略，更新后刷新 PPO loss；保留 ppo 指标与 Q/V 评分差异。

2026-11-18 14:21:27 +0800：rl-ppo 同步精简 TensorBoard 分组：去掉 `train_interval/*` 和全部 `*/success_count`；最近 100 回合窗口拆成 `train/`、`mobile/`（新增记录 max_drift_m、max_yaw_drift_rad、max_lift_drift_m）、`reward/`；`ppo/`、`warmup/` 不变，选模口径不变。monitor、best、evaluation、ppo 共 36 项离线测试通过。
