# 最低软价值站位五次操作视频

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T09:54:53.249145+08:00
- 替代：无
- 关联：[离线价值图](39-offline-soft-station-value.md)

## 目标与执行

从39号报告实际评分产物中取96个有效站位的最低软价值点，固定该站执行5次随机Actor操作并渲染全部视频。最低点为`S0091-y00`，软价值25.8207095861，世界(x,y,yaw)=(6.5245336067, 2.0001956089, 0.1243549945)。

使用原评分checkpoint：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/ckpts/step-007200126.pt`。7,200,126 transitions，56,094次更新；直接使用checkpoint有效配置，69维观测、固定物理底盘，GPU0、5个并行环境。原有共享`run_evaluation_trials`执行显式5条试验计划，独立动作随机流seed=20261109–20261113，无重试，无源码修改。

运行代码基线为`eedce7954d906ce5eeff8228a96acf7b99744c62`，工作区dirty，启动时状态保存在execution.json。当前工作区已有移动底盘等未提交改动，不能声称与原离线评分时源码逐字相同；本次使用的有效配置仍来自原固定底盘checkpoint。

## 结果

成功条件沿用checkpoint的rl-pick-contact-v2：双指接触力均超过0.5N、方块离开支撑且底面净空至少1cm，保持至少0.25秒。5/5成功，无碰撞成功2/5；基础设施失败0/5。成功不要求全过程无碰撞，故两项分别统计。

| 次数 | Seed | 仿真时长(s) | 成功 | 无碰撞 | 最大底面净空(m) | 最大保持(s) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 20261109 | 4.05 | True | True | 0.019010 | 0.288 |
| 2 | 20261110 | 19.75 | True | False | 0.041084 | 0.278 |
| 3 | 20261111 | 7.20 | True | False | 0.029833 | 0.252 |
| 4 | 20261112 | 7.55 | True | True | 0.018130 | 0.266 |
| 5 | 20261113 | 7.45 | True | False | 0.031882 | 0.284 |

## 产物与验证

Run：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-station-value/runs/20261118-lowest-s0091-5trials-g0`。包含有效config、selection、execution、trials、summary、日志、5份replay和5个MP4。全部视频完整解码退出0，检查见video-verification.json；抽取第1次2秒帧核对全景和夹爪近景均正常。视频20FPS、1280×480，含初态帧，因此比仿真时长多0.05秒。

视频文件名前缀56094是更新数，不是transition数。trials中沿用既有reason="simultaneous_contact"标签，但实际success仍为上述完整抬升与保持协议，以success、max_lift_m、max_hold_s共同核对。

本次只验证最低价值站位的5次实际表现，不能据此推出全站位成功率或价值排序与成功率的相关性。

后续碰撞对象核查见[最低价值站位碰撞分析](../analyze/43-lowest-value-station-collisions.md)。
