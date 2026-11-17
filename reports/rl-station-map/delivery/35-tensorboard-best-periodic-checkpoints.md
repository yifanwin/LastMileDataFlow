# TensorBoard 同窗口 best 与每40万步 checkpoint

- 状态：done
- 日期：2026-11-17
- 时间戳：2026-11-17T19:22:20+08:00
- 替代：运行指南中的最近100回合 best 规则；历史run和历史报告不改写
- 关联：[运行指南](running.md)、[核心需求](../requirement/01-fixed-base-sac.md)

## 目标与修改

用户要求 best.pt 与 TensorBoard 同步，以30秒区间内的成功率比较；每40万步保留一次独立 checkpoint，放入 ckpts/。

`TrainingMonitor.flush` 返回它刚写入 `train/success` 的同一个窗口的成功率与回合数，训练侧立即比较并保存对应模型。全部 Sampler 合并，只纳入健康、完整、全程没有随机预热动作的训练回合；普通失败纳入分母。成功率严格提高才替换 best，同分保留，空窗口不比较；不再维护最近100回合窗口，也不设最低100回合门槛。默认30秒，可沿用 `training.tensorboard_every_seconds` 调整。训练结束不足30秒的尾段随 TensorBoard 强制刷新并参与比较；整个训练无有效回合才用最终模型兜底，rate=null、count=0并记录原因。

checkpoint 中 `best_training_success_rate.metric` 改为 `tensorboard_interval_success_rate`，保存 rate、count、transitions 与 fallback。恢复 latest 时仅继承此口径的排名，旧回报或最近100回合排名不作转换；新窗口从恢复时开始。

累计健康 transition 越过400000、800000、1200000等固定阈值时，在该批次的学习更新完成后保存完整 SAC checkpoint 到 `ckpts/step-<九位实际步数>.pt`，全部保留。批量采样允许略超阈值，文件名与内部计数均为实际步数；下一阈值仍是40万的整数倍。保留原有日志保存 latest 与最终 latest 的行为。恢复训练从累计步数计算下一个阈值；从 ckpts 历史快照恢复时重新比较 best，避免沿用已经被后续训练覆盖的根目录 best 所对应的旧排名。

修改范围：`station_map/rl/monitor.py`、`training.py`、best与训练恢复相关测试，以及当前运行说明和需求。

## 验证

定向21项测试通过（best、monitor、evaluation）；读取真实 TensorBoard 事件验证30秒边界、窗口均值与 best 选择一致，覆盖预热/异常回合排除、同分不覆盖、空窗口、结束尾段、无回合兜底，以及40万阈值与批次越界。模拟训练入口覆盖新版单回合排名恢复、旧排名丢弃、从 ckpts 恢复、累计阈值保存和原末尾评估路径。

完整 RL 局部回归74项通过（6.869秒），git diff --check通过。命令从目标工作区执行：

```bash
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl PYTHONPATH="$PWD/src" \
  ../actor-critic/.venv/bin/python -m unittest discover -s tests/station_map -t . -p 'test_rl*.py' -v
```

## 生效范围与限制

本次为代码与离线测试交付，没有启动新GPU训练，也没有改写历史权重或TensorBoard事件。检查时1756649训练进程仍在运行，已加载启动时旧逻辑；本修改仅对后续启动或恢复的训练生效。未停止或重启该训练。没有验证真实长训练中的新checkpoint产物或学习效果。
