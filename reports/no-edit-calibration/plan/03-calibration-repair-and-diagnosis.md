# PandaOmron 标定：修复与排查实施方案

- 状态：draft（方案已整理，尚未实施）
- 日期：2026-10-27
- 替代：无；补充既有标定方案中的故障修复流程
- 关联：[问题梳理](../../panda-omron/analyze/2026-10-27-calibration-problem-review.md)、[复用缺陷](../analyze/03-planner-reuse-target-binding.md)、[Cup_30 顺序对照](../analyze/02-cup30-reuse-order-experiment.md)、[全量统计](../analyze/02-all-runs-trial-statistics.md)、[原实验方案](01-experiment-plan.md)

## 1. 目标、范围和执行顺序

工作目录固定为 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/no-edit-calibration-val0-99`。

本轮解决两条主线：修复已确认的跨任务 planner 绑定缺陷；查明并修正导致 goalset 异常拒绝的碰撞表示或坐标转换问题。随后仅针对仍存在的不可达和执行跟踪异常继续排查。

**采样参数调整不属于本方案。** 所有对照沿用已有站位、初始化姿态、抓取候选、seed 和规划预算，不修改采样器或配方加载限制。机器人物理资产、任务成功判据、碰撞检查与升降/底盘自由度也不预先放宽。

执行顺序：

```text
P0 固定输入与诊断样本
 → P1 修复任务绑定、checker 生命周期、携带物缓存
 → P2 同姿态几何/碰撞对照，定位 goalset 失败原因
 → P3 按证据修正规划表示，做单因素对照与真实执行
 → P4 定位残余可达性/跟踪问题，按实际报错修复
 → P5 小批量混合任务复核，交付修复和剩余边界
```

P1 完成前不将新成功率归因于碰撞球修正；P2 没有证实假碰撞前不直接整体缩球。各阶段有问题就停在该阶段定位，不继续消耗全量标定。

## 2. P0：固定比较对象和运行条件

### 2.1 输入及环境

- 复用 `outputs/_calibration_inputs/exp-cup30-only-20261027-1542/` 和 `exp-cup30-last2-20261027-1600/` 的冻结输入，以 `--dataset` 路径进入既有标定链，避免重新准备造成站位或物体静置差异。
- 同一物理站位以任务身份、xy/yaw 和初始化状态配对；保留原来的每站 3 trial。核对两个 Cup_30 输入是否相同，若不相同，统一引用一份原输入后再对照。
- 历史 raw trial 只读；新实验使用独立 run-id。保留必要的有效配置、seed、输入引用、软件版本及真实结果。
- 本轮检查时 worktree 尚无 `.venv`。实施时先通过本 worktree 的安装入口建立独立环境并确认 cuRobo 0.8.0、MuJoCo 与冻结 MJB 兼容；不借主工作区或其他 worktree 的 Python/.pth。若恢复有版本错误，按错误定位，不通过换 XML 假装恢复成功。
- 单 GPU worker、串行任务完成因果对照，GPU 按实时负载选择并显式指定；小批次通过后才恢复并行。原运行耗时只作背景，不用不同负载下的墙钟差判断性能回归。
- 本轮只写方案，不安装环境、不启动标定、不改变现有进程。

### 2.2 固定的小诊断集

先从已有原始结果选出 **12 个站位**，选定后固定，不按修复后的结果替换：

| 站位组 | 数量 | 覆盖目的 |
|---|---:|---|
| goalset 底座 scene collision 主导 | 6（Pick/Open 各 3） | 柜前、桌边/台面附近、较开阔但仍被拒绝；兼顾 IK 和 TrajOpt 失败 |
| 原有成功 | 2（Pick/Open 各 1） | 检查修复是否损害已有能力 |
| 低目标/低把手不可达 | 2（Pick/Open 各 1） | 区分运动学限制和碰撞阻挡 |
| 已规划但 pregrasp 跟踪超时 | 2（Pick/Open 各 1） | 定位参考转换、物理阻挡、控制响应 |

尽量覆盖至少 4 个 house。样本只需在诊断运行的主 JSON 中列原 trial/query/状态引用，不复制历史 trial 或创建第二套数据索引。若某分组不存在，报告实际数量，不补造样本。

## 3. P1：完整修复 planner 复用状态

### 3.1 修改点一：当前任务的 body 集合必须重新绑定

文件：`src/lastmile_dataflow/planning/curobo_v2.py`。

- 将初始化和 rebind 共用的目标/把手身份计算合为一处小函数：从**当前 sim** 的 `target_id`、`handle_id` 计算 descendants。
- `rebind_resources()` 先解除旧 attachment，再切换 sim/机器人参数和当前任务绑定，最后调用 `refresh_world()`。
- 每次加载的 environment、target-only、handle-only 都属于当前状态；target-only 仍保持 `target_bodies - handle_bodies` 语义。
- 保留现有 solver、warmup、随机种子刷新及 measured state 流程。不把 scene/target 全部加入 key 来绕过重绑定。

### 3.2 修改点二：Pick/Open 的 checker 结构不能串用

本轮源码复核发现：`handle_checker` 只在 planner 首次创建且有 handle 时存在；`curobo_v2_costs.py` 的 `handle_cost` 又在 cost manager 初始化时持有该 checker。仅重算 `handle_bodies`，或仅替换 `context['handle_checker']`，都不能保证已有 manager 真正使用新 checker。

**采用最小修复：在 `PlannerResources` key 中增加“是否具有 handle”的布尔维度。** 它是 cost manager 的结构差异，区别于场景内容：

- Pick/无 handle 与 Open/有 handle 分属兼容家族，首次跨类型时新建对应 planner。
- 同一兼容家族内仍跨任务复用；保留 checker 对象身份，通过 `load_collision_model()` 更新内容，确保已有 cost manager 引用仍有效。
- Open A → Open B 必须更新 handle world；`handle_body == target_body` 时 target world 合法为空，handle world 正确非空。
- 缓存容量仍为 2。混合任务可能增加淘汰和初始化，后续记录实际创建/复用次数；不在修复阶段提前扩容或优化调度。

这样无需现在引入空 handle checker 的常驻代价项，也无需遍历所有 rollout 重建 cost manager。只有实测表明家族切换造成不可接受的开销，才另行考虑统一 checker 结构。

### 3.3 修改点三：携带物拟合缓存按目标失效

`attach_target_geometry()` 当前仅检查 `_fit_model is sim.model`。改为同时匹配 **model 对象身份 + target_id**；同 model 换目标也必须重拟合，同一刚体目标跨站位仍可复用 target-local fit。

将失效与任务绑定放在同一流程，避免 rebind 和 attach 各自维持不同规则。若实际发现目标子树发生内部关节形变，再针对该实际输入处理；不预先设计通用几何缓存框架。

Pick attach 时若 target mesh 为空，给出包含当前目标的明确错误，沿既有 `episode_error` 路径记录，`task_outcome` 保持 unknown；不捕获后改写成规划无解或任务失败。此检查针对已有 1,842 次空列表异常，不能替代绑定修复。Open 的合法空 target world 不触发该错误。

### 3.4 有价值的测试

在已有 `tests/planning/test_planner_reuse.py` 中扩展必要用例，分别覆盖：

1. 不同 model 中复用相同数值 body id：最终导出的目标必须是当前目标，而非首次任务的同号 body。
2. 同 model 换目标：目标世界和 carried fit 均更新；同一目标重复请求仍允许 fit 复用。
3. 无 handle/有 handle 的家族切换，以及 Open A → B 的 handle 内容更新；验证实际 cost/checker 引用，不只验证函数调用次数。
4. Open 的 target=handle 合法空目标世界，以及 Pick attach 空目标的明确错误分类。

软件测试使用小模型和必要 mock；GPU 小检查验证原生 checker 在非空/空内容刷新后没有旧几何残留。不要只测 `set` 相等而忽略 solver 使用的对象。

### 3.5 原对照复跑和通过条件

- 修复后 Cup_30 单独运行：20 站 × 3 trial = 60。
- 修复后原顺序 soapdispenser → vase → peppershaker → Cup_30：使用原输入，预计仍为 132 trial，以输入实际数量为准。
- 另用少量 Pick/Open query 序列验证混合任务家族切换，覆盖 preclose 和闭爪后 pose 两类 planner，不额外铺开整批执行。

通过条件：

- 每个受检 query 的 target/handle geom 集合与当前 model 子树一致；按 geom 身份比对，不用名称前缀猜测。
- Cup_30 队列末尾不再出现空目标 attach 的 38 次结构性崩溃；同站位的实际成功能力恢复，所有相对同期单独运行的退化均有阶段证据可解释。
- 不要求数字机械地等于历史 37/60，但不能仅凭“出现 1 次成功”宣称恢复；先比较同期两组逐站结果，明显差距时只复核有差异的站位。
- 若绑定正确后仍有 lift 无解，保留真实结果并移交几何排查，不扩大宣称“全部 lift 失败已修复”。

## 4. P2：把底座主导代价拆成可证实的碰撞原因

### 4.1 核对三层表示

对**运行快照内实际编译的模型**核对，而不只看外部机器人 XML：

1. 上游 RoboSuite/RoboCasa 本体定义 → 导出 MJCF：尺寸、单位、mesh scale、关节轴、枢轴、torso 安装高度、碰撞 geom 与视觉 geom。
2. 编译 MJCF → 规划 URDF：base_site、mobilebase0_base、固定柱、升降柱、TCP 的变换。
3. URDF → cuRobo 世界球：球所属 link、中心、半径、buffer，以及当前底盘/yaw/torso 状态。

已知重点：底盘物理 box 0.70×0.50×0.38 m，对应 12 球包络约 0.88×0.68×0.60 m；固定柱直径 0.24 m，8 球横向包络约 0.48 m。`omron_base` 统计包含底盘和柱体，不能继续统称为地面底盘碰撞。

变换检查只取几组有意义状态：实际失败站位、yaw=0/π/2、一个非零底盘位移，以及 profile 使用的 torso 高度和一个合法非零高度。逐 link 比较位置/朝向；沿用现有 FK 容差作为基本检查，毫米级偏差也记录，不把 TCP 通过等同于球对齐通过。

上游源码若仍缺失，先完成实际资产与规划表示核对，再获取匹配版本的上游文件做只读比较；不把最新上游模型直接替换运行资产。

### 4.2 同一状态下比较距离，而非比较 cost 排名

对 12 站诊断集检查：初始化状态、goalset 的代表性 IK 末态、TrajOpt 最接近成功的轨迹或失败轨迹中碰撞最重的状态。

- 同一 measured state、同一抓取目标、同一局部参考系下计算 MuJoCo geom 距离/接触，以及 cuRobo 对应球的距离与约束可行性。
- 输出具体障碍 geom、机器人部件、球 index、球心/半径、距离和接触位置。sphere 和障碍必须转换到同一个世界坐标系。
- 同时保存原始几何距离和规划有效半径（含 buffer）距离，区分实体相交、2 mm 膨胀和 1 cm 优化代价激活。
- 考虑物理 contype/conaffinity、允许地面接触、目标允许接触等规则差异；检查 scene mesh 的位姿、尺度、局部截取和 SDF 查询。MuJoCo mesh 的碰撞近似与规划三角网格不一定一致，不把任一侧单独当作绝对真值。
- 若历史 trace 没有失败候选关节向量，用原输入做独立诊断规划，定向保存该阶段少量代表 q/轨迹；不从 cost 数字反推姿态，也不全量转储所有 seed。
- 静态诊断使用新建、未 begin 的 sim/scratch data；已经开始的执行 sim 不恢复或改写 qpos。

诊断结果归为：真实几何冲突、球形近似误拒绝、坐标/场景导出错误、SDF/checker 错误、当前证据不足。每条给出证据；不以“最大 scene cost”直接定类。

### 4.3 最小可视化

为底盘与柱体各选择一个有代表性的失败状态，生成物理几何、规划球、接触家具的侧视/俯视叠加图；必要时加一个正确通过状态。图放诊断输出目录，报告引用，不批量渲染全库视频。

## 5. P3：按证据修正规划几何和膨胀

### 5.1 修改规则

| P2 结果 | 对应修改 |
|---|---|
| 原点/轴/尺度错误 | 修正 `planning/panda_omron.py`、URDF 或 `collision_world_v2.py` 中被证实错误的变换，随后重做同状态距离检查 |
| 球近似误拒绝 | 优先重拟合 `spheres.yml` 的底盘、固定柱及必要的升降柱；底盘 box 分区覆盖、柱体按实际圆柱覆盖，保留真实体积 |
| buffer 是主要误拒绝来源 | 仅在修正形状后，对 2 mm 与 0 mm 做规划诊断；是否修改默认值由物理净空证据决定 |
| SDF/mesh 查询异常 | 修复被确认的导出/查询问题；保留已有 target query range 语义，检查环境与 target checker 的实际结果 |
| 实体确实碰撞 | 保留该无解；若初始化却被标 valid，再查状态时间点、接触过滤与静置差异 |

不能用统一比例缩球使 planner 通过。拟合要检查 box 的面/棱/角、柱体侧面/端面及各球之间的空隙；优先采用可说明覆盖范围的几何构造，再用实际表面检查辅助验证。报告残余外扩、覆盖误差和球数量，不只给总包络。

先用最少的必要球改善已确认的误拒绝，再看规划开销；不预先追求高分辨率全机器人拟合。只有实际显示手臂球导致同类问题时，才扩展范围。修改球数量后重建 GPU worker/solver，避免旧 tensor 布局或 sphere index 留存。

### 5.2 单因素实验

| 组 | 任务绑定 | 球表示 | buffer / activation | 用途 |
|---|---|---|---|---|
| A | 修复 | 现有 | 2 mm / 10 mm | 基准；P1 结果可复用 |
| B | 修复 | 几何修正 | 2 mm / 10 mm | 隔离几何收益 |
| C（有必要才跑） | 修复 | 几何修正 | 0 mm / 10 mm | 仅诊断 buffer 是否仍主导 |
| D（有必要才跑） | 修复 | 同 B | 保持默认 | 离线去掉已定位的部件-场景约束，验证该项是否阻止 IK/轨迹 |

C/D 的结果不作为生产默认或真实成功；尤其 D 不执行物理轨迹。activation 只在有证据指向优化软代价时单独消融，不和球/buffer 同时调整。

A/B 先对固定 12 站做规划，再以各 3 trial 做必要的真实执行（每组最多 36 trial），复用已经完成且参数一致的结果。比较 goalset 的 IK 收敛/可行、TrajOpt 成功、实际执行、任务成功、接触及规划耗时。

通过条件：已定位的假碰撞在同状态下消除；相关无解在相同预算下得到改善或剩余原因明确；原成功站位没有未解释的退化；新通过轨迹经真实执行验证，没有因几何漏覆盖产生新的禁止接触。若只有规划通过而执行失败，单列结果，不算完成修复。

## 6. P4：残余不可达和跟踪异常

### 6.1 可达性

在修复绑定和几何后的模型上，选诊断集中的低目标/低把手，保持目标位置和姿态要求，比较正常碰撞 IK 与临时关闭场景碰撞的离线 IK。

- 正常失败、无场景碰撞成功：继续定位阻挡部件/姿态，不下“高度绝对不可达”的结论。
- 两者均失败：检查肩部至 TCP 可达范围、关节限位、目标姿态、锁定 torso 和局部底盘范围。有限 seed 无解仍只说明当前配置/预算未解，不当作数学上的不可达证明。
- 若指向目标姿态生成或坐标错误，修复 `runtime/execution/manipulation.py` 或任务目标生成中的具体错误；只在发现问题后修改。
- 不按历史零成功高度直接删除目标，也不以开放 torso、增大底盘范围或宽松姿态容差代替缺陷修复。这类能力调整另立实验。

### 6.2 跟踪

文件入口：`runtime/execution/robocasa_tracking.py`、`runtime/control/panda_robocasa.py`。

优先复核历史超时在 P1/P3 后是否仍发生；消失则记录原因，不再扩大控制改动。若仍发生，用一条失败和一条成功的同类轨迹对照：

- 世界/局部底盘参考和实测 xy/yaw，尤其相对 `initial_yaw` 的转换。
- 规划关节参考 → scratch FK → TCP 参考 → OSC 指令的连续性和单位。
- 每参考点停留 tick 数、TCP 位置/姿态误差、base 到达误差、实际接触、控制饱和和关节/升降限位。
- 闭爪后的底盘保持与 fixed planner 状态，避免把旧的底盘保持问题重新引入。

实体被挡则回到 P2/P3；参考转换错误就修转换；在没有实体阻挡且参考正确时，才调整具体控制增益、限速或轨迹跟踪方式。先保持现有 120 tick/参考点和到达容差，不通过延长 timeout 掩盖卡住。

控制修复先跑对应 `test_robocasa_tracking`/`test_robot_interface`，再跑失败/成功对照的真实执行；只增加与实际错误对应的回归。

## 7. P5：小批次验收与后续标定入口

1. 用固定输入挑选 8–12 个任务，Pick/Open 都覆盖，至少 4 个 house；包含原成功任务、goalset 无解任务、低目标和顺序复用任务。
2. 每任务最多沿用原 20 站、每站 3 trial；总量约 480–720 trial。先检查前几个任务的绑定/几何和错误分类，再完成该批。
3. 恢复计划中的多 worker 设置；用一个跨任务 query 小序列确认各 worker 的 preclose/pose 均正确，不重复全套串行实验。
4. 本轮固定评测任务集，不使用旧失败统计触发动态类别排除。历史批次有目标错绑，旧的“零成功类别”结论可能被污染；后续全量恢复前应重新评估这些排除项，不直接继承为不可行结论。
5. 不在本方案执行过程中自动恢复千场景全量任务。交付可用修复、对照数据和剩余问题后，下一轮全量实验另行组织。

### 7.1 统计口径

使用同一张阶段统计表，同时给数量和分母：计划 trial、实际完成 trial、规划无解、执行失败、真实成功、episode/shared error、unknown timeout。

- 端到端产出：真实成功数 / 全部计划 trial，另报覆盖率；未完成和基础设施错误保持独立类别。
- 规划通过率：通过规划并可交付执行的 trial / 完成该阶段有效规划的 trial。
- 已执行成功率：任务成功 / 得到确定任务结果的已执行 trial；未知/基础设施错误单列。
- 同任务同站位逐项对比，另按 Pick/Open、goalset/pregrasp/lift/open 分层；不混用历史运行中快照与最终统计。
- 计时同时看 solver 创建/复用次数、规划用时、真实执行用时，不把 GPU 竞争或采样差异当算法变化。

### 7.2 软件验证顺序

在 worktree 独立环境中执行相关测试，例如：

```bash
.venv/bin/python -m unittest tests.planning.test_planner_reuse tests.planning.test_panda_omron -v
# 修改跟踪链路时才执行该专项
.venv/bin/python -m unittest tests.runtime.test_robocasa_tracking tests.runtime.test_robot_interface -v
# 局部通过、实现收敛后执行一次全套离线回归
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v
```

修改碰撞导出或新增几何测试时，补跑对应模块。全套回归通过不代表 GPU/真实任务通过；GPU 原生绑定检查、Cup 对照和小批次真实结果单列。

## 8. 文件范围与交付

| 文件/目录 | 计划职责 |
|---|---|
| `planning/curobo_v2.py` | 当前目标绑定、handle 家族兼容 key、target-local fit 失效、attach 空目标错误 |
| `planning/assets/panda_omron/spheres.yml` | P2 证实后修正底盘/柱体球 |
| `planning/panda_omron.py`、对应 URDF | 仅修复被证实的本体变换问题 |
| `planning/collision_world_v2.py` | 仅修复被证实的场景几何导出问题 |
| `runtime/execution/robocasa_tracking.py`、`runtime/control/panda_robocasa.py` | 仅修复 P4 复现的执行问题 |
| 现有 `tests/planning/`、`tests/runtime/` | 对应缺陷回归；几何与真实执行边界清楚分开 |
| `scripts/experiments/` | 如现有 probe 不足，新增一个定向诊断脚本，共用项目模型/碰撞函数，不另造执行链 |
| `outputs/diagnostics/<run-id>/` | 一份诊断主 JSON、必要对照图和日志；引用历史结果 |
| `outputs/<new-run-id>/` | 现有标定格式的 raw trial 和真实执行产物 |
| `reports/no-edit-calibration/delivery/` | 一份阶段交付报告，随阶段更新，写实际修复、对照结果和剩余限制 |

诊断脚本不增加公开 CLI 命令，不建立泛化审计/校验框架，不复制场景或大型资产。每个阶段产物可复用，不重复跑已通过且未受改动影响的检查。

实现遵循最新代码规范：公共绑定/失效流程只维护一份；长函数按职责拆分；新改核心逻辑通过参数、成员变量或必要 dataclass 显式传递依赖，不依赖闭包变量，不在函数内定义复杂类。若实际修复需要修改现有 NativeGraspPlanner/ContactManager 的核心逻辑，同步将对应类放到模块顶层并显式传入 owner/context；未涉及的历史代码不为本轮方案做全库重构。

## 9. 本次方案编写记录

- 已在指定 worktree 检查 Git 状态；保留用户未跟踪的 `reports/panda-omron/analyze/` 内容。
- 已复核现有报告、PlannerResources/V2Planner、checker/cost manager 持有关系、carried fit 缓存、planning worker 路径及跟踪测试。
- 在已有“重算 target/handle id”基础上，补齐 Pick/Open checker 结构兼容和同 model 不同 target 的拟合缓存失效，避免半修复。
- 本轮新增本方案并更新报告入口。没有修改实现、采样参数、外部资产、历史实验或用户已编辑的分析报告；没有运行新的软件/GPU/物理实验。
