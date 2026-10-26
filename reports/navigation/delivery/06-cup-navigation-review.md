# Cup r02 导航负结果复核

- 状态：done
- 日期：2026-10-26
- 替代：无；更正 05 号清单中 Cup“没有导航路径”的解释，保留原 run 数字
- 关联：[运行清单](../../task-modes/delivery/05-run-inventory.md)、[下游交付](../../task-modes/delivery/03-downstream-delivery.md)、[实施方案](../../task-modes/plan/01-task-mode-implementation.md)、[任务需求](../../task-modes/requirement/01-task-mode-construction.md)

## 1. 结果

用户指出的问题成立：`p5-mode-bench-20261026-r01` 的 `no_navigation_path=7` **不能解释为真实场景中七个起点均无路**。

复用原 Cup r02 快照、原机器人配置和原诊断选定的 S1，七个低率 S0 全部在搜索入口被保守膨胀栅格屏蔽；每个 episode 的恢复区域仍有 63 个目标，问题不是恢复区域为空，也没有运行到自由区域连通性搜索。

本次为七个 S0 分别找到通往合格 S1 的候选路线，363 个 MuJoCo 静态位姿检查全部通过，未出现非地面机器人接触。**这是实际模型上的几何采样结果，尚未验证动态跟踪；不生成 trial、episode 或导航成功记录。** 原 benchmark 的 `7 → 0 → 0` 保持不变。

## 2. 为什么会被判无路

调用链为 `_navigation_region → scene_grid → RecoveryRegion → difficulty → search_region`。

1. [`_navigation_region`](../../../src/lastmile_dataflow/workflows/lastmilebench.py) 测量所有合法站位的整机外接圆，取最大半径 **0.480969839 m**、最大高度 **1.569045406 m**，加导航 margin **0.02 m**。因此，栅格要求障碍距离至少 **0.500969839 m**。
2. [`scene_occupancy`](../../../src/lastmile_dataflow/planning/astar.py) 把与机器人高度区间相交的碰撞 geom 的世界坐标 AABB 投影到 XY；桌面高度为约 **0.673–0.750 m**，因此整个桌面投影成为障碍。`task_support_obstacles` 又把桌子的整体包围盒作为支撑障碍，桌下空间同样被预留。
3. 这个模型相当于用一个覆盖整机高度、与朝向无关的圆柱绕行。实际机器人在各高度的截面不同，底盘也有朝向；站位初始化用真实几何判断合法，导航圆柱却可能判非法。两种判定的保守程度不一致。
4. `search_region` 首先调用 `segment_free` 检查起点连接；起点所在格不自由便立即返回 `None`。[`build`](../../../src/lastmile_dataflow/workflows/lastmilebench.py) 将 `difficulty=None` 统一记录为 `no_navigation_path`，未区分“起点被近似模型屏蔽”和“自由区域不连通”。

| S0 | 原栅格障碍距离 m | 要求距离 m | 起点格 | 本次候选路线 | 平移长度 m | 检查位姿数 |
|---|---:|---:|---|---|---:|---:|
| S0014 | 0.364645 | 0.500970 | blocked | S0014 → S0021 | 0.354 | 37 |
| S0022 | 0.364645 | 0.500970 | blocked | S0022 → S0021 | 0.250 | 27 |
| S0030 | 0.367758 | 0.500970 | blocked | S0030 → S0029 | 0.250 | 27 |
| S0038 | 0.300055 | 0.500970 | blocked | S0038 → S0044 | 0.250 | 27 |
| S0040 | 0.264645 | 0.500970 | blocked | S0040 → S0044 | 0.559 | 57 |
| S0041 | 0.264645 | 0.500970 | blocked | S0041 → S0044 | 0.791 | 81 |
| S0042 | 0.264645 | 0.500970 | blocked | S0042 → S0041 → S0044 | 1.041 | 107 |

障碍距离是原 0.05 m 栅格的保守距离场读数，不能当成真实机器人表面净空。三个高率站 **S0001、S0007、S0037** 也被同一栅格屏蔽，进一步说明该模型不能完整表达已验证合法的站位。

例如 `S0022` 与 `S0021` 的 XY 分别约为 `[6.813457, 2.098837]` 和 `[6.563457, 2.098837]`。桌面左缘为 `x=7.193399`；S0022 的中心距桌面投影约 0.380 m，外接圆模型会拦截它，但实际机器人可向左平移 0.25 m 并小幅调整朝向到达 S0021。

![物理投影与原膨胀栅格对比](../../../outputs/diagnostics/navigation/cup-navigation-20261026-r01/result/navigation-comparison.png)

橙色为低率 S0，绿色为高率站，蓝线为本次采样路线；右图黑色包含膨胀排除区域，不能当成真实物体轮廓。

## 3. 复核方法与限制

- 读取原快照，保持物体状态不变；全部检查在 `Simulation.begin()` 之前进行，不积分、不运行控制器。
- 使用诊断保存的实际 base/yaw；终点属于该 S0 原诊断的 `high_station_ids`，本次选中的 S0021、S0029、S0044 均为原标定 3/3 成功站。
- 每段 XY 线性插值、yaw 沿最短角插值；相邻采样平移不超过 0.01 m、转角不超过 1°。手臂、升降和夹爪保持源初始化，并检查它们与终点初始化一致。
- 每个位姿执行项目已有 `robot_contacts` 与 `floor_support` 检查；环境/自碰撞/地面的初始化穿透阈值分别为 −0.001/−0.002/−0.005 m。本次额外读取全部机器人接触，363 个位姿均没有非地面接触。
- S0042 直接插值到 S0044 会碰到橱柜；经 S0041 分段后通过。这个实际反例也说明不能只把半径调小或强制把起点格改成 free。
- 采样检查不证明采样间的连续碰撞自由，也不证明控制器可以跟踪、满足原 0.02 m margin 或最终操作成功。没有新物理步，不替换已有 benchmark 结果。

## 4. 执行产物与复现

产物位于 [cup-navigation-20261026-r01](../../../outputs/diagnostics/navigation/cup-navigation-20261026-r01)：`probe.py`、`result/summary.json`、`result/navigation-comparison.png`。这是诊断附件，不计入原清单的 60 个数据 run。

在仓库根目录执行，输出使用新的目录：

```bash
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python outputs/diagnostics/navigation/cup-navigation-20261026-r01/probe.py \
  --output outputs/diagnostics/cup-navigation-recheck-01
```

实际输出：`Blocked starts: 7 / 7`、`Sampled routes passing initialization checks: 7 / 7`、`Samples: 363; physical steps: 0`。本次只运行 CPU 几何复核，未修改生产代码，不重跑标定、采集或策略评测。

## 5. 后续修复方向

需要修复的是导航近似与合法站位之间的连接，以及失败原因表达。保守栅格可继续用于远处搜索，但合法 S0/S1 到栅格的局部连接应按实际机器人几何和朝向检查，旋转过程也必须检查；不能直接放开 blocked 格或删除桌子障碍。输出应区分起点被近似模型屏蔽、恢复目标被屏蔽与真正搜索不连通。

本报告记录原因定位、静态几何复核和文档更正。后续已按[导航几何修复方案](../plan/03-navigation-geometry-fix.md)完成代码修复、Cup/Open 新构建、Train 规划复核和四条实际导航，见[修复交付](07-navigation-geometry-fix.md)。后续结果保留 2 cm 导航余量，因此不将本报告七条无接触采样路线直接当作七条合格导航。
