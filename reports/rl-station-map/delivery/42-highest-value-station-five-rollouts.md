# 最高软价值站位五次操作视频

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T10:03:09.607852+08:00
- 替代：无
- 关联：[离线价值图](39-offline-soft-station-value.md)、[最低价值站位五次操作](41-lowest-value-station-five-rollouts.md)

## 目标与执行

从原评分JSON取96个有效站位中软价值最高点`S0078-y00`，价值32.9757150710，世界(x,y,yaw)=(6.9189965327, 1.9015798774, 0.4636476090)。固定该站执行5次随机Actor操作并渲染全部视频。

Checkpoint：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/ckpts/step-007200126.pt`。7,200,126 transitions，56,094次更新；直接使用checkpoint有效配置，69维观测、固定物理底盘，GPU0、5个并行环境。沿用共享run_evaluation_trials与capture_all=True，显式5条试验计划，独立动作随机流seed=20261109–20261113，与最低价值站位五次试验一致，无重试、无源码修改。

运行代码基线`eedce7954d906ce5eeff8228a96acf7b99744c62`，工作区dirty；启动时状态保存在execution.json。工作区已有其他未提交修改，不声称与最初评分时源码逐字相同。

## 结果

抓取成功4/5，无碰撞成功3/5，基础设施失败0/5。第2次发生过碰撞，持续至50秒上限仍未成功；保留完整失败视频。成功协议仍为双指接触力均超过0.5N、底面净空至少1cm、无支撑接触并连续保持至少0.25秒。

| 次数 | Seed | 仿真时长(s) | 成功 | 无碰撞 | 最大底面净空(m) | 策略步采样最大保持(s) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 20261109 | 2.10 | True | True | 0.021198 | 0.274 |
| 2 | 20261110 | 50.00 | False | False | 0.011326 | 0.066 |
| 3 | 20261111 | 6.10 | True | False | 0.017012 | 0.284 |
| 4 | 20261112 | 2.45 | True | True | 0.019314 | 0.260 |
| 5 | 20261113 | 2.55 | True | True | 0.012579 | 0.224 |

第5次success=true但max_hold_s=0.224：成功在每0.002秒物理子步判断并锁存，max_hold_s由评估器每0.05秒策略步采样，两者采样频率不同；该统计不是物理子步保持峰值，不以其低于0.25秒覆盖环境success。已核对terms.py中的逐子步hold累计及success按位或锁存、environment.py中per_substep=True。未额外保存逐物理子步完整hold曲线。

既有reason标签对成功使用simultaneous_contact，对有碰撞的失败优先使用collision_failure；第2次确实执行1000步到50秒上限，不应将标签解读为碰撞发生时立即终止。

## 视频与验证

Run：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-station-value/runs/20261118-highest-s0078-5trials-g0`，保存有效config、selection、execution、trials、summary、日志、5份replay及5个MP4。视频20FPS、1280×480，并排全景/夹爪近景；含初态帧，视频比仿真时长多0.05秒。全部5个视频完整解码退出0，见video-verification.json；第1次1秒预览帧已核对。文件名前缀56094为更新数。

本次最高价值站位成功4/5；此前最低价值站位成功5/5。这10次试验不能用于判断全站位价值排序与成功率的相关性。
