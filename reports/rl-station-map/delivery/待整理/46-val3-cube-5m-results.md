# val_3 方块500万条训练结果

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T12:06:58+08:00
- 替代：无
- 关联：[启动与配置](45-val3-cube-random-stations-5m-training.md)

## 完成与统计口径

完整完成 5,000,000 条健康经验与 38,906 次 SAC 更新，采样耗时 1,607.9 s（26.8 min），吞吐约 3,110 条/s。约 11:54 结束，主进程已退出；`latest.pt` 和 TensorBoard 最终步均为 5,000,000。保留 12 个每40万条周期 checkpoint。

共记录 11,219 个结束回合，114 个 unhealthy（约 1.02%）；512 个包含随机动作预热，其中健康预热 497 个。以下仅统计健康、已完成、无预热的 10,608 个回合；失败保留在分母。分段按回合记录的全局 transition 归属，Actor 持续更新，区间回合数会受回合时长影响，不是等量独立试验。预算末未结束的回合不计入完成回合指标。

成功沿用接触协议：双指各 >0.5 N，方块最低点离支撑面至少1 cm、无支撑接触，连续保持0.25 s。成功不要求全回合无非法碰撞；无碰撞成功率单独统计。

## 结果

| 健康经验区间 | 回合数 | 成功率 | 无碰撞成功率 | 平均 return |
|---|---:|---:|---:|---:|
| 0–100万 | 162 | 20.4% | 17.9% | 21.19 |
| 100–200万 | 2,056 | 68.4% | 67.0% | 40.50 |
| 200–300万 | 3,025 | 83.0% | 73.6% | 38.82 |
| 300–400万 | 3,188 | 81.6% | 66.1% | 34.67 |
| 400–500万 | 2,177 | 67.6% | 48.0% | 32.08 |
| 最后50万 | 836 | 42.6% | 29.3% | 21.04 |
| 最后100个合格回合 | 100 | 51.0% | 42.0% | 25.56 |

总体成功 8,025 / 10,608（75.7%），无碰撞成功 6,784 / 10,608（64.0%）；这是全训练混合策略统计，不能代表最后策略。最后50万的836个合格回合中有479次未成功超时，317个回合发生非法碰撞；最后100个回合有49次未成功超时、24个回合发生碰撞。表现中期提高、末期退化，不能只用总体75.7%或某次滚动峰值描述最终能力。

### 奖励分解

| 平均每回合奖励分量 | 200–300万 | 最后50万 |
|---|---:|---:|
| position | 4.757 | 5.073 |
| orientation | 5.096 | 4.392 |
| collision | -5.368 | -6.384 |
| success | 24.893 | 12.775 |
| closing | 0.651 | 0.074 |
| grasp | 1.352 | 0.262 |
| lift | 7.441 | 4.845 |

末期 return 下降主要伴随成功奖励减少、抬升奖励减少，碰撞惩罚也加重；位置奖励没有同步下降。因此已有统计支持“完整抓取表现回落”，不能仅归因于到达位置变差。具体策略退化机制尚未通过轨迹与视频定位。每回合 return 与分量和的最大差约 0.00043，为浮点累加量级。

### Checkpoint

- `latest.pt`：5,000,000 条，38,906 次更新。
- `best.pt`：1,669,212 条，12,884 次更新；保存时最近100个健康非预热训练回合成功97次（97%）。
- 训练尾部先保存 latest，再加载 best，因此控制台最终摘要的 updates=12,884 是 best 的更新计数；最终训练更新数以 `sampling_complete`、latest.pt 和 TensorBoard 的38,906为准。

本轮没有独立 checkpoint rollout 评估或代表性视频。97% 是保存时的训练滚动窗口，不是固定 best 策略的独立成功率。下一步若验收抓取能力，应独立比较 best 与 latest，保持相同站位/随机种子/固定试验数，分别报告成功及无碰撞成功。

2026-11-18后续补充：已完成用户指定的best（离176万最近）与400万checkpoint配对各5次独立试验、站位质量图和全部10个视频，见[独立验证报告](47-val3-two-checkpoint-evaluation.md)。该后续实验未使用latest，不改写上述训练统计。

## TensorBoard

```bash
/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-random-stations/.venv/bin/tensorboard \
  --logdir /home/wenyifan/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-random-stations/outputs/experiments/rl-cube-random-stations/runs/20261118-112631-val3-cup12-random-facing-5m-g0123/tensorboard \
  --port 6006 --bind_all
```

重点查看 `train/success`、`train/return`、`train/reward/*`、`train_interval/*`、`loss/*`、`policy/*`、`runtime/*`。`train/*` 为最近最多100个合格回合；`train_interval/*` 为30秒汇总区间。`train/collision_free` 是无碰撞回合比例，不是无碰撞成功率；最后100回合该值为76%，与成功条件取交集后的无碰撞成功率为42%。本轮未写独立评估结果。

统计产物：[val3-5m-training-summary.json](../../../outputs/experiments/rl-cube-random-stations/analysis/val3-5m-training-summary.json)，包含各站位、奖励分量、分段指标及 TensorBoard 最终标量；来源为本 run 的 `metrics.jsonl`、checkpoint 与事件文件。
