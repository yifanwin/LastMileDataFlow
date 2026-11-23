# PPO 移动底盘：初始采样半径扩大 1.5 倍

- 状态：active
- 日期：2026-11-20
- 时间戳：2026-11-20T14:14:30+08:00
- 替代：无
- 关联：[移动 PPO 总结](55-ppo-cup30-mobile-summary.md)、[运行指南](running.md)

## 目标与修改

按用户要求放开底盘移动，初始站位采样范围扩大为原有1.5倍，用物理 CUDA0–3 开始新训练。沿用 Cup_30 移动 PPO 配置：fixed_base=false、mobile_base=true，10维动作；底盘每步平移0.03 m、转角0.15 rad，初始 std 0.1。新增 stations.radius_scale，默认1；本配置取1.5。采样半径从0.8469596623341055 m变为1.2704394935011583 m，间距仍为0.09861573150365006 m，朝向目标。保留初态物理碰撞、支撑和局部边界筛选；扩大的是候选圆盘，实际有效站位依场景筛选决定。

## 验证

MUJOCO_GL=disable、PYTHONPATH指向当前工作区，tests.station_map.test_rl_scene 8项通过，覆盖默认几何与扩大半径后的新候选。初次使用egl的测试在导入阶段因本机EGL库未加载失败；无渲染测试和训练沿用历史的disable设置。

## 训练

从零训练，seed42，CUDA0–3，Learner在CUDA0，每卡512环境，共2048环境。预算2000万健康样本，32步/rollout，约306轮、每完整轮80次minibatch更新，约24480次；实际以健康样本和最后一轮为准。上轮吞吐约2740条/s，据此预计约2小时，扩大范围的实际耗时待运行核查。20 s回合，其余奖励、PPO和checkpoint设置不变。配置、命令、commit、PID和启动日志保存到新run；旧run保留。

训练启动与进度以 outputs/experiments/ppo-cup/README.md 和新run的 launch.json、metrics.jsonl 为准。本次交付为调整并启动训练，尚未证明扩大范围后的学习效果。

## 停止与修正（2026-11-20T14:36+08:00）

上一节的 run `20261120-141450-cup30-mobile-ppo-radius15-20m-e512-g0123` 按用户要求于282万条停止（未评测）。

**底盘位置偏移 bug**：mjlab 机器人实体的底盘平面关节为0时，base_site 在世界系 (+0.21, 0) m；移动底盘的 reset（terms.py）和站位检查（stations.py）都把站位坐标直接写进关节，所以每回合实际起点比站位往 +x 偏21 cm，检查也按偏后的位置判穿透，原233点网格只剩56个有效站位（固定底盘按 mocap 精确放置，为96个）。yaw 轴过 base_site，偏移与朝向无关。日志里的 `base_xyyaw_initial/world` 是关节值，因此看不出偏移。此前所有移动底盘 run（PPO 报告54/55、rl-mobile 的 SAC）都受影响：站位图上的点比机器人实际起点偏西21 cm，离桌最近一圈站位从未训练。

修正（f3233f7）：`stations.base_joint_offset` 从模型算出关节零点处的 base_site 位姿，reset 与站位检查都用 关节=站位−偏移。验证：重置后 base_site 与站位误差<1e-6 m；原网格有效站位恢复为96（与固定底盘一致）。`base_xyyaw_*` 日志字段仍是关节值（=站位−偏移）。

**网格**：新增 `stations.spacing_scale`（默认1）。按用户要求本配置半径×1.5（1.270 m）、间距×2（0.197 m）：137个候选、61个有效（半径×1.5、间距不变时为233/109）。test_rl_* 136项通过。

**训练**：`outputs/experiments/ppo-cup/runs/20261120-143608-cup30-mobile-ppo-r15-s20-basefix-20m-e512-g0123/`，提交 f3233f7，CUDA0–3、每卡512环境、seed42、2000万条，其余与报告55相同。启动约2.5分钟后到32.8万条，无报错。
