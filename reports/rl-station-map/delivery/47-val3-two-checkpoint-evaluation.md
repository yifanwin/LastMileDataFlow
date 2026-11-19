# val_3 两个checkpoint：质量图与配对五次抓取验证

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T12:21:58+08:00
- 替代：无
- 关联：[500万训练结果](46-val3-cube-5m-results.md)

## 目标与执行

用户要求选择离176万最近、以及400万附近的两个checkpoint，分别输出站位质量图和各5次轨迹视频。检查全部已保存权重（best、latest及周期checkpoint）的实际transitions后，选中：

- `best.pt`：1,669,212条 / 12,884次更新，距1,760,000为90,788条，比1,600,092（差159,908）更近。
- `ckpts/step-004000038.pt`：4,000,038条 / 31,094次更新，距4,000,000为38条。

2026-11-18T12:17:46+08:00在实时空闲的GPU0、GPU1分别启动，两组均使用checkpoint有效配置、5个并行环境、同一份提前冻结的5个不重复站位和逐回合独立随机流seed=20261109至20261113。冻结随机Actor，不使用均值动作；每回合最多50秒，失败不重试，全部5次均输出视频。源码固定为`d681bfa3541d5b6129cab36ad4f504a0d20c18e7`，无源码修改，已有报告改动保留；执行命令、环境和工作区状态见各run的execution.json。

## 站位质量图

两张图都对checkpoint记录的233个候选中的16个有效站位评分，其余217个初始无效点保留掩码。每站128个Actor动作，seed20261109，batch2048：`V=mean(min(Q1,Q2)-alpha*log_pi)`。这是策略条件下的软价值估计，不是成功概率；两个checkpoint的Critic和alpha均不同，绝对数值升高不能单独证明能力提高。

| Checkpoint transitions | alpha | 16站价值最小值 | 最大值 |
|---:|---:|---:|---:|
| 1,669,212 | 0.02816826 | 16.678486 | 20.031053 |
| 4,000,038 | 0.00602181 | 20.501515 | 27.737655 |

质量图对比使用相同色阶，黑圈1–5对应实际试验编号。

![站位软价值对比](../../../outputs/experiments/rl-cube-random-stations/analysis/20261118-val3-two-checkpoints/station-value-comparison.png)

### 初态一致性核查

一次性从真实评估环境reset提取全部16站的69维观测，保存NPZ，评分推理仅消费保存的初态数组，不推进仿真。两份权重的初态数组逐元素完全相同；保存动作、log_prob、Q1、Q2、alpha的NumPy复算与16个站位均值完全一致，所有数值有限。

初次12:16:27启动的两份任务在打分和试验前因离线构造观测检查失败退出，日志原地保留。定位为S0124-y00世界yaw的±pi边界：raw yaw相差约2pi，网络输入却不等价。最终直接采用真实reset的原始69维数组，保留训练观测的yaw符号；其余维度与数值构造最大差5.2453e-5，环绕后的yaw误差约3.02e-7。未修改训练观测、源码或物理参数，也没有将错误构造的yaw用于最终评分。该离线构造边界仍需后续单独修复，本次保存的offline-score-input.yaml仅为诊断配置，正式结果以scores/samples.npz中的observation为准。

## 独立验证结果

| Checkpoint | 成功 | 无碰撞成功 | 基础设施异常 |
|---|---:|---:|---:|
| 1,669,212 | 4/5（80%） | 4/5（80%） | 0/5 |
| 4,000,038 | 5/5（100%） | 3/5（60%） | 0/5 |

两组均执行用户指定的固定5次，所有失败均保留在分母。成功协议仍为双指各>0.5N、方块最低点距支撑面>=1cm、无支撑接触并连续保持0.25秒；无碰撞成功额外要求整个回合无非法碰撞。

| 次数 / 站位 | Seed | 1,669,212结果 / 时长 | 4,000,038结果 / 时长 |
|---|---:|---|---|
| 1 / S0074-y00 | 20261109 | 成功，无碰撞 / 16.05s | 成功，无碰撞 / 4.40s |
| 2 / S0057-y00 | 20261110 | 成功，无碰撞 / 10.15s | 成功，无碰撞 / 17.55s |
| 3 / S0107-y00 | 20261111 | 成功，无碰撞 / 12.65s | 成功，无碰撞 / 5.35s |
| 4 / S0042-y00 | 20261112 | 成功，无碰撞 / 8.65s | 成功，有碰撞 / 4.75s |
| 5 / S0141-y00 | 20261113 | 失败，超时且有碰撞 / 50.00s | 成功，有碰撞 / 6.75s |

较早checkpoint的第5次S0141运行1000策略步到50秒上限；最大底面净空约1mm、最大保持0秒，累计碰撞时间约18.23秒，未满足抬升协议。reason字段为collision_failure，但不是碰撞后立即终止；完整失败轨迹已保留。400万checkpoint在该同站同seed试验成功，但存在碰撞。400万第4次也有碰撞。

这5对试验只说明这些站位和seed下的表现，不能据此断言400万策略的全站位成功率更高，也不能直接推翻训练末期回落的统计；训练统计与冻结策略独立试验的分布和样本量不同。

## 视频与原始产物

全部10个视频完整解码通过，解码帧数与采样帧数一致；20FPS、1280x480，并排全景和夹爪近景，包含初态帧，因此视频时长比仿真时长多0.05秒。第一组第1次和第5次末段、第二组第1次的预览帧已视觉核对。视频文件名前缀12884/31094是更新数，不是transitions。每个run均保存5个replay、5个视频、完整试验清单、逐次结果、评分样本和视频核查结果。

| 次数 / 站位 | 1,669,212视频 | 4,000,038视频 |
|---|---|---|
| 1 / S0074-y00 | [视频1](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/views/actor-000000012884-train-S0074-y00-stochastic-t000.mp4) | [视频1](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/views/actor-000000031094-train-S0074-y00-stochastic-t000.mp4) |
| 2 / S0057-y00 | [视频2](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/views/actor-000000012884-train-S0057-y00-stochastic-t001.mp4) | [视频2](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/views/actor-000000031094-train-S0057-y00-stochastic-t001.mp4) |
| 3 / S0107-y00 | [视频3](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/views/actor-000000012884-train-S0107-y00-stochastic-t002.mp4) | [视频3](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/views/actor-000000031094-train-S0107-y00-stochastic-t002.mp4) |
| 4 / S0042-y00 | [视频4](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/views/actor-000000012884-train-S0042-y00-stochastic-t003.mp4) | [视频4](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/views/actor-000000031094-train-S0042-y00-stochastic-t003.mp4) |
| 5 / S0141-y00 | [视频5](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/views/actor-000000012884-train-S0141-y00-stochastic-t004.mp4) | [视频5](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/views/actor-000000031094-train-S0141-y00-stochastic-t004.mp4) |

- [配对统计JSON](../../../outputs/experiments/rl-cube-random-stations/analysis/20261118-val3-two-checkpoints/comparison.json)
- [评分复算与初态对账](../../../outputs/experiments/rl-cube-random-stations/analysis/20261118-val3-two-checkpoints/score-verification.json)
- 1,669,212：[summary](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/summary.json)、[独立质量图](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/scores/station-value.png)、[全部站位数值](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/scores/station-value.json)、[视频验证](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-best-1669212-g0/video-verification.json)
- 4,000,038：[summary](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/summary.json)、[独立质量图](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/scores/station-value.png)、[全部站位数值](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/scores/station-value.json)、[视频验证](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-121746-val3-eval-4m-4000038-g1/video-verification.json)
