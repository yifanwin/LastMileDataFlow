# 导航软净空实施结果

- 状态：done
- 日期：2026-10-26
- 替代：接续 07-navigation-geometry-fix.md；新运行使用软目标协议，历史硬门槛结果保留
- 关联：[需求](../requirement/02-navigation-soft-clearance.md)、[修改方案](../plan/04-navigation-soft-clearance.md)、[上一轮交付](07-navigation-geometry-fix.md)

## 1. 实际改动

完成 `soft_clearance_v1`：**20 mm 不再决定通行资格，只影响路线代价；实际到达、无严重碰撞及记录完整分别检查。**

- `planning/navigation.py` 使用 `NavigationSettings` 集中传递净空目标、软权重、穿透阈值及转向折算比例。`GeometryCheck` 分开保存可行性、净空和边代价，不再返回 `endpoint_clearance_below_margin`。
- 保留 ≤1 cm / ≤1° 的实际几何采样，净空不足或浅接触可以通过。基准参数为目标 **0.02 m**、权重 **2.0**、导航严重穿透阈值 **0.005 m**；轮/底盘的正常地面接触单独处理。
- 站位连接和按需生成的房间 XY/yaw 状态使用同一 A*、同一检查和代价。房间状态沿用 XY 分辨率，旋转间隔 15°；旧圆包络栅格只提供坐标范围与显示，不作为硬通行门槛。旧的 `_room_route/_connectors` 后备流程已删除。
- 为避免统一搜索在优化时丢失原有路线，同一个 A* 先在有限站位连接上提供候选上界，再纳入房间状态继续择优。两次搜索共享几何/代价缓存和同一个时间预算；预算耗尽时保留已找到的完整候选，并标记 `budget_limited`。
- `runtime/outcomes/contacts.py` 的 `NavigationContacts` 共用于准备态、cuRobo 物理子步和 VLA 显式导航段，统一测量最大穿透和严重碰撞；导航外的操作接触语义保留。执行器继续跟踪原 `poses`，不重建 yaw。
- 恢复区域、配对和 benchmark 使用新可行性规则，合法 S1 不因不足 20 mm 被删除。保留 profile、原标定/诊断来源、合格 S1 集合和恢复区域裁剪。
- 两个新增参数 `navigation_clearance_weight`、`navigation_max_penetration_m` 随运行保存；`navigation_margin_m` 保留名称但改为软目标。标定来源配置与当前导航参数分开记录，调整三项导航参数不强制重新标定。

没有增加源码目录、CLI、平行执行器或新的记录链。使用现有文件中的小型 dataclass 和共享搜索/碰撞函数，不引入通用校验或配置迁移框架。新增运行目录及本次涉及的源码、测试、导航文档目录均未超过 20 个直接子目录；已有原始数据目录未清理或搬迁。

## 2. 软代价

```text
p(d) = clip(1 - max(d, 0) / target, 0, 1)^2
Δℓ = 平移长度增量 + (导航平移速度 / 导航角速度) × |yaw 增量|
代价 = Σ Δℓ × (1 + weight × 相邻采样平均 p)
```

20 mm 以上惩罚为零，越窄惩罚越大但保持有限。目标或权重为零时关闭净空惩罚。原地旋转同样有代价，避免只优化 XY。`length_m` 仍是实际平移长度，另保存 `route_cost` 与 `clearance_penalty_cost`。

`optimal_in_graph` 表示在本次有限候选图内完成择优，不是连续配置空间全局最优；预算内未找到完整路线仍报告 `navigation_search_budget_exhausted`。

## 3. 原输入复核

复用原始标定和诊断，没有新增标定 trial，没有重分类或覆盖旧产物。

| 输入 | 上一轮 20 mm 硬门槛版本 | 本轮软目标版本 |
|---|---:|---:|
| Cup，7 个候选 S0 | 2 个 episode | **7/7 个 episode** |
| Open，7 个候选 S0 | 7 个 episode | **7/7 个 episode** |
| Train，原 13 对 | 9 对可规划 | **11/13 对可规划** |

主结果：

- [Cup 新 benchmark](../../../outputs/diagnostics/navigation-soft-cup-bench-20261026-r02/summary.json)：`7 → 7 → 7`，无排除，构建约 102 s。
- [Open 新 benchmark](../../../outputs/diagnostics/navigation-soft-open-bench-20261026-r01/summary.json)：`7 → 7 → 7`，无排除，构建约 21 s。
- [Train 规划复核](../../../outputs/diagnostics/navigation-soft-train-plan-20261026-r02/summary.json)：11 对有完整路线；S0000→S0047/S0048 两对在每对 120 s 的预算内未找到完整候选，保留为预算耗尽，不冒充真实空间无路或任务失败。

Cup 原来因净空不足被排除的 S0022、S0030、S0038、S0041 均重新进入搜索并得到路线；S0042 得到约 0.966 m、17 个位姿点的绕行路线。原 Train S0042→S0047/S0048 两对也得到路线。

首轮 Train 结果保留在 `navigation-soft-train-plan-20261026-r01`：10/13 对有路线，其中 S0029→S0047 在统一房间搜索中耗尽预算，退化于上一轮已知路线。加入共用搜索的站位候选上界后，新 r02 恢复该配对，得到最终 11/13；没有改写首轮负结果。此问题增加了专门回归测试。

## 4. 实际物理导航（CPU MuJoCo）

使用真实场景快照与 `robocasa-v1`，每条路线新建 Simulation 和 attempt，初始化后仅通过现有控制器连续 stepping。仅验证导航，到达后结束；未运行抓取、开抽屉或权重策略评测，`task_outcome=unknown`。

| 来源 | 角色 | 导航控制间隔 | 含到达的总控制间隔 | 最终 XY 误差 | 最大非地面穿透 | 严重碰撞 |
|---|---|---:|---:|---:|---:|---|
| Cup S0022 | 原始净空约 1.38 mm 的起点 | 24 | 34 | 1.535 mm | 0 | false |
| Cup S0041 | 原始净空约 12.79 mm 的起点 | 99 | 109 | 0.594 mm | 0 | false |
| Cup S0042 | 多段绕行 | 140 | 150 | 0.854 mm | 0 | false |
| Open S0048 | 软代价选出的约 0.552 m 路线 | 72 | 82 | 1.001 mm | 0 | false |
| Train S0042→S0048 | 原来因端点余量不足被排除 | 94 | 104 | 0.647 mm | 0 | false |

**5/5 实际导航到达，479 个实际控制间隔，记录全部完整，无严重碰撞。** Cup 三条和 Open 一条均被离线轨迹统计判为进入保存的恢复区域；Train 检查精确 S1 到达。

- [Cup 三条导航结果](../../../outputs/diagnostics/navigation-soft-cup-navigation-20261026-r01/summary.json)。
- [Open/Train 两条导航结果](../../../outputs/diagnostics/navigation-soft-extended-navigation-20261026-r01/summary.json)。

物理验证引用首轮软目标规划；最终 Cup r02 的路线与首轮七条完全相同，Train 的被测 p000010 路线在两轮间也相同。运行数据中保留实际来源引用，未重写为新目录。控制间隔数不是底层物理子步数，未记录图像/视频，也不声称实际运动全程保持 20 mm 净空。

## 5. 软件验证

- 局部测试检查了宽窄双路线的软代价择优、只有窄路时仍可通行、19/21 mm 无有效性跳变、采样密度与转向代价、浅接触/严重穿透、优化预算内保留完整候选、恢复区域重载、显式 yaw 和标定复用。
- 实测暴露的 Train 预算退化增加了多段站位候选在房间优化预算耗尽时仍被保留的回归。
- 最终全套 **374 项：372 通过、2 个显式渲染检查跳过**。日志：[navigation-soft-regression-r02.log](../../../outputs/logs/navigation-soft-regression-r02.log)。几何导航局部 14 项全部通过，日志：[navigation-soft-geometry-final.log](../../../outputs/logs/navigation-soft-geometry-final.log)。

复现软件检查：

```bash
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest tests.planning.test_geometry_navigation \
  tests.runtime.test_contacts tests.runtime.test_torso \
  tests.workflows.test_collection tests.workflows.test_mode_downstream -v

LD_LIBRARY_PATH=/data1/wenyifan/opt/lib MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v
```

本轮 `nvidia-smi` 无法连接驱动，全部新增验证为 CPU 几何/物理或离线软件检查，不报告 CUDA、模型推理或策略任务成功。

## 6. 结论和边界

- 已完成软净空实现、配置/格式/运行说明同步、Cup/Open 构建、Train 计划复核及五条独立物理导航；原标定、旧 run 和原 12 条 S1 训练数据保持不变。
- 5 mm 是本轮采用的独立穿透判据，不是通用碰撞危险程度的定标。简化 MuJoCo 测试验证了约 2 mm 浅穿透允许、较深穿透拒绝；五条真实场景导航没有发生非地面穿透，因此不能据此声称真实机器人擦碰容忍度已经定标。
- 参考路线和恢复区域仍是有限采样近似；两条剩余 Train 配对只有预算耗尽结果，没有连续空间不可达证据。
- 没有新增完整 S0→S1→操作示教，未更新 train_index，未运行新策略评测。导航到达、操作成功和记录完整继续分别报告。
