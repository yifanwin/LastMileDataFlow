# 评测协议统一：判据、自由度、底盘、噪声与站位

- 状态：active
- 日期：2026-11-24
- 替代：[05](05-quality-alignment-plan.md) §1 中 σ 的停靠/初始臂姿成分、§4 偏航扩展站表
- 关联：[04 首对实验构建计划](04-paired-station-experiment.md)、[05 目标对齐计划](05-quality-alignment-plan.md)

## 1. 统一后的协议

| 项 | 所有方法统一为 | 实现位置 |
|---|---|---|
| 成功判据 | 原生 `PickJudge`（`robocasa-pick-bottom2cm-0.5s-v2`）：左右指垫都接触目标、目标不接触任何环境几何体、最低碰撞顶点比冻结桌面高 ≥2 cm，三者连续保持 ≥0.5 s，每个 20 Hz 控制步判一次 | 原生 `operation/outcomes/robocasa_pick.py`；mjlab `TaskState.judge_pick`（同一组常量，`terminated()` 里每个策略步调用一次，指垫按 `found` 接触判，不再用 0.5 N 力阈值） |
| 自由度 | 7 DoF 臂，torso 锁在 0.00 m | 原生 `lock_base()` 把 torso 夹到 [0,0] 并丢弃 torso 命令；mjlab `scene.lock_torso: true` 删掉 torso 关节，DLS 7 关节，观测里 lift 一格恒为 0；cuRobo/IRM/B2 本来就是 7 关节，基线导出前也调用 `lock_base()` |
| 底盘 | 原生：保留底盘关节，按阶段锁定；mjlab：rl-ppo 的 mocap 固定底盘 | 见 §2 |
| 终止和碰撞 | rl-ppo 固定底盘随机站位的分档扣分：小/中/大首次 0.5/0.8/1.0、持续每步 0.1/0.5/0.5，断开 0.25 s 后可再计首次；2 s 窗口内碰撞占比 ≥50%、TCP 路程 <2 cm、距离和姿态都无进展判卡住，终止并扣 10；目标中心下降 >30 cm 终止并扣 20；接触本身不再终止（`termination.illegal: []`），关节超限和时限仍为真终止 | RoboCasa PPO 配置的 `reward`、`termination`；`FAILURE_REASONS` 新增 `stuck` |
| σ 扰动（Ours） | 只保留感知误差（目标位置偏置 `target_m` 5 mm）和动作噪声（`action` 0.1），都乘 σ | 删除停靠抖动（`dock_m`/`dock_yaw_rad`）和初始臂姿噪声（`home_noise_rad`） |
| cuRobo 随机性 | 单独保留停靠扰动 ±1 cm/±1°，每站 K=15 | `set_station` 写底盘关节时加入，之后 `lock_base()` |
| 站位 | 只评正对目标的站位：40 个 XY 全部合法，π0.5 可见 39 个 | `station-table/kettle-far__kettle-left-125mm-0deg/stations.jsonl`（`robocasa_station_table.py` 默认偏航改为 `[0]`） |

## 2. P0-4 调整：原生底盘保留关节、按阶段锁定

最初方案是原生侧也删掉平面关节，改用 mocap 根。用户改为：

- `lock_base(torso_m=0.0)`：夹平面关节范围、torso 夹到 0.00 m、屏蔽底盘和 torso 命令、强制 arm mode，并同步控制器目标；
- `unlock_base()`：恢复原生关节范围、同步控制器目标、放开底盘命令，为以后的“移动—操作—再移动”留接口；
- `_place_base` 仍写底盘关节，cuRobo 停靠扰动从这里加；每回合记录底盘和 torso 最大漂移。

原因：移动阶段需要原生底盘动力学和控制器，删关节以后无法恢复；夹关节的漂移（实测约 0.01 mm）与 mocap 的 0 之差远小于 cuRobo 的停靠扰动（1 cm）。mjlab 侧不需要移动阶段，维持 mocap 固定底盘不变。

## 3. 验证（2026-11-24 16:20–17:00）

| 门槛 | 结果 | 产物 |
|---|---|---|
| 单元测试 | 新增 `tests/station_map/test_rl_native_pick.py` 3/3：同一串事实喂给 `PickJudge` 和 `judge_pick`，200 组随机序列成功步完全一致；`test_robocasa_pick` 7/7；RL 相关 95 个测试中 12 个报错与改动前基线相同（`test_rl_rewards`/`test_rl_ppo` 原有问题），没有新增 | — |
| 原生锁定漂移（S0022，两场景，200 步随机强底盘和 torso 命令、base mode 置 1） | 底盘 0.0075 mm / 0.0015°，torso 0.006 mm，门槛 0.1 mm / 0.05° 通过；`unlock_base()` 后 40 步前进 0.17 m | `outputs/experiments/scratch/20261124-plan06-gates/lock-*.json` |
| mjlab 环境冒烟 | 模型无底盘和 torso 关节，DLS 7 关节，动作 7 维，40 站，噪声只有 `action`/`target_m`；16 个随机回合在 400 步时限真终止 | `.../mjlab-env.json` |
| PPO 短训练 | 32 env、8192 transitions、8 次更新，写出 checkpoint | `runs/20261124-1645-plan06-ppo-smoke` |
| cuRobo 小样（S0022 两场景，K=2 停靠扰动） | 4/4 成功；底盘漂移 ≤0.001 mm，torso ≈0.002 mm | `runs/20261124-1646-plan06-curobo-smoke` |
| π0.5 冒烟（S0022 两场景，seed 42，1000 步） | 0/2（与 05 记录的 seed 42 far 0/3 一致）；底盘漂移 0.004 mm，torso ≤0.015 mm；π0.5 原始 torso 命令恒为 0，锁 torso 不削弱它 | `runs/20261124-1647-plan06-pi05-smoke` |
| 跨仿真回放（3 站 × 2 场景名义 cuRobo 轨迹，原生 JOINT_POSITION 与 mjlab 绕过 DLS 直接给关节目标） | 6/6 两边都判成功；成功步差 mjlab − 原生 = −1、−4、−1、−1、−1、−1（far S0022 差 0.2 s），结果一致，时刻 5/6 在 1 步内 | `runs/20261124-1650-plan06-curobo-nominal`、`.../replay-*.json` |

## 4. 影响和遗留

- 以前的 M2 小样、M4 stage A/A2/A3 checkpoint 都按旧口径（力阈值判据、非法接触终止、7+1 DoF、118 站）产生，不进正式结果；`stage_a` 配置已删除，`stage_a3` 和 `station_quality` 已同步新协议，且不再从旧 checkpoint 初始化。
- 观测和动作维度没变（lift 一格恒为 0，动作仍是 TCP 6 维加夹爪），旧 checkpoint 技术上能加载，是否用于初始化需另行决定。
- 跨仿真回放里 far S0022 差 4 步，原因未查（可能是持续窗口在原生侧中断了一次）；判据结果一致，暂不处理。
- 评审中提出、本次未做、需要用户决定的：左场景是泛化测试还是进入训练；π0.5 每站 seed 数（建议 10）；预先登记 S 退化（方差过小、饱和）时的处理规则。
