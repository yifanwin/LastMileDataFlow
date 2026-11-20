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
