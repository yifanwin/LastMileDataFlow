# 导航几何误拒绝修复交付

- 状态：done
- 日期：2026-10-26
- 替代：接续 06 号原因复核；原实验 run 和计数不改写
- 关联：[修复方案](../plan/03-navigation-geometry-fix.md)、[原因复核](06-cup-navigation-review.md)、[运行清单](../../task-modes/delivery/05-run-inventory.md)、[下游交付](../../task-modes/delivery/03-downstream-delivery.md)

## 1. 执行过程与实际改动

完成“真实几何局部连接 + 保守 A* 全房间绕行”的共用实现，保持原 0.02 m 导航余量，未重新标定或重做模式分类。

1. 新增 [`planning/navigation.py`](../../../src/lastmile_dataflow/planning/navigation.py)。在独立准备态中保持源手臂、升降和夹爪，按 ≤1 cm / ≤1° 采样检查实际整机碰撞、地面支撑和机器人—环境净空。MuJoCo 距离查询保留碰撞掩码，允许的底盘/轮—地面接触除外；不修改共享模型或执行中的状态。
2. 用合法站位的实际 XY/yaw 建候选图，按实际几何检查边后搜索。局部图没有路线时，通过检查过的短连接接入原房间 A*；连接候选范围为 1 m、每端最多 8 个。每个源站位查询预算不超过 120 s，且受来源 attempt/benchmark 预算限制。输出有限搜索耗尽或预算耗尽，不声称连续空间无路。
3. train 与 benchmark 都消费相同路线结构。`poses` 是明确的 `[x,y,yaw]`，`xy` 从同一路线投影。执行器直接跟踪规划朝向，删除按路径点序号重新生成 yaw 的逻辑；导航/到达阶段通过已有物理子步回调识别实际碰撞并终止当前导航。
4. RecoveryRegion 保留合法实测 S1 锚点，局部位姿 box 的角点和锚点到中心连接经过几何采样检查。保持恢复半径、yaw 容差、最近实测站位裁剪及合格 S1 集合；`goals/contains`、保存/重载和实际轨迹统计共用同一表示。
5. 拆分端点净空、初始化、恢复区域、搜索预算和实际跟踪失败原因；保存保守栅格是否屏蔽起点。难度和图片改称参考路线，不将有限候选图的结果称为连续空间最短路。数据格式、架构、运行说明、配置和测试说明已同步。

## 2. 复用原标定的构建与配对结果

全部写入新目录，未覆盖旧 p5/p6 run。以下均没有新增标定 trial。

| 输入 | 原结果 | 修复后 | 剩余负结果 |
|---|---|---|---|
| Cup r02，7 个 S0 | benchmark 0/7 | **2/7 episode** | 4 个端点净空不足 2 cm；1 个有限搜索耗尽 |
| Open r02，7 个 S0 | benchmark 1/7 | **7/7 episode** | 无；未运行七条策略评测 |
| Train 原 13 对 | 10 对 A* 无路，3 对执行超时 | **9/13 对找到几何路线** | 2 对端点净空不足；2 对有限搜索耗尽 |

产物入口：

- [Cup benchmark](../../../outputs/diagnostics/navigation-geometry-fix-cup-bench-20261026-r01/summary.json)：candidate → eligible → written = **7 → 2 → 2**。
- [Open benchmark](../../../outputs/diagnostics/navigation-geometry-fix-open-bench-20261026-r01/summary.json)：**7 → 7 → 7**。
- [Train 规划复核](../../../outputs/diagnostics/navigation-geometry-fix-train-plan-20261026-r01/summary.json)：13 对计划全部保留，仅做规划，`new_physical_steps=0`；具体路径在同目录 `planning.jsonl`。

Cup 四个被排除的 S0 实际净空：S0022 **1.38 mm**、S0030 **19.07 mm**、S0038 **16.18 mm**、S0041 **12.79 mm**。这些站位在原初始化碰撞阈值下合法，但不满足导航 20 mm 余量；上轮“没有接触”的静态检查不能替代此项。S0042 未找到满足当前余量的候选路线，保留负结果。

Train 的 S0042→S0047/S0048 因端点净空不足被排除；S0000→S0047/S0048 为有限搜索耗尽。没有将旧的三个超时 attempt 改写为成功。

## 3. 实际物理导航

使用原模型、原 `robocasa-v1` 配置和现有 `ContinuousContext/navigate/AttemptRecorder`，每次新建 Simulation、初始化一次，然后连续 stepping；没有重置、传送或 GPU 规划。仅验证导航，到达后结束，不执行抓取/开抽屉，`task_outcome=unknown`。

| 实例 | 参考路线 | 导航控制间隔 | 含到达阶段的总控制间隔 | 最终 XY 误差 | yaw 误差 rad | 结果 |
|---|---|---:|---:|---:|---:|---|
| Cup S0014 | 到 S0021 恢复区域，0.320 m | 36 | 46 | 0.748 mm | 0.000143 | 到达，进入恢复区域 |
| Cup S0040 | 到 S0044 恢复区域，0.515 m | 55 | 65 | 0.268 mm | 0.000458 | 到达，进入恢复区域 |
| Open S0048 | 到 S0043 恢复区域，0.945 m、17 个位姿点 | 114 | 124 | 1.422 mm | 0.000066 | 到达，进入恢复区域 |
| Train S0029→S0047 | 1.384 m、经中间位姿绕行 | 143 | 153 | 1.003 mm | 0.000415 | 到达 S1 |

正式验证 **4/4 导航成功**，共 388 个实际控制间隔，记录全部完整，`forbidden_contact_count=0`。控制间隔数不是底层 MuJoCo 物理子步数；本次不声称测量了动态全过程的 2 cm 净空。

- [Cup 导航主结果](../../../outputs/diagnostics/navigation-geometry-fix-cup-navigation-20261026-r02/summary.json)，两条轨迹在同目录 `rollouts/`。
- [Open/Train 导航主结果](../../../outputs/diagnostics/navigation-geometry-fix-extended-navigation-20261026-r01/summary.json)，两条轨迹在同目录 `rollouts/`。

另保留 `navigation-geometry-fix-cup-navigation-20261026-r01`：首次诊断脚本完成首条导航并保存 46 个控制间隔后，在离线统计时误用了 ExecutionConfig，缺少恢复诊断阈值而退出。该次不计入上述正式四条汇总；修正统计调用后使用新的 r02 完整验证，没有覆盖记录。

## 4. 软件验证

- 新增 8 项真实 MuJoCo 简化几何测试：圆柱栅格误拒绝、站位绕障、旋转中碰撞、正净空但低于 margin、房间 A* 接入、恢复区域重载、预算耗尽、跨 ±π 的短角插值。
- 新增执行器中间 yaw 跟踪测试；更新采集测试和已有 Panda smoke 路径输入，检查新 `poses` 协议。
- 全套离线回归 **367 项：365 通过，2 个显式渲染检查跳过**。日志：[navigation-geometry-regression-r03.log](../../../outputs/logs/navigation-geometry-regression-r03.log)。
- 最后对相关领域与 benchmark 构建/绘图再检查 **61 项全部通过**。日志：[navigation-geometry-local-final-r02.log](../../../outputs/logs/navigation-geometry-local-final-r02.log)。

首轮全套回归因默认动态库路径无法导入 EGL，导致 9 个多进程检查失败；临时 EGL/OpenGL 库仅解压在 `/tmp/lmdf-egl-libs`，补充 `LD_LIBRARY_PATH` 后同套检查通过，未修改系统或替换测试。当前 NVIDIA 驱动不可访问，本轮不报告 GPU、权重推理或策略任务验证。

相关测试可直接执行：

```bash
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest tests.planning.test_geometry_navigation \
  tests.data.test_recovery tests.runtime.test_torso tests.workflows.test_collection \
  tests.workflows.test_mode_downstream tests.workflows.test_benchmark_progress -v

# 全套包含会自行选择 EGL 的 spawn 测试，需显式提供 EGL 库目录。
LD_LIBRARY_PATH=/tmp/lmdf-egl-libs/root/usr/lib/x86_64-linux-gnu \
  MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v
```

服务器已有库目录 `/data1/wenyifan/opt/lib` 也可用于 EGL 加载，说明见 `tests/README.md`。

## 5. 结果边界

- 完成导航代码修复、已有标定复用构建、Train 计划复核及四条实际导航。未新增成功的 S0→S1→操作示教，原 12 条训练数据仍是 S1 直接操作；没有更新 train_index。
- Open 的 7 个 episode 是新构建结果；只验证其中一条导航，没有重新运行 π0.5 或 MolmoBot 评测。
- 当前路线与局部恢复区域仍是有限采样近似，非连续碰撞自由证明。端点净空不足、有限搜索耗尽和实际碰撞均保留为独立结果。
- 原 p5/p6 数字继续描述原版本；本报告的新结果不回写旧目录或原标定结论。
