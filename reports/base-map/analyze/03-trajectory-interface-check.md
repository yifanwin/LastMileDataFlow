# 轨迹接口与接触几何核对

- 状态：done
- 日期：2026-11-06
- 替代：构建方案中目标开关含义未定的判断
- 关联：../plan/02-feasible-trajectory-quality.md、../requirement/02-feasible-trajectory-quality.md

## 结论

1. Pose-goal 使用 `solve_pose(..., use_implicit_goal=False, goal_state=None)`，固定初态，以 TCP 位姿约束终态，不把输入 IK 代表设为硬关节目标。True 表示启用隐式关节终态边界，不能用于本需求的自由终态。`solve_pose` 注释将 True 解释为自由变量，是错误注释。
2. 证据不是函数名：cuRobo 0.8.0 的 `bspline_boundary_constraint.cuh` 361–366 行，True 调用 `assign_goal_pattern_implicit`，False 调用 `assign_goal_pattern_replicate`；`bspline_gradient_util.cuh` 的 implicit_goal_boundary 分支阻断该末端边界梯度。`solve_joint` 使用 True 与此一致。
3. 复用陷阱真实存在：`manager_goal.py` 174–175 行仅在 True 时赋值，没有在 False 时清零。首版独立保持 Pose-goal solver，所有调用使用 False，不与 Joint-goal 混用。若以后必须切换，应在适配层明确清零对应 goal buffer 并验证，不能只传 False 就假定清零。
4. 多候选接口可用：`solve_pose` 接收 `seed_traj`，`num_seeds`，`return_seeds`，结果包含 `interpolated_trajectory` 与逐候选 `interpolated_last_tstep`。内部 `_get_best_result` 调用 `get_topk_seeds`，默认只返回 1；不能依赖默认保留全部。首版每个逻辑初值一个 batch lane，lane 内 num_seeds=return_seeds=1，最多 8 个真实 lane/grasp；padding 排除统计。截取每 lane 的真实有效长度，不能 flatten 合并多条轨迹。运行张量形状与自由终态运动仍须实际 GPU smoke 核验。
5. 接近方向确定为最终 TCP 的局部 +Z。名义 pregrasp 位于 `p_grasp - 0.10 * R_grasp[:,2]`；原生默认轴 z、负 approach offset，经 grasp 局部变换构造目标。项目 tcp_local 的 roll 绕 z，不改变该轴。资产 TCP offset 0.01 m 与接近距离 0.10 m 是独立参数。

## 允许接触几何

检查实际资产 `/data1/wenyifan/lmdf/molmospaces_data/assets/robots/panda_omron/model.xml`，存在独立的 pad 几何：

- `robot_0/gripper0_right_finger1_pad_collision`
- `robot_0/gripper0_right_finger2_pad_collision`

首版必要目标接触仅允许上述 pad 与当前目标的碰撞几何配对，并受方案中最终接触窗口和容差限制；按实际机器人 namespace 解析名称，不豁免整个 finger body。`hand_collision`、`finger1_collision`、`finger2_collision` 与目标仍需检查，环境和自碰撞不放宽。pad 是 box，名称区分了指腹和外壳，但不是仅有内侧面的单面几何；若要进一步限制只允许指腹内侧表面，需要检查接触位置/法向，不能声称 geom 白名单已区分六个面。

现有 cuRobo `contact_links` 包含整个 panda_finger1/2；`contact_spheres` 在允许时将这些 link 的球对目标检查屏蔽，并没有按 pad、轨迹时刻区分。现有布尔 contact_allowed 不足以直接实现新规则。实施时需要按轨迹阶段/接触窗口限定目标代价，并由 MuJoCo pad 几何验证最终候选；不能沿用整条轨迹豁免两根手指。Open 的掌部 handle_contact_links 不适用于本次 Pick。

## 执行与边界

本轮只读核对工作区代码、实际机器人 XML 和本机 cuRobo 0.8.0 的底层边界/梯度及结果选择实现，确定上述接口选择和接触映射。未修改依赖或算法、未运行 GPU 优化和物理执行；源码确定性与运行验收分开。main 保持干净。
