# Cube 最低点离桌面、接触力与姿态奖励调整

- 状态：done
- 日期：2026-11-17
- 时间戳：2026-11-17T16:34:43+08:00 起实施
- 替代：无；历史实验记录保留原口径
- 关联：[运行指南](running.md)、[上一版阶段峰值奖励](29-cube-stage-peak-success-best-training.md)

## 目标与实现

工作区 `.worktrees/rl-cube-contact`。保留任务开始前已有修改，包括 training.py、选模测试和独立评估改动。

- 当前 cube 配置：`success.finger_force_min_n: 0.5`、`success.lift_m: 0.01`、`reward.rotation: 1.0`。
- 同一物理子步左右指对 cube 的法向力均严格 >0.5 N，并且 cube 最低点距原桌面 ≥0.01 m，才触发成功；现有失败门控、成功锁存与终止行为不变，无新增保持时间。
- 桌面高度固定为准备场景中初始直立 cube 中心高度减去半边长；对应场景生成时选定的真实支撑面，不使用 episode 中心基线推断桌面。
- 统一最低点离桌面高度 `h = z_center - (size_m / 2) * sum(abs(R_world[2, :])) - z_table`，按实际世界姿态计算最低角点。
- 抬升奖励进度改为 `clip(h / 0.01, 0, 1)`；沿用阶段重入不补发、持续阶段峰值增量、每回合累计最多5分的机制。
- 姿态奖励为 `1.0 * (angle_previous - angle_current)`，角度仍是原始弧度。
- 成功、抓握事件、抬升阶段、首次双指接触时间、评估与视频中的双指接触统一为 >0.5 N。共享计算位于 env/terms.py，capture.py 与 evaluation.py 直接复用。
- 评估 `max_lift_m`、视频 `lift` 改为最低点离桌面高度，视频 `peak` 按配置中的目标高度换算。65维观测字段、尺度和动作接口不变；legacy Cup 的中心相对抬升与接触门槛不变。

## 验证

使用已有 actor-critic 独立解释器与当前工作区 `PYTHONPATH=src`，没有安装或下载依赖。

- 相关测试40项全部通过（4.004秒），`git diff --check` 通过。覆盖成功、奖励、观测、场景、评估、视频；新增倾斜 cube、严格0.5 N、评估与视频口径用例，更新1 cm边界、同子步条件和姿态奖励预期。
- station_map 离线测试：106项，105项通过；TensorBoard `test_resume_purges_future_steps_and_new_output_stays_independent` 在整组测试中失败，读取到 `[160,100,200]` 而非 `[100,160]`。同一测试单独运行通过，说明存在顺序或时序相关现象，本次未改动 monitor.py 或该测试。
- 全套离线回归首先使用 `MUJOCO_GL=egl`，被 EGL 初始化的 `eglQueryString` 错误阻挡。
- 随后使用 `MUJOCO_GL=disable` 完整执行381项：367通过、3跳过、2失败、9错误。除上述 TensorBoard 失败外，其余为缺少 curobo / rtree，以及 curobo 缺失导致规划异常文本与预期不符；没有将全套回归记为通过。
- 临时检查日志：`/tmp/rl-cube-clearance-local-tests.log`、`/tmp/rl-cube-clearance-station-tests.log`、`/tmp/rl-cube-clearance-regression.log`、`/tmp/rl-cube-clearance-regression-disable.log`、`/tmp/rl-cube-clearance-monitor.log`。

## 运行限制

本次未启动训练、未执行真实GPU rollout或策略成功率评估，未修改历史run、checkpoint与实验结果。离线测试只验证所述软件与数值边界，不证明Actor已学会新任务。
