# RoboCasa 闭爪阶段底盘保持修复

- 状态：done
- 日期：2026-10-26
- 替代：08-mobile-base-hold-fix.md
- 关联：[标定交付](02-calibration-delivery.md)、[构建交付](01-build-delivery.md)、[下游与验证](03-downstream-delivery.md)、[计划表](../plan/02-build-schedule.md)

## 1. 问题与证据

修复前完整标定中，IK/轨迹有解但执行常见空夹或 `open_no_grip`。只读恢复真实轨迹后，抽查 Cup 失败误差 1.38–3.18 mm、成功误差 1.17–2.76 mm，另有 Open 失败误差仅 0.81 mm；不能归因为跟踪容差过宽，未调整容差或成功判据。

继续检查闭爪末帧，发现机器人已离开把手。原轨迹实际底盘位置：

| 试验 | 初始 XY/yaw | approach 结束 | close 结束 |
|---|---|---|---|
| Open r000011 | [4.466742, 0.640834, −0.071495] | [4.795198, 0.508130, −0.455689] | [4.393402, 0.624984, −0.070676] |
| Cup r000002 | [6.563457, 1.848837, 0.175574] | [6.787826, 2.026148, 1.391213] | [6.555630, 1.802220, 0.167057] |

来源分别为 `p4-high-open-full-20261026-r01` 与 `p4-cup-full-20261026-r01/calibration/rollouts/`；各阶段坐标是记录的控制边界，无离线积分。

## 2. 原因与最小改动

普通执行的 `ContinuousContext.mobile_tick` 会更新 `manip_base_target`；RoboCasa 的 `follow_points` 使用独立的 FK→OSC 跟踪分支，提前返回，没有同步该目标。`manipulate` 开始前把它设为初始站位，随后 `arm_tick` 在闭爪/保持时继续发送旧目标，把底盘拉回原站位。

仅在 `runtime/execution/robocasa_tracking.py` 的每个参考点同步最后规划底盘目标，与已有 `mobile_tick` 保持一致。未改变 OSC、动作定义、控制 profile、跟踪容差、接触约束或任务成功判定；未改写 qpos、恢复运行中状态或传送。

## 3. 验证

- 新增离线回归 `test_close_holds_the_base_reached_by_the_trajectory`，实际串接 `follow_points`→`ContinuousContext.arm_tick`，检查闭爪目标与 TCP 不回到初始底盘。该 fixture 只验证接口与阶段衔接。
- 相关局部 14 项通过；修复当时全套 **352 项：350 通过、2 跳过**。标定交付时为 354 项，场景预览改动后最新为 355 项，见[标定交付](02-calibration-delivery.md)。
- 真实 `p4-evidence-20261026-r03/calibration`：与旧 r02 相同 S0013、相同 seed 42/43/44、相同执行配置。旧为 0/3，新为 **2/3 mobile_pick 成功**；另一条仍为 `robocasa_reference_tracking_timeout`。新 r000002/r000000 分别执行 360/421 步。
- 新 r000002 在 approach→close 的底盘 XY 差约 4.76e-8 m，新 r000000 约 2.71e-7 m；原 r000002 回退约 0.32 m。

## 4. 后续与限制

- 原负结果全部保留，不重新标为成功。r03 仅覆盖 3/60，仍判 incomplete，不作为完整困难配对或最终标签。
- 修复后新建完整 Cup r02 与显式把手 Open r02，最终得到 Cup 34/60、Open 14/60；完整结果与六类标签正例见[标定交付](02-calibration-delivery.md)。
- 修复前结果不能用于宣称修复后的任务固有困难；旧运行不覆盖、不混合。
