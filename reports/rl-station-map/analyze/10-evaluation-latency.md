# 单站位评估耗时分析

- 状态：done
- 日期：2026-11-13
- 时间：2026-11-13T17:52:42.465589+08:00
- 替代：无
- 关联：../delivery/16-s0246-facing-1m-launch.md、../delivery/17-experiments-reorganization.md

## 实际流程

核对启动提交830a81c；evaluation.py、capture.py、video.py与当前文件完全一致。训练的4个Sampler关闭后，只在首张GPU上使用1个评估环境；128环境/卡的训练并行度不会延续至评估。

1. 初态Q评分：站位抓取池最多16个grasp；每个单独reset、构建观测、采样16个动作，用min(Q1,Q2)评分，再平均到站位。每轮最多256个动作样本，Actor及两个Critic分别计算。
2. 依次跑16次stochastic完整回合；每回合60s、20Hz、最多1200控制步，每控制步25物理子步。记录成功、碰撞、姿态误差、双指接触、抬升、保持、回报与奖励分项。
3. 再跑stochastic与mean各1回合供视频使用。代表视频按成功/碰撞筛选，但每个评估回合都开启capture采集。18回合最多21600控制步、540000物理子步。
4. 写评估JSON、统计与图表。回到train后，对同一单站位再次调用station_scores做最终Q输出；该初态Q评分重复。

## 慢的结构性原因

单环境串行仿真无法摊薄GPU调用开销；capture每控制步执行qpos.cpu().numpy()、误差.cpu().tolist()和多次CUDA标量转Python，形成频繁GPU/CPU同步。即使最终不保存某回合视频，其逐步采集开销仍已发生。额外两个完整回合及末尾重复Q评分增加工作量。

上次完整单站位运行20261112-175934-g67的evaluation.seconds为4022.0667s（约67分钟）；videos中累计render_seconds只有14.786s。16个正式trial的模拟时长合计936.4403s。可确定最终视频渲染并非主要耗时；没有分阶段计时，不能将剩余时间精确分摊给Q、仿真和capture。

本次20261113-165822-s0246-facing-1m-g0123从17:14:28生成selection，到17:44:32被用户停止，约30分钟，无完整评估事件或回放输出。只能确认已进入评估，不能从selection断定停在具体哪个循环。此前依据历史67分钟给出的剩余时间仅为粗估。

## 修复方向（尚未实施）

保持16次试验和抓取评分样本数，先将试验和grasp-Q计算合批；只给选定代表轨迹启用录像采集，将逐步指标留在GPU后批量搬回CPU；复用同一冻结checkpoint的初态Q结果，避免末尾重算；补充Q/rollout/capture-render分阶段耗时及trial进度。

本轮仅分析，未修改算法、预算或程序，未重启评估，未声称优化加速已实测。

后续实施及实测见 [18-parallel-evaluation.md](../delivery/18-parallel-evaluation.md)；本文保留优化前问题与证据。
