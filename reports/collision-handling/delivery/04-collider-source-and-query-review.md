- 状态：done
- 日期：2026-11-04
- 替代：无
- 关联：01-robot-representation.md、02-structured-environment.md、03-cup30-near-station-failures.md

# 当前碰撞体来源、冗余与查询性能

## 范围与执行

检查对象为 `.worktrees/collision-handling` 当前源码、其安装的 cuRobo、外部 RBY-1 资产及已有交付报告。保留已有未跟踪的 03 报告；未修改生产代码、球表、配置或运行结果。只运行 CPU 球表计数、同 Link 重复/单球包含检查，未运行新 GPU 性能实验或 rollout。

## 来源与表示

| 对象 | 当前规划表示 | 来源及本项目加工 |
|---|---|---|
| PandaOmron | 141 个实体球，另 40 个携带物槽位；buffer 2 mm | 初始运动链和臂/底盘球来自本地 MolmoSpaces panda_omron 描述；物理几何及坐标来自 robosuite 1.5.2 Panda、Omron、gripper MJCF。本项目补手指/夹爪，并重建底盘、柱体、link0 球 |
| RBY-1 | 球表 86 球，配置启用 78 球；另双手各 40 携带物槽位，共 158 槽；buffer 5 mm | 读取 MolmoSpaces 外部资产包的 rby1m_holobase.yml 与 rby1m_holobase_spheres.yml；本项目调整运动学、锁关节和路径，不重拟合球 |
| 环境、目标 | 默认 merged_hull：每 geom 闭合凸包/三角化 primitive，合并成网格 | 原始几何来自已加载 MuJoCo 场景的碰撞 geom；本项目 collision_world.py 选择、转坐标及导出，使用 trimesh 构造/求凸包 |
| 可选环境 | box_cuboids：近处原生 Box → 有向 Cuboid，其余仍合并网格 | 本项目拆分逻辑；最近 1.5 m 包围半径口径内最多 128 个 Box，超量回网格 |
| 携带目标 | 最多使用预留 40 槽的球拟合 | 调用 cuRobo 原生 fit_spheres_to_mesh，VOXEL 方法；实际返回球数可能少于 40 |

Panda 的 141 球：底盘 51、固定柱 19、升降柱 15、link0 实体底座 35、臂 link1–7 共 12、夹爪 3、手指 6。底盘和 link0 用本项目离线格块覆盖/集合覆盖 ILP；柱体用本项目环形扇区覆盖加集合覆盖选择。不是直接采用 cuRobo 官方球表或官方 MORPHIT 生成的最终配置。

Panda 来源说明在 `src/lastmile_dataflow/planning/robots/assets/panda_omron/NOTICE`。RBY 外部资产位于 `/data1/wenyifan/lmdf/molmospaces_data/assets/robots/rby1m/curobo_config/`，真实版本路径落在 `mujoco/robots/rby1m/20251224/`。可确认其直接来源是 MolmoSpaces 资产，不能仅因目录叫 curobo_config 就称为 NVIDIA 官方球表，也未追溯证明每个球由机器人厂商生成。MolmoSpaces 官方仓库为 https://github.com/allenai/molmospaces 。

RBY 的 ee_left / ee_right 各 4 球没有列入 collision_link_names；头部、轮子、若干关节 Link 也未单独列入。是否由相邻球充分覆盖未在本次验证，因此不能把锁定关节等同于完整实体覆盖。

## 冗余和几何误差

- 两份 YAML 同 Link 内完全重复球为 0，被单个其他球完全包含的球为 0。未检查多球联合覆盖后的可删性，不能据此声称全局最少球。
- Panda 底盘 51 球已去掉旧候选的 4 个重复球；目前主要问题是保守外扩，不是简单重复。含 buffer 侧向最大约 3.6 cm、顶部约 6.2 cm。柱体侧向约 2.1 cm，link0 外扩约 3.2 cm。
- Cup_30 S0022/S0030/S0038 的最新诊断确认：实体间隙分别 1.38/19.07/16.18 mm，底盘球仍穿入桌子，三站均 0/3。这是起点表示误拒，未修复；不意味着消除误拒后必然有轨迹。
- 环境排除机器人、非碰撞 geom、地面/plane、范围外 geom；目标独立查询。环境范围由 `2 * workspace_radius + 1 m` 决定，不固定为 5 m。
- 环境没有按机器人高度/精细可达域进一步裁剪，也没有跨 geom 做包含消除或布尔并集。已有 5 m 样本导出 303–369 geom、64,410–81,538 三角面；其中距机器人小于 1 m 的只有 26–29 geom，远处小件仍有建模/BVH 成本。不能直接称这些几何都可删除。
- 合并多个闭合凸包不等于求几何并集；重叠壳的内外符号仍是已记录风险，不能称为精确统一 SDF。
- 环境、目标、把手按不同接触规则分开查询，目标与把手集合互斥，不是把同一目标无条件重复检查。额外 checker 仍增加调用与球张量 clone 成本。

## 查询链

1. 操作规划：本项目 V2Planner → cuRobo MotionPlanner 的 IK / TrajOpt / metrics → RobotCostManager + 本项目 ContactManager → SceneCollisionCost → 原生 GPU SceneCollision。FK 将各 Link 球变到查询坐标系。
2. 默认环境和目标为 sphere–mesh，安装源码使用 Warp mesh_query_point 和 BVH；可选 Box 为 sphere–OBB 解析查询。机器人自碰撞为 sphere–sphere，使用 self_collision_ignore 排除指定 Link 对。本项目没有另写 GJK/SAT/BVH 内核，也没有在主链使用通用 mesh–mesh 或环境 ESDF。
3. TrajOpt 的原生 lbfgs_bspline 配置启用 swept 查询，内核 SWEEP_STEPS=3；metrics 配置 use_sweep=False，使用离散 binary 查询。不能把整个规划验证描述为严格连续 CCD。优化设置 activation distance=1 cm，这不是球表的物理膨胀 buffer，也不是所有查询共用的硬间隙。
4. 目标接触允许时，只在目标 checker 中屏蔽指定手指球；Panda 把手可另放行掌部。环境和自碰撞仍使用原球。携带物 attach 后关闭目标独立代价，转为附着球检查。
5. 导航是另一条 CPU 路径：MuJoCo 原始碰撞 geom，包围半径粗筛后 mj_geomDistance，并读 contacts；边按约 1 cm / 1° 离散采样，缓存 pose/edge 查询。它不是 cuRobo 全身球查询。
6. 执行时读取 MuJoCo 物理接触；contacts.py 的操作接触策略为 runtime-record-only-v1，导航有独立严重穿透判据。不要把所有操作接触记录解读为碰撞即自动急停。

## 已有性能证据

下面均为已有报告记录，不是本次新测，也不是单个碰撞核延迟：

| 对照 | 结果 | 能说明什么 |
|---|---|---|
| Panda 仅替换 link0 底座球，同求解次数 | 约 1.2 倍耗时 | 增加球数确有规划开销；不能把全部新球总耗时都归因于碰撞核 |
| 七站冻结规划：merged_hull / 局部 Box、128 槽 | 170.9 / 173.0 s，成功 lane 8/21 / 12/21 | 该样本总体耗时接近，Box 改变可行候选 |
| Box 2048 槽改 128 槽 | 226.5 → 173.0 s（局部 Box） | 原生 Box 核线程数受球数×预留槽数影响，空槽也有启动成本 |
| S0012 相同 10 次求解 | 9.7 → 29.6 s | Box 仍可能慢约 3 倍，原因未明确 |
| 固定 42 trial | 两表示都 12/42 任务成功 | 没有稳定总体优胜；长 Open 样本预算占用升到 98% |

当前 use_cuda_graph=False；保留 planner/solver 分配与局部几何缓存，支持相同冻结站位复用，以及重新绑定场景资源。场景更新仍需坐标变换、网格上传/BVH 更新。多次优化迭代、候选和重试会放大碰撞查询总量，秒级整次规划不能等同于秒级单次查询。

缺少两机器人在相同 B/H/N、相同场景与硬件上的 GPU 同步计时微基准，不能给出可信的微秒/毫秒单次延迟、QPS 或 Panda 对 RBY 的速度比。RBY 当前球更少不等于一定更快，其自由度、双臂几何和求解难度不同。

## 结果

碰撞几何是“外部物理资产 + 项目自定义近似与导出 + cuRobo/MuJoCo 原生查询”的组合。当前优先关注已证实的 Panda 底盘外扩误拒、RBY 未启用部位覆盖边界；性能已有整次规划证据，单核查询性能尚未隔离测量。此次仅增加本总结报告。
