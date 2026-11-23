# 最低价值站位碰撞对象与判定核查

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T10:29:46.011935+08:00
- 替代：无
- 关联：[最低价值站位五次操作](../delivery/41-lowest-value-station-five-rollouts.md)、[最高价值站位五次操作](../delivery/42-highest-value-station-five-rollouts.md)

## 方法与边界

读取原S0091-y00五次试验summary及replays，使用原checkpoint有效config重建同拓扑模型（GPU0，1环境），对原始每0.05秒qpos和mocap执行CPU MuJoCo mj_forward，不重跑Actor或积分。原replay没有qvel、逐物理子步接触或传感器分项，故qvel置零，只用重建结果定位碰撞几何对，不将其当作原2ms接触力/精确接触时序。原总碰撞时长取原summary；策略步collision标记取原replay.metrics。当前代码commit为`d681bfa3541d5b6129cab36ad4f504a0d20c18e7`；保留工作区已有改动。

分析产物：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-station-value/analysis/20261118-lowest-s0091-collisions`，snapshot-contacts.json保存对应接触对、距离、接触位置及原metrics；summary.json为汇总，diagnostic.log为过程日志。

## 当前判定

环境传感器覆盖机器人自碰撞、机器人与场景body/world geom、非夹指机器人body与目标。手指子树接触目标用于合法抓取，目标接触桌面等单独用于支撑判定；手指接触环境仍属于非法碰撞。正常底盘地面接触从world floor传感器匹配范围排除，手臂仍受监控。最终参与检测的接触仍受模型碰撞掩码、排除对等MuJoCo规则约束。

每0.002秒物理子步，对environment_/nonfinger_/self_collision_传感器读取found>0，任一为真即碰撞，不设额外力阈值、持续时间阈值或毫米穿透容差。step_collision对一个0.05秒策略步内各子步取或；collision_free全回合锁存为假；collision_time按发生任一非法碰撞的子步累计，不把同一子步多个碰撞相加。

该checkpoint的碰撞奖励权重为1，所以碰撞策略步扣1分。碰撞本身不终止回合；成功与collision_free分开，成功判定不包含collision_free。

## 实际对象

| 次数 | Seed | 原始碰撞时长(s) | 原碰撞标记策略步 | 轨迹快照定位 |
| --- | --- | --- | --- | --- |
| 1 | 20261109 | 0 | 0 | 未检测到非法接触 |
| 2 | 20261110 | 11.619523 | 236 | 手掌碰撞体—方块；另有指垫—桌面 |
| 3 | 20261111 | 0.052000 | 2 | 手掌碰撞体—方块 |
| 4 | 20261112 | 0 | 0 | 未检测到非法接触 |
| 5 | 20261113 | 0.190000 | 5 | 手掌碰撞体—方块 |

三次共同接触对为`RL_Cube_geom`与`robot/robot_0/gripper0_right_hand_collision`，对应nonfinger_15传感器，不是合法的finger-target抓取接触。手掌geom来自机器人XML中显式mesh碰撞体，不是cuRobo碰撞球或网络估计。

第2次：原collision标记从8.00秒策略步出现，持续/反复出现到19.75秒结束；233个快照复现手掌—方块接触，范围8.00–19.70秒，模型最大穿透约10.782mm。15.95秒快照还存在finger1_pad_collision与diningtable碰撞体接触，模型穿透约0.0683mm，对应environment_31。第3次原碰撞标记在6.75、6.80秒；6.75秒快照定位手掌—方块，模型穿透约0.413mm。第5次原标记在7.05–7.25秒；7.05–7.20秒四个快照定位手掌—方块，模型最大穿透约0.705mm。

这些距离属于MuJoCo碰撞几何的重建接触距离，不等于现实物体形变量，也不能据此单独证明视觉mesh是否穿透。

## 原因及限制

直接原因是策略将夹爪推进到非夹指手掌碰撞体接触方块的位置；第2次还使指垫擦到桌面。共同问题不是把正常双指抓取误算为非法碰撞。发生首次碰撞的原快照中，姿态误差分别约1.179rad、0.828rad、0.582rad，显示姿态对准仍有明显误差。

算法没有硬性避碰检查或动作投影，碰撞仅是奖励惩罚；69D输入没有显式障碍几何或碰撞状态，因此该Actor未保证执行过程无碰撞。它最终能满足双指抬升保持成功条件，与历史发生碰撞并不矛盾。上述证据不能把根因进一步唯一归结为站位远、奖励权重或某个网络输入；需对应消融才能作因果判断。
