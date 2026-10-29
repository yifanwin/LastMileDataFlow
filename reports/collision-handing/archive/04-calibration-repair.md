# PandaOmron 标定修复：阶段交付

- 状态：archived（2026-10-28 迁入 `collision-handing/archive/`，作为 N0–N5 与凸包修复的历史证据保留、不再追加；新结果写入本主题 `delivery/`。文中 `outputs/experiments/...` 位于 `.worktrees/no-edit-calibration/`。归档前状态：N5 与凸包版本的固定 12 站对照完成；保留凸包修复，已采样自由空间假内部误判消除；重叠区域漏判、val107 退化及残余任务失败待处理，复核见第 14 节）
- 日期：2026-10-27（第 10–14 节 2026-10-28）
- 替代：无；第 6 节为只读复核，第 7 节记录 N0–N2 实施结果，第 10 节记录 N3/N4 实施结果，不改写前文已有实验结果
- 关联：[修复诊断方案](03-calibration-repair-and-diagnosis.md)、[重构计划](../../no-edit-calibration/plan/calibration-repair-schedule.md)、[复用缺陷分析](../../no-edit-calibration/analyze/03-planner-reuse-target-binding.md)、[Cup_30 顺序对照](../../no-edit-calibration/analyze/02-cup30-reuse-order-experiment.md)

原阶段记录时改动尚未提交；本轮只读复核开始时工作区干净，HEAD 为 `023ca75 refactor: fix planner state reuse and restructure code`。第 7 节的代码改动在该 HEAD 之上，尚未提交。以下 GPU/测试结果仍只代表各节注明的实际运行版本。

## 1. P1：planner 复用状态修复（完成）

### 1.1 修改

文件：`src/lastmile_dataflow/planning/curobo/planner.py`（原 `curobo_v2.py`）。

| 缺陷 | 修复 |
|---|---|
| `rebind_resources()` 复用首次任务的 `target_bodies`/`handle_bodies` | 新增 `bind_target(sim)`，初始化与 rebind 共用，从当前 sim 的 `target_id`/`handle_id` 计算子树 |
| Pick/Open 共用一个 cost manager，`handle_cost` 在构造时绑定 | `PlannerResources` key 增加 `bool(handle_id)`，Pick 与 Open 分属不同家族；同家族内仍复用，checker 对象不替换，只 `load_collision_model()` 更新内容 |
| 携带物拟合只按 model 判定复用 | 新增 `fit_matches(sim)`：同时匹配 model 对象身份与 `target_id` |
| target world 为空时 `mesh[0]` 抛 `IndexError` | 在索引前抛出带 `target_id` 和 body 名的 `ValueError`，沿 `episode_error` 路径记录 |

新增测试：`test_planner_reuse.py` 4 个（不同 model 同号 body、Pick/Open handle 子树、同 model 换目标的 fit 失效、空目标报错），`test_resources.py` 2 个（Pick/Open 分家族、refresh 时 retained checker 加载当前 target/handle 几何，含 target=handle 的合法空 target world）。

### 1.2 GPU 对照（GPU 6/7，单 worker 串行，冻结输入不变）

| run | Cup_30 | 其余任务 | episode_error |
|---|---:|---|---:|
| `exp-cup30-only-20261027-1542`（基准，单独） | 37/60 | — | 0 |
| `p1fix-cup30-only-20261027`（修复，单独） | 37/60 | — | 0 |
| `exp-cup30-last2-20261027-1600`（基准，Cup_30 最后） | 0/60 | soap 2/3、vase 0/36、pepper 0/33 | 55 |
| `p1fix-cup30-last2-20261027`（修复，Cup_30 最后） | 37/60 | soap 2/3、vase 10/36、pepper 21/33 | 0 |

- 修复后单独运行与基准逐状态一致（37 success / 2 failure / 21 planning_no_solution）。
- 顺序队列 132/132 trial 完成。按 geom 名与当前 model 目标子树比对 target world：基准 3 正确、99 空、30 错绑（pepper 绑到 `chestofdrawers`）；修复后 132/132 正确。
- 队列末尾 Cup_30 的 38 次空目标 `IndexError` 消失，成功数恢复到单独运行的 37/60。

### 1.3 验证边界

- 两组 GPU run 在 19:04 启动，用的是 P1 修复后、后续重构前的代码。下文重构只经离线测试验证，未再跑 GPU。
- 四个队列任务都是 Pick；Pick/Open 家族切换和 Open A→B 的 handle 刷新只有单元测试覆盖，没有 GPU 实测。

## 2. 代码重构（阶段 1–4 完成，阶段 5 跳过）

- **阶段 2**：`PlannerContext` dataclass（`curobo/context.py`）替代 context dict，`costs.py`、`head_fov.py` 改为属性访问；ContactManager 通过 cfg 字段 `context` 取得状态，不再依赖闭包变量；原函数内 `NativeGraspPlanner` 改为模块级 mixin `TracedPlanPose` + `native_grasp_planner_type()`，solver 追踪闭包改为 `SolverTrace` 类（`curobo/tracing.py`），owner 显式传入。`ContactManager`/`ContactManagerCfg` 仍定义在 `manager_type()` 内（cuRobo 0.8.0 依赖 cfg 类型匹配，本轮未移出）。
- **阶段 3**：`V2Planner.__init__` 从约 133 行拆为约 40 行，职责下沉到 `_native_types`、`_bind_model`、`_build_context`、`_setup_fov`、`_create_motion`、`_check_head_camera_fk`、`_write_identity`；`bind_trial`/`rebind_resources`/`refresh_world` 共用 `_reset_trial_state`、`_reset_tool_criteria`、`_target_worlds`、`_environment_world`、`_fov_target`、`_local_center`、`pose_error`。
- **阶段 4**：按用户要求分子目录：

```
planning/
├── pool.py  remote.py  waypoints.py  evidence.py   # 顶层（pool 为 spawn 入口）
├── curobo/      planner.py context.py costs.py tracing.py collision_world.py head_fov.py
├── robots/      panda_omron.py rby1m_mobile_torso.py assets/panda_omron/
└── navigation/  geometry.py（原 navigation.py） astar.py
```

  不保留旧模块名 shim；`src/`、`tests/`、`scripts/` 的导入与 patch 路径全部改写，`pyproject.toml` package-data 改为 `lastmile_dataflow.planning.robots`，同步 `planning/README.md` 和 `docs/architecture.md`。配置名 `curobo_v2_v080`、`configs/execution/curobo_v2.json` 不是模块路径，保持不变。
- **阶段 5**（collision_world 拆分，可选）：未做，文件仅 61 行。

### 2.1 离线验证

```bash
cd .worktrees/no-edit-calibration-val0-99
PYTHONPATH=$PWD/src MPLCONFIGDIR=/tmp/lastmile-mpl \
  /data1/wenyifan/lmdf/LastMileDataFlow/.venv/bin/python -m unittest discover -s tests/planning -t .
```

- `tests/planning`：70/70 通过。
- 全量 `tests/`：9 个失败/错误，集合与 HEAD 基准相同（`workflows.test_station_parallel`、`test_station_diagnosis`、`test_split_calibration` 的多进程用例）。其中 `test_one_planner_supplies_multiple_independent_cpu_trials_across_scenes` 在全量运行中时而 FAIL 时而 ERROR，单独运行在 HEAD 与本 worktree 都通过，属于既有的不稳定用例。
- 主仓库 `.venv` 的 editable 安装指向主仓库 `src`，在 worktree 跑测试必须设置 `PYTHONPATH=$PWD/src`。
- 补充（N0 发现）：阶段 4 漏改 `planning/pool.py`、`planning/remote.py` 的延迟导入 `.curobo_v2`，GPU 规划进程处理任何请求都会 `ModuleNotFoundError`；离线测试用 fixture service 未覆盖。已修复，见 7.1。

## 3. P2：几何/碰撞对照（按当前进度暂停）

已完成：

- **12 站诊断集**（`outputs/experiments/diagnostics/p2-geometry-20261027/selection.json`），用 `failure_analysis._planning_failure` 从 `no-edit-val-all-c001…c005` 的 6,050+ 站位确定性选出，覆盖 12 个 house：goalset 底座约束主导 6（Pick/Open 各 3）、原成功 2、低目标不可达 2、pregrasp 跟踪超时 2。
- **初始站位状态的球距离**（`scripts/experiments/p2_collision_diagnosis.py`，结果 `distances.json`、`summary.json`）：在未 begin 的新 sim 中恢复站位 `state.npz`，按规划参考系（`base_site` 位置 + `mobilebase0_base` 朝向，URDF 偏移 omron_base +0.21 m、panda_link0 +0.70 m）放置 cuRobo 球，与 `collision_world` 导出给 cuRobo 的同一组场景网格求点到网格距离。
  - `omron_base`：4/12 站已有球侵入场景网格（-0.014 至 -0.068 m；val10 椅子、val103 墙、val12 餐桌、val32 抽屉柜），其余 8 站最小净空 +0.02 至 +0.34 m。
  - `panda_link0`：12 站都没有侵入（最小 +0.049 m）。
  - 编译模型中底盘 box 为 0.70×0.50×0.38 m，固定柱/升降柱为 r≈0.12 m 的圆柱；12+8 球的包络比实体外扩，与问题梳理中的已知值一致。

未完成，结论只限于初始状态：

- （已由 7.3 的修正测量取代；原文件保留。）`distances.json` 里的物理几何距离（`physical[*].distance_m`）不可用：`mj_geomDistance` 对远处 mesh 对返回 0，被误判为接触。改用三角网格测距的版本单站耗时过长，已停止。因此目前不能区分"实体相交"和"球近似误拒绝"。
- 未对 goalset IK 末态、TrajOpt 失败轨迹做同状态对照，未做坐标/尺度三层核对和可视化。
- 由于缺少物理侧距离，P2 的五类定性归因（真实冲突/球近似误拒绝/坐标错误/SDF 错误/证据不足）尚未给出，P3 的球重拟合不应基于当前数据启动。

## 4. 未开始

- P3 单因素实验（A/B/C/D 组）、P4 可达性与跟踪排查、P5 小批次验收。
- ~~GPU 复核：重构与目录重组后的代码，以及 Pick/Open 混合队列。~~ 已完成最小检查，见 7.2。

## 5. 复现入口

- GPU run 启动脚本：`outputs/experiments/logs/p1fix-run.sh <input-dir> <run-id> <gpu>`。本机 MuJoCo EGL 需要 glvnd 库路径，脚本已设置 `LD_LIBRARY_PATH`（其中 `/tmp/lmdf-egl-libs` 在重启后可能丢失）。
- P2：命令已随脚本修正变化，当前入口见 7.5。

## 6. 方案重梳与严重性判断（2026-10-27，只读复核）

**需要修复的是当前诊断工具中的确定错误；尚未确认主规划链路出现新的严重回归。** 已将下一步重写到[修复与排查方案](03-calibration-repair-and-diagnosis.md)，本轮未恢复暂停的实验。

- P1 Pick 顺序缺陷已有真实恢复证据，不重新执行整套修复。当前重构版本和 Pick/Open 混合队列仍需最小 GPU 验证。
- 当前诊断脚本仍读取不存在的旧 `planning/assets/panda_omron/spheres.yml`，新路径在 `planning/robots/assets/`；这是确定的运行错误。
- 手写 `LINK_OFFSETS` 未计入 torso 位移，非零升降下的 link0 球位置会错；物理升降柱与规划 link 的对应也需拆清。不能据此直接否定所有 torso=0 初始状态结果。
- 球距离为无符号表面距离减半径：负值提示球与表面相交，但不等于可靠穿透深度；正值不能排除球心已位于实体内部。因此第 3 节的“净空/侵入”应按这一较弱语义理解，不作为最终碰撞判据。
- 当前随机表面采样替代方法既昂贵，也会漏判面/边交叉和反向包含。旧 physical 字段继续无效，不能用该替代方法直接支持缩球。
- 6 个 goalset 底座主导样本中，仅 val10、val103 的初始球表面余量为负；另外两个负值属于低目标组。优先检查实际 IK 候选和失败轨迹，不能由初始状态推断全体 goalset 的失败原因。
- 对 `mj_geomDistance` 的零值，先核查调用参数、截断语义、实际版本和 CCD 路径，再决定替代方法；具体依据与步骤见方案 N2。尚未确认零值的根因。
- `.venv` 已存在但缺少 mujoco/torch/curobo/trimesh/项目包。第 5 节入口目前不能直接复现；需先补齐 worktree 独立依赖并修诊断脚本。旧 GPU 启动脚本使用主仓库解释器，保留为历史说明，不作为后续推荐运行入口。

后续顺序为：独立环境与必要局部整理 → 当前版本最小 GPU 检查 → 单几何对可靠测距 → 四个代表站位及实际失败候选 → 证实后局部修球 → 固定 12 站和必要的小批次。`costs.py` 的函数内复杂类/工厂 context 闭包仍属规范欠账，限定范围处理，不扩展为新一轮全库重构。

本轮完成源码/现有 JSON/解释器包发现检查和官方 API 语义查询，修改方案与报告入口；未修改程序或实验产物，未运行新的离线回归、GPU 规划或物理 rollout。

## 7. 方案 N0–N2 实施结果（2026-10-27）

**当前重构代码的 Pick 真实执行和 Pick/Open 家族复用通过最小 GPU 检查；修正后的同状态测距显示，初始状态下 4 个球侵入站位的实体底盘均未接触障碍。** 实施中发现并修复了重构遗漏的 GPU 规划进程导入错误。

### 7.1 N0：环境与局部整理

- **环境**：按用户 2026-10-27 的要求，没有给 worktree 单独下载依赖，改用主仓库 `/data1/wenyifan/lmdf/LastMileDataFlow/.venv`，配合 `PYTHONPATH=<worktree>/src`。已核实 `lastmile_dataflow.__file__` 指向 worktree；版本为 mujoco 3.5.0、torch 2.7.1+cu128、nvidia-curobo 0.8.0、warp-lang 1.11.1。这与方案 N0 原定的“worktree 独立 `.venv`”不同：worktree 的 `.venv` 目前只有 pip/setuptools，不能直接使用。
  本机 MuJoCo EGL 需要 `LD_LIBRARY_PATH` 包含 `/tmp/lmdf-egl-libs/root/usr/lib/x86_64-linux-gnu`（同 `outputs/experiments/logs/p1fix-run.sh`）；少了这项，导入 mujoco 时 OpenGL EGL 初始化失败，测试会成批报错。
- **修复导入回归**：`planning/pool.py`（`PlanningService.__init__`、`execute`）和 `planning/remote.py` 从 `.curobo_v2` 改为 `.curobo.planner`。新增 `tests/planning/test_pool.py::test_resident_service_uses_the_current_planner_module`。用静态扫描检查了 `src/`、`tests/`、`scripts/` 中所有指向本包的 import，没有发现其他不存在的模块。
- **`costs.py` 规范整理**：`ContactManager`、`ContactManagerCfg` 移到模块顶层，`compute_costs` 拆成目标/把手接触、工作区圆、头部 FOV 三个方法，行为不变。cuRobo 0.8.0 要求 `type(cfg)` 与 `cost_manager_config_instance_type` 完全相同，而且只通过 `type.create(data_dict, ...)` 创建配置，所以 `manager_type(context)` 返回一个只带 `planner_context` 类属性的子类，不再用闭包捕获 context。由于 `costs.py` 现在导入时就加载 cuRobo，`planner.py` 改为在 `_create_motion` 内才导入它，CPU 执行路径不受影响。新增 `test_curobo.py::test_cost_manager_type_carries_its_own_planner_context`。
- **`collision_world.py`**：把 geom 选择与网格转换抽成 `exported_geoms()`，`collision_world()` 与诊断脚本共用同一套导出规则，输出不变。
- **离线回归**（主仓库解释器 + worktree `PYTHONPATH` + 上述 EGL 路径）：`tests/planning` 72/72 通过；全量 `tests/` 396 条通过（跳过 2 条）。2.1 节记录的 9 个多进程失败这次没有出现；在 HEAD 临时检出上用同一环境单独跑这三个模块，也是 10/10 通过。因此那 9 个失败更可能与当时的环境有关，但具体原因未确认。

### 7.2 N1：当前版本 GPU 检查

**Cup_30 真实执行**（`n1-cup30-s0022-s0001-20261027`，GPU 6，单 worker，规划复用开启）。输入只保留原第 8、9 站（S0022、S0001），执行配置只把 seed 改为 66，使 6 个 trial 的种子与 `p1fix-cup30-only-20261027` 的 66–71 完全相同。输入和配置在 `outputs/experiments/inputs/n1-cup30-s0022-s0001-20261027/`。

| 站位 | 原结果（P1 修复后、重构前） | 当前代码 |
|---|---|---|
| S0022 | 3 × planning_no_solution | 3 × planning_no_solution |
| S0001 | 3 × success（执行 421/271/302 步，抬升 0.1015/0.0993/0.0984 m） | 3 × success，步数与抬升逐条相同 |

第二站 `planner_reuse.json` 为 `solver_reused: true`，run `status=completed`，没有 episode 错误。

**Pick/Open 家族复用**（`scripts/experiments/n1_family_reuse_check.py`，结果 `outputs/experiments/diagnostics/n1-family-reuse-20261027-r3/family_reuse_check.json`，GPU 7，只做规划、不执行）。用已有 run 的 `state.npz`、任务、抓取候选和原 trial 种子重建冻结的 pre-close 请求，按 Pick → Open A → Open B → Pick 的顺序发给同一个 `PlanningService`，再把每个请求单独发给 fresh planner（`reuse_planners=False`）作对照。

| 查询 | 家族 | 实际复用 | 导出世界 = fresh | 规划结果（复用 = fresh） |
|---|---|---|---|---|
| Pick Cup_30 S0001 | Pick | 否（新建） | 是（target 50 geom） | 3/3 成功 |
| Open A val93 S0044（target≠handle） | Open | 否（新建） | 是（target 6、handle 11） | 3/3 成功 |
| Open B val101 S0000（target=handle） | Open | 是 | 是（target 0 为合法空集、handle 11） | 2 成功、1 无解 |
| Pick Cup_30 S0006 | Pick | 是 | 是 | 3/3 成功 |

- 每个 planner 有 28 个 `ContactManager`，都持有本 planner 的 context；其中 16 个有场景碰撞配置，它们的 target cost 引用的正是 `context.target_checker`。Open 的 handle cost 恰好出现在这 16 个上并引用 `context.handle_checker`，Pick 一个也没有。接触 mask 下标和 contact links 一致。查询结束后 attachment 球都已恢复为参考值。
- 复用与 fresh 的每条 lane：状态相同，选中的 grasp 序号相同，路点最大差 0.0。
- 边界：Open 只验证规划语义，没有做真实执行。val101 的历史 3/3 规划成功来自 P1 修复前的错绑代码，不能作为对照。r2 用了错误的检查规则（漏了“只有带 target cost 的 manager 才有 handle cost”这个前提），结果保留在 `-r2` 目录；第一次运行因脚本里的 `link_spheres` 索引错误中断，保留在无后缀目录。

### 7.3 N2：测距方法修正

`scripts/experiments/p2_collision_diagnosis.py` 已重写：

- 球心和有效半径来自 cuRobo FK，输入是 planner 自己的 `PlanningModel`（`configuration()` 读取 `robots/assets/panda_omron/spheres.yml`，包含 torso 锁定值和实测底盘参考系），不再拼旧路径或手写偏移。
- 球组与物理部件分开对应：omron_base 球 0–11 对底盘 box，12–19 对固定柱 `g0_col`；panda_link0 球 1–8 对随 torso 移动的升降柱 `g1_col`，球 0 对 link0 mesh。
- 障碍集合用 `exported_geoms()`，范围与生产一致（`2·radius_m+1` 的 XY 范围），按 environment/target/handle 三个世界导出，Open 绑定当前 handle。
- 规划侧数值改为球心到该 geom 导出网格的 SDF（cuRobo checker、warp 射线判符号）减有效半径。整体判决读取与 planner 相同的三个 checker。删除了随机表面采样。
- 物理侧对每个“部件—障碍”对调用 `mj_geomDistance`（distmax=1 m，返回 distmax 时记为截断），检查见证线段长度是否等于返回值，同时记录另一种 CCD 模式的结果。距离不可信或小于 0.1 m 时，用顶点凸包的二次规划（QP）独立求距离；MuJoCo 物理碰撞用的正是 mesh 的凸包。QP 结果大于 0 时直接采信（凸包上的最小距离就是分离证据）；QP 判为相交时，只有 MuJoCo 的值自洽且不为正才采信；其余情况记为 unavailable。

**已知用例**（每次运行先执行，全部通过才测站位）：box 对 box、凸 mesh、cylinder，分离 0.1 m、相交 0.05 m、超出 distmax 三种情况，在 native 和 legacy CCD 下符号、数值、截断都正确；cuRobo 对封闭 box 网格的内外符号正确（+0.3/−0.05/−0.1 m）；开口单面网格两侧都给出 +0.1 m，即没有“内部”（只记录，不作断言）。

**`mj_geomDistance` 零值的定位**（val103/S0000，底盘 box 对墙）：

| 障碍 | native CCD | 见证线段 | legacy CCD | 凸包 QP | 接触表 |
|---|---:|---|---:|---:|---|
| `wall_2_5`（5 mm 薄板 mesh） | +0.0042 | 一致 | −0.694 | +0.0042 | 无 |
| `wall_0_6`（5 mm 薄板 mesh） | 0.0 | 长约 0.47 m，不一致 | −0.692 | +0.0092 | 无 |

- 零值和 distmax（0.02/0.1/1/10 m）、ccd_tolerance（1e-6/1e-10）、ccd_iterations（35/100）都无关。这是 native CCD 在这类“box 对薄板凸包”配对上的距离查询失败，不是调用参数或截断语义的问题；MuJoCo 内部的具体原因没有继续追查。在诊断模型上把 ccd_iterations 设为 1000 会让 MuJoCo 段错误（不涉及生产配置）。
- legacy CCD 在这两对上给出约 −0.69 m 的错误穿透，见证线段长度却与返回值一致，所以单靠见证一致性不能证明结果正确。
- 12 站共 665 个“部件—障碍”对中，有 11 对出现这种不一致的零值，全部改由凸包 QP 给出距离。

**固定 12 站初始状态**（`outputs/experiments/diagnostics/p2-geometry-n2-20261027/initial-states.json`，每站一张俯视叠加图 `initial-states-<house>-<station>.png`；val103 的首次单站核验结果在同目录的 `val103.json`）：

| house / station | 组 | 底盘球最小（SDF − 半径） | 同障碍实体底盘距离 | planner 判碰撞的球 |
|---|---|---:|---:|---|
| val10 / S0000 | goalset 底座 | −0.016（chair） | +0.047 | 底盘 1 |
| val103 / S0000 | goalset 底座 | −0.070（wall） | +0.0042 | 底盘 4 |
| val12 / S0007 | 低目标 | −0.027（餐桌） | +0.037 | 底盘 2 |
| val32 / S0045 | 低目标 | −0.016（抽屉柜） | +0.068 | 底盘 4 |
| 其余 8 站 | — | +0.018 至 +0.340 | +0.10 至 +0.43 | 0 |

- 12 站 torso 都是 0（planner 锁定值也是 0），所以旧 `LINK_OFFSETS` 漏算 torso 不影响这批初始状态。升降柱、固定柱、link0 的球在 12 站都没有侵入。
- 初始状态下有球侵入的 4 站，实体底盘与同一障碍都分离 4 mm 到 7 cm，属于“球近似导致的同状态误判”；另外 8 站球与实体都分离。按“底盘球最小值”和“实体最小值”比较，规划侧余量比实体小 7–9 cm（val100 为 2.9 cm），与球包络外扩一致；两侧最近障碍不一定是同一个 geom，只作量级参考。
- 这只说明初始状态。6 个 goalset 样本里只有 val10、val103 初始就侵入，其余 goalset 失败需要看实际 IK 候选和轨迹（N3）。另外，N1 中 Cup_30 S0022 规划时 cuRobo 反复输出 `Start or End state in collision`，起止状态碰撞是 N3 应优先核对的线索。

### 7.4 未做与限制

- 没有修改碰撞球、buffer 或 activation，没有恢复任何暂停的实验，没有提交代码。
- N3（实际 IK/轨迹候选的同状态对照）及之后的步骤都未开始。7.3 的“误判”结论只覆盖初始站位状态，还不能推出“这些站位缩球后就能规划成功”。
- 物理侧对 primitive 圆柱用 128 边形近似，误差小于 0.04 mm。非凸障碍在物理里按每个 geom 的凸包碰撞，在规划里按原始三角网格；两者的差异按“表示差异”记录，没有统一修正。

### 7.5 复现入口

```bash
cd .worktrees/no-edit-calibration-val0-99
export PYTHONPATH=$PWD/src MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  LD_LIBRARY_PATH=/tmp/lmdf-egl-libs/root/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
PY=/data1/wenyifan/lmdf/LastMileDataFlow/.venv/bin/python

# N1 Cup_30 真实执行（GPU 按实时空闲选择）
D=$PWD/outputs/experiments/inputs/n1-cup30-s0022-s0001-20261027
$PY -m lastmile_dataflow.cli evaluate-stations --dataset $D --config $D/execution_seed66.json \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output-dir $PWD/outputs \
  --run-id <new-run-id> --gpu <gpu> --station-workers 1 --planning-workers-per-gpu 1 \
  --cpu-workers 1 --preparation-workers 1 --recording-mode numerical

# N1 家族复用（查询格式 run:scene_id:station_id，依次给出）
$PY scripts/experiments/n1_family_reuse_check.py --gpu <gpu> \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output-dir <new-dir> --queries ...

# N2 测距（cuRobo checker 用 CUDA_VISIBLE_DEVICES 指定的卡）
CUDA_VISIBLE_DEVICES=<gpu> $PY scripts/experiments/p2_collision_diagnosis.py \
  outputs/experiments/diagnostics/p2-geometry-20261027/selection.json [--stations val103/S0000] --output <new.json>
```

日志：`outputs/experiments/logs/n1-cup30-s0022-s0001-20261027.log`、`n1-family-reuse-20261027-r3.log`、`p2-geometry-n2-20261027.log`、`n0-unittest-full-20261027.log`。

## 8. 基于第 7 节的下一轮建议（仅文档整理）

- N0–N2 的最小验证已完成，不再重复环境安装、完整 P1 队列或扩大 MuJoCo 薄板测距排查。按用户已授权方式继续使用主仓库解释器加载 worktree 源码。
- 4 个初始站位已有“实体分离、planner 球碰撞”的同状态证据，允许进入 **chassis 球局部修正 A/B**；不必等待全部 IK/轨迹失败完成解释。固定柱、升降柱和 link0 在这批初始状态没有侵入，先保持不变。
- 优先 val10（同障碍实体间隙约 47 mm）观察假碰撞是否消除；val103 只有约 4.2 mm，作为保守边界样本，不为强行通过它而损害实体覆盖。val12/val32 仍可能受低目标可达性限制。
- 同时定位 Cup_30 S0022 的真实 start/end/trajectory 拒绝状态，保持原 seeds 66–68；S0001 的 seeds 69–71 和三条真实成功作为对照。新增候选证据对应实际 query/phase/lane/seed，不凭日志一句 `Start or End` 决定原因。
- 球数/顺序变化后同步诊断脚本 `PARTS` 的 chassis/column 下标，并重建 solver。先保持 2 mm buffer 和 10 mm activation，避免同时调整多个因素。
- 验证分为几何一致、规划通过、物理执行、任务成功四层；初始假碰撞消失但仍因可达性失败，保留为有效分层结果。Open 真实执行尚未覆盖，出现可行轨迹后选一站 3 trial 补齐，不能用规划复用一致代替操作成功。

已把这些建议写入方案第 3.2、6、7 节，并将过时的待修项标为完成、同步最新测试结论。本次保留已有程序改动与实验产物，仅更新两份文档；未修改碰撞球、运行新测试或启动实验。

## 9. 球拟合方案选择建议（只读评估）

**建议按几何选方法：机械臂一般 mesh 优先直接调用官方 MORPHIT；当前已证实有问题的规则 chassis box，保留现有 ILP 球作为专项候选，先精简和 A/B，不重新手写 MORPHIT/VOXEL/SURFACE。** 柱体与其他臂 link 尚无本轮同状态故障证据，不因选择拟合工具而全机器人重拟合。

本次读取已有 `outputs/experiments/diagnostics/n4-chassis-spheres-20261027/chassis_spheres.json`，没有重新拟合。按该脚本“完整实体覆盖”的比较口径：

| 方法 | 实际球数 | 原始最大网格覆盖缺口 | 补覆盖的统一半径增量 | 补齐后侧向/顶部外扩估计 |
|---|---:|---:|---:|---:|
| block-cover ILP | 55 | 0 | 0 | 34 / 59.8 mm |
| MORPHIT，申请 64 | 61 | 43.8 mm | 48.1 mm | 90.3 / 89.2 mm |
| MORPHIT，申请 100 | 92 | 44.6 mm | 48.9 mm | 86.7 / 81.9 mm |
| VOXEL，申请 64 | 64 | 82.4 mm | 86.7 mm | 86.7 / 86.7 mm |

这些是一次拟合和当前参数下的已有结果，不证明所有 MORPHIT 参数都不适合 box。官方 coverage 是内部采样点覆盖比例，不保证所有棱角被覆盖；申请球数与实际返回数也可能不同。不能用 coverage 较高或包络看起来较紧，替代实际边界误差和物理执行验证。

### 9.1 各方法的定位

- **MORPHIT**：普通机械臂 link 的首选官方拟合候选，复用现成优化与质量指标；不要另写同类优化器。必要时对出现问题的部位补球或小范围调整，不为追求一个 coverage 数值统一放大所有球。
- **VOXEL**：适合作为快速基线/初始化；当前箱体结果的覆盖缺口明显，不能因为内接球外扩小就直接用于底盘碰撞模型。
- **SURFACE**：薄片/退化 mesh 的候选；固定半径表面球不自动保证厚实体内部和角部覆盖，不是当前厚箱体首选。本轮未运行 SURFACE 对照。
- **ILP block cover**：利用当前 box 的特殊结构，在选定格点子块和外扩限制下求覆盖方案；不是全机器人通用拟合器，也不是全空间最少球证明。运行期仍只读取普通球 YAML，MILP 仅用于离线生成。

### 9.2 当前 ILP 需要的小修正

1. **去掉重复球。** 本次读取生成 YAML：55 行只有 51 个不同的中心/半径组合；去重保持球并集不变，可省掉 4 个冗余球。已检查没有进一步被单个其他球完全包含的不同球。尚未实际修改 YAML。
2. **外扩指标改为确定的面法向值。** 当前 `evaluate()` 的随机球面采样“side/top bulge”与 `admissible()` 的面法向约束不是同一指标。对最终序列化球直接求 `center ± radius`：当前精确侧向最大外扩 34.1 mm、顶部 59.8 mm、底部 236.1 mm，均尚未加生产 2 mm buffer。下方不设限是该 ILP 的有意设定，比较时需说明，不能称为各方向均贴合。
3. **覆盖膨胀是保守上界。** 5 mm 格点加半对角线的方法可以提供该 box 的覆盖上界，但不是最小必要膨胀；表中 MORPHIT/VOXEL 的“膨胀后外扩”是这一后处理策略的代价，不等于官方原始拟合外扩。公平 A/B 比较实际写入并加载的最终球，包含取整与 buffer。
4. **有限预算结果按实际状态命名。** 当前已有 run 的 MILP status=0；后续若超时，应仅在有可行解且所选子块覆盖全部 cell 时导出，并报告可行解而非最优解。只需要这个现有求解边界检查，不扩展通用校验框架。

底部球伸出并不自动造成错误，因为当前 planner 不导出地面；但不能直接靠 clip_plane 切掉这些部分而不复核覆盖。当前 ILP 没有对底部施加与官方拟合相同的贴合偏好，这也是比较边界。暂不为此增加大量约束或再做参数网格搜索。

### 9.3 实际 API 与下一步

已读取当前安装的 cuRobo 0.8.0 源码：入口为 `curobo._src.geom.sphere_fit.fit_spheres_to_mesh`（项目 attach 路径已在使用）；参数/结果字段为 `num_spheres`，当前函数默认 `iterations=200`。用户贴文中的 `n_spheres`、50 iterations 与本机接口不同，调用以安装源码为准。

下一步不重复已有 8 组官方拟合。先对现有 ILP 去重、修正报告指标，更新诊断 PARTS 分组并重建 solver；沿原球/候选球固定站位 A/B 检查假碰撞消除、实际成功和规划用时。若球数或运行代价明显不合适，再对官方 MORPHIT 做一次有针对性的参数/局部补球改进，比较同等误差要求下的开销。机械臂一般 mesh 使用官方路线，但当前底盘选型由已有几何结果与执行结果决定。

本轮仅检查本机 API、已有拟合 JSON 和生成 YAML，并做重复球/轴向外扩的只读计算。没有运行 GPU 拟合、修改用户现有球配置或宣称 N4 真实 A/B 已通过。

## 10. N3/N4 实施结果（2026-10-28）

**新 chassis 球（51 个，按区块证明完整覆盖实体底盘，侧向外扩含 buffer 36 mm，原球 107 mm）消除了 val10/val12/val32 的初始假碰撞；val10 由 0/3 变为至少 1/3 真实成功，原成功对照没有退化，回放未发现新增的机器人–环境接触。代价是规划变慢：同等工作量的查询约慢 1.0–1.4 倍（Pick），Open 站 val108 的活动预算占用由 81% 升到 89%，并发运行时曾 2/3 超时。** Cup_30 S0022 的 `Start or End state in collision` 已定位为起点状态碰撞（实体只隔 1.4 mm，并有固定柱球假碰撞），换底盘球不能使其通过。

按用户顺序实施：1+4（chassis 球与诊断分组）→ 2（val103 边界）→ 3（S0022 拒绝状态）→ 5（分层评价）。第 9.2 节的四项修正（去重、精确外扩、导出条件、按实际求解状态命名）已在 10.1 中完成。

### 10.1 改动

| 文件 | 改动 |
|---|---|
| `planning/robots/assets/panda_omron/spheres.yml` | omron_base 前 12 个底盘球（r=0.21）换为 51 个块覆盖球；固定柱 8 球、升降柱、link0 和各臂 link 不变；buffer 2 mm、activation 10 mm 不变 |
| `scripts/experiments/p2_collision_diagnosis.py` | `PARTS` 改为底盘 0–50、固定柱 51–58；新增检查：各 link 的分组必须恰好覆盖该 link 的全部球，否则报错；新增 `chassis_box_max_uncovered_m`（1 cm 网格，扣除 buffer）；物理距离采信规则抽成 `physical_distance()` 供 N3 复用；`load_station` 改用绝对路径 |
| `scripts/experiments/n4_chassis_spheres.py`（新） | 块覆盖 ILP 生成球、去重、写 YAML 片段；同口径与官方 `fit_spheres_to_mesh` 对比 |
| `scripts/experiments/n3_rejection_states.py`（新） | 冻结 pre-close 请求的拒绝状态定位，只加观察钩子 |
| `scripts/experiments/n4_ab_summary.py`（新） | A/B 分层表、耗时统计、执行轨迹接触回放 |
| `scripts/experiments/station_subset_input.py`（新） | 从已有输入抽取单站位、按原种子生成执行配置 |

**球的构造。** 底盘是一个 MuJoCo box（0.70×0.50×0.38 m，omron_base 系 x∈[−0.55, 0.15]，z∈[0.002, 0.382]，由 URDF `omron_root` x=+0.21 与实测 box 位姿换算）。把 box 切成 28×20×12 的格子，候选为任意格对齐子块，球取子块的外接球，因此“所选子块的并 = box”即证明球完整覆盖实体。候选只保留外扩不超过“侧面 35 mm、顶面 60 mm”的子块（底面贴地，planner 不导出地面，不设限），集合覆盖 MILP 选最少子块。结果：55 个子块，HiGHS 状态 Optimal（是这组格对齐候选下的最优，不是全局最少球数）；其中 4 对子块的外接球相同，去重后写入 51 球，球的并集不变。中心取整到 0.1 mm，半径按取整偏移向上加宽。

**最终加载球的外扩**（`center ± radius`，含取整与 2 mm buffer，`n4-chassis-spheres-dedup-20261028/chassis_spheres.json`）：

| 方案 | 球数 | 实体覆盖 | 侧向 | 顶面 | 底面 |
|---|---:|---|---:|---:|---:|
| 原球 | 12 | 网格缺口 0.2 mm（需补 4.6 mm 才能证明） | 106.6 mm | 114.6 mm | 118.6 mm |
| 块覆盖 ILP | 51 | 子块证明完整覆盖 | 36.1 mm | 61.8 mm | 238.1 mm（不设限） |

官方拟合只在 2026-10-27 用旧口径（随机球面采样外扩）跑过一次（`n4-chassis-spheres-20261027/chassis_spheres.json`，第 9 节已引用），本轮没有重跑：MORPHIT 申请 64/100 球时，覆盖缺口为 44–45 mm；要补齐覆盖需把半径统一加大约 48 mm，加大后外扩 82–90 mm。这个“补齐后外扩”是本脚本后处理的代价，不代表官方原始拟合的外扩。

**实测覆盖。** 12 站真实初始状态下，用 cuRobo FK 得到的底盘球（扣除 buffer）覆盖 MuJoCo 物理 box：最大未覆盖距离均为 −0.09 mm（≤0 即覆盖），说明球的坐标系和偏移与实体一致。

### 10.2 几何层：12 站初始状态（`n4-geometry-b51-20261028/initial-states.json`）

| house / station | planner 碰撞的底盘球 A → B | 底盘球最小余量 A → B（m） | 同障碍实体底盘距离（m） |
|---|---|---:|---:|
| val10 / S0000 | 1 → 0 | −0.016 → +0.020 | +0.047 |
| val12 / S0007 | 2 → 0 | −0.027 → +0.015 | +0.037 |
| val32 / S0045 | 4 → 0 | −0.016 → +0.035 | +0.068 |
| val103 / S0000 | 4 → 15 | −0.070 → −0.025 | +0.0042 |
| 其余 8 站 | 0 → 0 | 均为正，最小 +0.072 | +0.10 至 +0.43 |

- val103 是边界样本：实体距墙 4.2 mm，小于新球 36 mm 的侧向外扩，仍判碰撞。碰撞球数增多是因为单球变小，侵入深度由 70 mm 降到 25 mm。按约定不为这一点牺牲覆盖。
- 固定柱、升降柱和 link0 的球在 12 站初始状态下仍无侵入。已知检测用例全部通过。

### 10.3 Cup_30 S0022 的拒绝状态（N3，`outputs/experiments/diagnostics/n3-rejection-20261027/`）

用原 seeds 66–68 和冻结的 pre-close 输入，在新建 planner 上重放同一请求，只加观察钩子：IK 的起点与返回目标、TrajOpt 每个 seed 每轮优化的逐时间步约束（排序选优前）、PRM 起止检查。

| 证据（A 原球，`s0022-a.json`） | 结果 |
|---|---|
| IK 目标 | 5 次尝试 × 12（3 lane × 4 seed）全部可行，位姿误差约 1e-7 |
| 起点状态（IK 指标） | 不可行：`scene_collision`，omron_base 球 10、11、19 |
| TrajOpt | 每次尝试 12/12 seed 不可行；均从第 0 步（起点）开始，终点从未不可行；不可行段持续到第 23–72 步（共 81 步） |
| PRM 起止检查 | 起点 0/12 可行，目标 12/12 可行：日志的 `Start or End` 实为 **Start** |
| 碰撞对象 | 底盘球 10、11 侵入同一餐桌 `collision_4` 61 mm，**实体底盘与它只隔 1.4 mm**；固定柱球 19 侵入餐桌 `collision_1` 7 mm，实体固定柱距它 109 mm（柱体球假碰撞） |

- 新球（`s0022-b.json`）：底盘球侵入降到 14–15 mm，实体仍隔 1.4 mm；柱体球 19 不变，起点仍不可行，三条仍规划无解。S0022 与 val103 同属近接触边界样本，并且多了一个柱体球假碰撞；按约定本轮不动柱体，留作后续证据。
- S0001 对照（`s0001-a.json`/`s0001-b.json`）：起点可行，PRM 检查 12/12 通过，不出现该日志；A/B 选中同样的抓取序号（15/5/5），3/3 成功。TrajOpt 的不可行 seed 都卡在中间时刻的 `cspace`，起点和终点都没有问题。
- N1 日志里的 6 行 `Start or End` = planner 预热 1 行（`warmup` 内的 `plan_cspace` 也做这项检查）+ S0022 的 5 次尝试，与 S0001 无关。批量模式下任一 lane/seed 的起点或目标不可行，整次图种子即放弃，但 TrajOpt 仍照常运行，所以这句日志本身不是最终拒绝原因。

### 10.4 分层 A/B（受控：每站 A、B 同时运行，各独占一张 GPU）

A 为 HEAD `072e38c` 导出的源码（原球），B 为 worktree（51 球）；输入、种子、预算、单 worker 配置相同。run 为 `n4a-<站>-iso-20261028` / `n4b51-<站>-iso-20261028`，汇总在 `outputs/experiments/diagnostics/n4-ab-iso-20261028/ab_summary.json`。

| 站位 | 假碰撞（初始） | 规划通过 A → B | 执行完成 A → B | 任务成功 A → B | B 失败的位置 |
|---|---|---|---|---|---|
| val10 S0000（Pick，间隙 47 mm） | 消除 | 0/3 → 3/3（pre-close） | 0 → 3 | 0/3 → 1/3 | 2 条抓取后 lift（pose）TrajOpt 无解 |
| val103 S0000（Open，4.2 mm 边界） | 保留（预期） | 0/3 → 0/3 | — | 0/3 → 0/3 | goalset TrajOpt（同 A） |
| val12 S0007（Pick，低目标） | 消除 | 0/3 → 0/3 | — | 0/3 → 0/3 | goalset **IK**（同 A，可达性） |
| val32 S0045（Open，低目标） | 消除 | 0/3 → 0/3 | — | 0/3 → 0/3 | goalset **IK**（同 A，可达性） |
| val134 S0003（原成功对照） | 无 | 3/3 → 3/3 | 3 → 3 | 2/3 → 3/3 | — |
| Cup_30 S0001（N1 真实成功对照） | — | 3/3 → 3/3 | 3 → 3 | 3/3 → 3/3 | 执行步数 421/271/302 → 420/271/302 |
| Cup_30 S0022 | 起点近接触 + 柱体球 | 0/3 → 0/3 | — | 0/3 → 0/3 | goalset TrajOpt（起点不可行） |
| val108 S0028（Open 原成功） | 无 | 3/3 → 3/3（pre-close） | 3 → 3 | 2/3 → 2/3 | 1 条 `open_no_grip`；A 的失败是另 1 条的 approach_contact IK 无解 |

- val10 的 B 结果在两次运行间有波动：第一次（并发运行，55 球）为 2/3 成功、1 条 lift 规划失败；本次受控运行为 1/3、2 条 lift 规划失败。共同点是假碰撞消除后 pre-close 3/3 可规划并真实执行，残余失败在抓取后的 lift 规划。lift 阶段失败的原因本轮未定位，不归于底盘球。
- val134 A 的 1 条失败是 `robocasa_reference_tracking_timeout`；单站 3 trial 不足以把 B 的 3/3 解释为改善。
- **Open 真实执行已覆盖**：val108 S0028 在 A、B 下各有 2 条完成接近、接触和开合并判定 `physical_open`（674/641、680/640 步）。B 的 1 条 `open_no_grip` 原因未查；在第一次并发运行里同一 seed 也是这个结果。
- **漏覆盖检查**：所有实际执行的轨迹（受控运行中 A 9 条、B 12 条，共 21 条）按记录的 qpos 在新建、未 begin 的仿真中逐帧回放，机器人与“非机器人、非地面–底盘、非目标/把手子树”物体的接触为 0。同一方法在 val10 一条成功轨迹上检出 489 次手指–目标接触，说明回放能检出接触。限制：只检查 20 Hz 控制帧，不含帧间物理子步。

### 10.5 时间开销

只用受控运行的数据。pre-close 批量查询在 A、B 的输入完全相同，是最干净的可比单位；分阶段耗时包含该阶段的全部重试。

| 站位 | pre-close 批量查询 A → B（s） | 比值 | 求解调用数是否相同 | trial 规划耗时中位 A → B（s） | 活动预算最高占用 A → B |
|---|---|---:|---|---|---|
| val12 | 3.17 → 3.06（只到 goalset） | 0.96 | 相同 | 10.2 → 10.8 | 3% → 4% |
| val32 | 2.93 → 3.18（只到 goalset） | 1.08 | 相同 | 9.5 → 11.5 | 3% → 4% |
| val134 | 5.68 → 6.45 | 1.14 | 相同（各 1 轮） | 11.8 → 14.6 | 12% → 10% |
| val103 | 7.14 → 8.48（只到 goalset） | 1.19 | 相同 | 12.7 → 14.7 | 4% → 5% |
| Cup_30 | 14.5 → 20.0 | 1.38 | 相同 | 15.1 → 19.3 | 20% → 22% |
| val10 | 7.4 → 30.1 | 不可比 | A 在 goalset 失败，B 继续 pregrasp/grasp | 13.0 → 39.2 | 4% → 36% |
| val108 | 11.2 → 25.0 | 不可比 | goalset A 3 轮、B 1 轮；grasp A 1 轮、B 4 轮 | 43.5 → 63.3 | **81% → 89%** |

- 求解工作量相同时，Pick 类查询为 A 的 0.96–1.38 倍（5 站中位 1.14）。Open 的单次 approach_contact 查询中位 1.32 → 1.71 s（1.30 倍，A 39 次/B 31 次）。
- 改球还会改变重试轮数（val108 的 goalset 少了、grasp 多了），所以单 trial 总耗时的变化方向不固定。
- 风险在长时间的 Open 任务：val108 的“规划+执行”已用到 300 s 活动预算的 81%（A）和 89%（B）。2026-10-27 第一次 A/B 是 7 个 run 在两张卡上并发，B（55 球）规划 101–107 s、2/3 `attempt_active_budget` 超时；同一天单独复测（A、B 各独占一张卡）仍是 2/3 超时（规划 73–82 s）；本轮 51 球受控运行 0 超时（63–69 s）。超时取决于规划耗时与执行时间，在 89% 附近，GPU 负载或重试数的小变化即可越过预算。
- 并发运行时 B 的规划耗时约为 A 的 1.2–3 倍（如 val10 17 → 52 s），主要来自 GPU 争用，不能作为球数代价的估计。

### 10.6 结论与限制

- **chassis 修正有效，覆盖无已知缺口**：间隙足够的初始假碰撞（val10 47 mm、val12 37 mm、val32 68 mm）均消除；近接触样本（val103 4.2 mm、S0022 1.4 mm）保留明确的保守拒绝；12 站状态下实体全覆盖；原成功对照无退化；执行回放无新增环境接触。
- **收益分层**：val10 由假碰撞阻塞变为 pre-close 3/3 可规划执行、任务 1–2/3 成功；val12/val32 假碰撞消除后仍被 goalset IK 阻止（可达性），任务不成功不算作修复失败。
- **待决定的代价**：同等工作量下 Pick 规划约慢 14%（中位），Open 长任务接近活动预算上限。要不要接受、是否需要减少球数（例如侧向外扩放宽到 40 mm：在 21×15×8 网格上的一次未去重求解为 42 球，或对官方 MORPHIT 做针对性改进），需要用户决定；本轮未调整预算、未扫描参数。
- 未做：柱体球（S0022 中有 7 mm 假碰撞，外扩约 11–12 cm）、val104 IK 候选、val10 lift 失败与 val108 `open_no_grip` 的归因、固定 12 站的完整 36 trial 扩展、代码未提交。离线回归：`tests/planning` 72/72，全量 396 条通过（跳过 2 条，`outputs/experiments/logs/n4-unittest-full-20261028.log`）。
- 每站只有 3 个 trial；任务成功率的差异（如 val10 1/3 与 2/3）在这个样本量下不稳定，只作分层结论的证据，不作比例估计。

### 10.7 复现入口

```bash
cd .worktrees/no-edit-calibration-val0-99
export PYTHONPATH=$PWD/src:$PWD/scripts/experiments MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  LD_LIBRARY_PATH=/tmp/lmdf-egl-libs/root/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
PY=/data1/wenyifan/lmdf/LastMileDataFlow/.venv/bin/python

# 球设计（约 6 分钟；去掉 --skip-curobo-fit 会同时跑官方拟合对比）
$PY scripts/experiments/n4_chassis_spheres.py --skip-curobo-fit --output <new-dir>
# 单站输入（以 val10 为例；种子同原 run）
$PY scripts/experiments/station_subset_input.py --source outputs/v1-no-edit/inputs/no-edit-val-all-c001 \
  --scene <scene_id> --stations S0000 --seed 42 --output outputs/_calibration_inputs/<new-input>
# A/B 真实运行：A 用 `git archive 072e38c src` 导出的源码目录，B 用 worktree src；受控对比见 outputs/experiments/logs/n4-iso-ab-20261028.sh
outputs/experiments/logs/n4-run.sh <input> <run-id> <gpu> <src-dir>
# N3 拒绝状态（查询格式 run:scene_id:station_id）
$PY scripts/experiments/n3_rejection_states.py --query <run>:<scene>:S0022 --gpu <gpu> \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output <new.json>
# 12 站几何
CUDA_VISIBLE_DEVICES=<gpu> $PY scripts/experiments/p2_collision_diagnosis.py \
  outputs/experiments/diagnostics/p2-geometry-20261027/selection.json --output <new.json>
# 分层汇总与耗时
$PY scripts/experiments/n4_ab_summary.py --pairs <label>:<A_run>:<B_run> ... \
  --geometry-a outputs/experiments/diagnostics/p2-geometry-n2-20261027/initial-states.json \
  --geometry-b outputs/experiments/diagnostics/n4-geometry-b51-20261028/initial-states.json --output <new.json>
```

A 源码导出在 `outputs/experiments/n4-chassis/src-head-072e38c/src`，其包根解析相对路径时以导出目录为准，所以诊断脚本读取输入一律用绝对路径。日志在 `outputs/logs/n4*-20261027*.log`、`n4*-20261028*.log`、`n3-rejection-20261027-*.log`。第一次并发运行的 run（`n4a-*-20261027`、`n4b-*-20261027`，55 球）和 55 球单独复测（`*-val108-s0028-iso-20261027`）保留，汇总在 `outputs/experiments/diagnostics/n4-ab-20261027/ab_summary.json`；其中 55 球与 51 球的并集相同，几何结论不变。

## 11. N3/N4 结果判断与 N5 决策（2026-10-28）

### 11.1 核心问题解决到哪一步

**两个已确认的重要工程缺陷已有有效修复；“整体标定成功率异常低”尚不能结案。** P1 已消除跨任务目标错绑，Cup_30 队列末尾恢复到单独运行的 37/60。N4 已消除 val10/val12/val32 的初始底盘假碰撞，并在 val10 恢复 pre-close 3/3 可规划和实际任务成功。这些足以保留修复，但还不足以说明底盘假碰撞解释了原有失败池的大部分，或当前整体成功率已恢复。

- val12/val32 仍是 IK 失败；初始碰撞消失不等于所有目标都可达，也不能仅凭失败状态证明目标在物理上绝对不可达。
- val103 与 Cup_30 S0022 的实体间隙只有 4.2/1.4 mm；允许当前保守拒绝，不以救回这些边界点作为完成条件。S0022 另有已确认的柱体假碰撞，仍是局部欠账。
- val10 的两条 lift 失败、val108 的 `open_no_grip` 尚未归因。每站 3 trial 只能支持代表样本结论，不能证明整体成功率稳定。
- 块覆盖提供的是底盘几何覆盖依据；21 条轨迹的零环境接触仅覆盖记录的 20 Hz 帧，不能升级为连续时间无碰撞结论。

### 11.2 接受耗时，进入有限规模验证

**接受当前 51 球作为 N5 基线；暂不减球、不替换拟合方法、不增加活动预算。** 理由是几何修正已有直接收益，受控运行尚未显示原成功对照的任务退化。当前耗时代价值得进入固定 12 站验证，但这不代表全量吞吐或长 Open 的预算稳定性已经验收。

第 10.5 节五个可比查询的比值中位数为 1.14，其中包含 Pick 和 Open 站位，因此应称“这五个可比查询中位慢 14%”，不外推为全部 Pick 的性能。val108 最高预算占用 89%，按 300 s 计算只剩约 33 s；当前三条未超时不能排除重试变化带来的超时。

### 11.3 下一步及结束条件

1. 补齐固定 12 站、每站 3 trial；版本、输入、seed、预算一致的现有 51 球结果直接复用，不重复跑齐全部。使用独占 GPU、单 worker 的受控设置，保留原成功判据和完整分母；分别统计规划通过、任务成功、预算超时、错误/未知及耗时。N5 是代表集验证，之后的小批次才检验更广任务的恢复程度。
2. 优先用已有记录定位 val10 lift：检查抓取后状态、携带物球、起点/目标/轨迹中间的具体拒绝约束；尚无证据时不归因于底盘球。随后补 val104 的 IK 对照及 val108 `open_no_grip`。柱体问题保留定向处理，但仅修柱体并不能消除 S0022 的底盘近接触拒绝，不作为 N5 前置条件。
3. 如果受控运行出现 B 因预算失败、同条件 A 可完成的实际退化，先定位该查询的计算耗时与重试次数，再决定球数优化；暂缓扩大任务批次。若尝试 40 mm 外扩候选，必须复查已救回站位，尤其实体间隙仅 37 mm 的 val12；不能只凭球数更少接受。机械臂 link 如需重拟合，沿用官方方法。
4. N5 完成后以完整计划集合报告收益和残余失败：原成功对照没有未解释退化、误拒绝修复保持有效、长 Open 的预算失败得到明确处理后，再进入 8–12 任务小批次。真实不可达或合理边界拒绝不要求变为成功；仍需由实际结果判断异常低成功率是否消除。

本次只复核交付报告与方案、记录判断并更新下一步状态；未重新运行实验、未修改代码或球配置、未提交现有改动。第 9 节保持不变。

## 12. N5 实施结果：固定 12 站与 val10 lift 定位（2026-10-28）

**固定 12 站的受控 A/B 已补齐：51 球下 pre-close 规划并执行 15/36（原球 12/36），任务成功 7/36（原球 4/36），两边都没有预算超时或错误。定位 val10 lift 失败时发现一个比底盘球影响更大的问题：planner 把整屋环境合并成单个网格，cuRobo/Warp 判断内外时会把成片自由空间判成障碍内部，并且结果随坐标系翻转。val10 剩下的两条 lift 失败和 val12 的失败都由此造成。** 修复需要改环境的碰撞表示，会影响所有规划和耗时，本轮未改生产代码，交由用户决定（12.5）。

### 12.1 固定 12 站（第 11.3 节第 1 项）

复用第 10.4 节 6 站的 51 球受控结果（同版本、输入、seed、预算）；其余 6 站（val100/101/102/104/107/105）按同样方式补跑：A（HEAD 原球）与 B51 同时运行，各独占一张 GPU，单 worker，种子同原 run。run 为 `n4a-<站>-iso-20261028` / `n4b51-<站>-iso-20261028`，汇总 `outputs/experiments/diagnostics/n5-fixed12-20261028/ab_summary.json`（逐 trial 分层、耗时、接触回放）。

| 站位（组） | pre-close 规划并执行 A → B | 任务成功 A → B | B 的失败位置 |
|---|---|---|---|
| val10（goalset 底座） | 0 → 3 | 0 → 1 | 2 条 lift 规划（12.2：SDF 符号错误） |
| val100（goalset 底座） | 0 → 0 | 0 → 0 | goalset |
| val101（goalset 底座） | 0 → 0 | 0 → 0 | goalset |
| val102（goalset 底座，Open） | 0 → 0 | 0 → 0 | goalset |
| val103（goalset 底座，Open，4.2 mm 边界） | 0 → 0 | 0 → 0 | goalset TrajOpt |
| val104（goalset 底座，Open） | 0 → 0 | 0 → 0 | goalset IK（12.3） |
| val134（原成功） | 3 → 3 | 2 → 3 | — |
| val108（原成功，Open） | 3 → 3 | 2 → 2 | 1 条 `open_no_grip` |
| val12（低目标） | 0 → 0 | 0 → 0 | goalset IK，起点被判碰撞（12.3：SDF 符号错误） |
| val32（低目标，Open） | 0 → 0 | 0 → 0 | goalset IK |
| val107（pregrasp 跟踪超时） | 3 → 3 | 0 → 1 | 1 条 lift IK 无解、1 条跟踪超时 |
| val105（pregrasp 跟踪超时） | 3 → 3 | 0 → 0 | 3 条跟踪超时（同 A） |
| **合计 36 trial** | **12 → 15** | **4 → 7** | 预算超时 0 → 0，错误/未知 0 → 0 |

- Cup_30 S0022/S0001 对照不计入 12 站分母：A/B 均为 S0022 0/3、S0001 3/3。
- 回放中出现机器人–环境接触的只有 val107 seed 46：A、B 都是 `link5` 碰到窗户 `collision_5`（A 123 帧、B 240 帧，最大穿透 0.4/0.2 mm），随后跟踪超时。这是机械臂而非底盘，两边相同，与底盘改动无关；是规划漏判（扫描中 val107 有“误判为外部”的点，见 12.4）还是 link5 球覆盖不足，未定位。回放只检查 20 Hz 记录帧。
- 每站 3 trial；4/36 与 7/36 的差距来自 val10/val134/val107 各 1 条，样本量不足以给出比例估计。

### 12.2 val10 lift 的拒绝状态（第 11.3 节第 2 项）

`n3_rejection_states.py` 新增 `--pose-rollout`：从该 trial 的 `trajectory.npz` 取 lift 查询时刻的完整积分状态与控制快照，从 `planning.json` 取记录的 lift 目标（`goal_world`，抓取后 TCP 上移 0.10 m），在新建 planner 上重放同一闭爪查询（底盘锁定、携带物球按原方式拟合并挂接）。重放结果与原运行一致：seed 42、43 失败，seed 44 成功。几何对照时恢复同一时刻的实测状态，并把 planner 实际使用的球表（含携带物球）拷入 FK。

| seed（查询步） | 拒绝位置 | 被拒的球 | planner 合并网格 SDF（规划系 / 同网格世界系） | 逐 geom 最近真实余量 |
|---|---|---|---|---|
| 42（第 366 步） | **起点**：IK 起点不可行，PRM 起点 0/4，TrajOpt 第 0–6 步 | `panda_finger1` 球 84 | −0.106 / +0.106 m | +8.9 cm（餐桌 `collision_1`） |
| 43（第 284 步） | **轨迹中间**：起点、目标可行，插值第 11–46 步 | 携带物球 96 | −0.107 / +0.107 m | +9.2 cm（同一餐桌） |
| 44（成功对照） | — | — | — | — |

- 两条失败都是假碰撞：球离最近物体约 9 cm，但 planner 把这个位置判为在障碍内部约 10.6 cm。底盘球和携带物球的拟合方式都不是原因。
- seed 42 的后 4 次尝试只跑了 IK：单目标规划从第 2 次起先做 PRM 图种子，起点不可行时图种子返回空即 `continue`，TrajOpt 不再运行。
- 携带物球使用 VOXEL 拟合（申请 40 个，返回 22 个，cuRobo 覆盖率 5.7%），覆盖不足的问题仍存在，但与这两条失败无关。

### 12.3 同一问题在其他站位的直接证据

- **val12 S0007**（原归为“低目标不可达”）：新球下起点状态被判 `scene_collision`，涉及 link7、手掌、手指共 10 个球（球 79–88）。planner 合并网格判它们在障碍内部 34–49 cm；同一网格在世界系下判在外部；逐 geom 计算，离最近物体（餐桌）53–62 cm。起点不可行意味着这条查询不可能成功，所以第 10 节“val12 受可达性限制”的归因不成立。
- **val104 S0000**（Open）：5 次 IK 尝试都没有可行目标；全部 60 个返回目标中，违反 scene_collision 的 50 个、handle_contact 35 个、cspace 59 个。违反最少的目标是手指球侵入抽屉柜 1.4 mm（实体手指距 5.2 mm），属于把手抓取的真实近接触。其余目标中有多少落在误判区，本轮没有逐个核对。

### 12.4 误判的影响面（`n5_sdf_sign_scan.py`，`outputs/experiments/diagnostics/n5-sdf-sign-20261028/scan.json`）

每站在规划系原点周围 1.5 m、高 0–1.6 m 的圆柱内采样 20,000 点，只统计离表面 0.3 m 以内的点。参照为逐 geom 单独建网格后取 SDF 最小值。

| 站位 | 表面附近点 | 误判为内部（假碰撞） | 误判为外部（漏碰撞） | 规划系/世界系符号不一致 |
|---|---:|---:|---:|---:|
| val12 | 5733 | 1627（28%） | 0 | 1635 |
| val104 | 6231 | 1633（26%） | 69 | 1986 |
| val100 | 6916 | 1445（21%） | 267 | 2919 |
| val101 | 8100 | 300（3.7%） | 139 | 418 |
| val102 | 10276 | 146（1.4%） | 100 | 290 |
| val103 | 7940 | 92（1.2%） | 37 | 580 |
| val10 | 5915 | 29（0.5%） | 15 | 62 |
| val134 / val108 / val32 / val107 / val105 | 6316–8156 | 0 | 14–575 | 44–176 |

- **独立核对**：val104、val12、val100、val10 的全部“误判为内部”点（1633/1627/1445/29）都不在任何 geom 的凸包内（MuJoCo 物理按凸包碰撞），即物理上是自由空间；planner 判其在内部的深度中位 18–19 cm（val10 为 12.5 cm）。
- **机理**：`collision_world` 把范围内所有环境 geom（472–1791 个）拼成一个 cuRobo mesh。cuRobo 0.8.0 在 `data_mesh.py` 中用 `wp.mesh_query_point` 求 SDF，其符号由沿坐标轴的射线奇偶决定；合并网格含不封闭或互相重叠的部件时，奇偶随射线方向变化。val10 上同一合并网格在世界系为 +0.1058、仅做刚体变换到规划系即变为 −0.1058，查询范围填充（0 或 5 m）不影响结果。
- “误判为外部”的点未用凸包逐一核对；参照本身也是逐 geom 的射线判定，单个网格不封闭时可能出错，所以这一列只作量级参考。

### 12.5 待决定：环境碰撞表示的修复

误判为内部会直接产生起点、目标或轨迹中间的假碰撞，误判为外部可能放过真实碰撞，影响所有站位，与底盘球无关。候选修复是把 `collision_world` 改为每个 geom 一个 cuRobo mesh（单个 geom 网格基本封闭，符号只取决于自身）：

- 查询耗时：同样 20,000 个球心，单合并网格 0.044–0.052 s，每 geom 一个 mesh 为 0.049–0.101 s（geom 越多越慢，val12 的 1791 个 geom 约 2.2 倍）。只测了碰撞查询本身，完整规划耗时和每次查询重建上千个 Warp mesh 的开销未测。
- 需要同步：checker 的 mesh 缓存数量、每个 mesh 的查询范围填充（不能为 0，否则远离自身包围盒的球会被跳过）、诊断脚本的导出规则。
- 验证建议：先用本节扫描确认误判点消失，再在固定 12 站做与第 10.4 节相同的受控 A/B（“51 球 + 合并网格” vs “51 球 + 每 geom 一个 mesh”），同时报告耗时与 Open 预算占用。

这会给 N5 基线引入新变量，所以按第 11 节“不同时调整多个变量”的原则，本轮只取证、不改生产代码。

### 12.6 未做与限制

- 第 11.3 节第 4 项中，val108 `open_no_grip` 与柱体问题未处理；val104 只做到 IK 目标的约束统计。
- 12.4 只覆盖 12 站初始状态附近的空间采样，不等于全库比例；运行时状态（如 lift）的误判在 12.2 中逐条核实。
- 长 Open 预算：本轮固定 12 站没有超时；val108 最高占用 89%（A 81%），其余站位最高 36%。没有出现 A 能完成、B 因预算失败的情况。
- 新增/修改的诊断脚本：`n3_rejection_states.py`（`--pose-rollout`、IK 目标代表状态、合并网格与逐 geom SDF 对照）、`n5_sdf_sign_scan.py`（新）、`p2_collision_diagnosis.py`（`load_station` 改按 run 配置的 `dataset` 定位输入目录）。离线回归 `tests/planning` 72/72。代码未提交。

复现：

```bash
# 固定 12 站补跑（成对受控）
outputs/experiments/logs/n5-iso-ab-20261028.sh
# lift 重放（rollout 取自对应 run）
$PY scripts/experiments/n3_rejection_states.py --query n4b51-val10-s0000-iso-20261028:<scene_id>:S0000 \
  --pose-rollout r000000 --gpu <gpu> --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output <new.json>
# SDF 符号扫描
CUDA_VISIBLE_DEVICES=<gpu> $PY scripts/experiments/n5_sdf_sign_scan.py \
  outputs/experiments/diagnostics/p2-geometry-20261027/selection.json --output <new.json>
```

结果：`outputs/experiments/diagnostics/n5-lift-20261028/r2/`、`n5-rejection-20261028/`（val104、val12）、`n5-sdf-sign-20261028/`、`n5-fixed12-20261028/`；日志 `outputs/logs/n5-*.log`。

## 13. 环境 SDF 符号错误的修复（2026-10-28）

**修复方式：mesh 类型 geom 改为导出其凸包（MuJoCo 物理本来就按凸包碰撞），仍合并成一个网格。修复后 12 站的“误判为内部”为 0，val10 两条 lift 重放和 val12 起点都恢复可行；固定 12 站受控运行中，规划并执行 15 → 17/36，任务成功 7 → 8/36（val100 0 → 2/3），无预算超时，规划耗时不变。** 第 12.5 节提出的“每个 geom 一个 mesh”先实现过，因耗时不可接受已撤回（13.2）。

### 13.1 根因与改动

- Warp `mesh_query_point` 的文档写明：内外判断依赖额外的射线，**网格必须封闭才可靠**。12 站导出的环境 geom 中有 27%–72% 是不封闭的原始 mesh（如 val12 为 1288/1791），合并后奇偶计数失效，自由空间被判为障碍内部，并随坐标系变化。
- `planning/curobo/collision_world.py::exported_geoms`：mesh geom 改为 `trimesh.convex.convex_hull` 的封闭凸包，primitive 仍按原方式网格化；`collision_world` 仍合并成一个网格。planner、缓存、查询范围填充均不变。
- 与诊断共用同一导出规则：`p2_collision_diagnosis.py`、`n3_rejection_states.py`、`n5_sdf_sign_scan.py` 的规划侧几何随之变为凸包。
- 新增测试 `tests/planning/test_curobo.py::CollisionWorldTests`：不封闭的 L 形 mesh 导出为封闭、绕向一致的凸包（缺口被填平），primitive 保持封闭，目标不进入环境世界。`planning/README.md` 同步说明。离线回归：`tests/planning` 73/73，全量 397 条通过（跳过 2 条）。

### 13.2 撤回的方案：每个 geom 一个 mesh

先按第 12.5 节实现了“每个 geom 一个 cuRobo mesh”，附带缓存容量与跨站复用重建、所有 checker 的查询范围填充、携带物拟合合并。符号扫描显示它对逐 geom 参照的假碰撞和漏碰撞都是 0，lift 重放也恢复成功；但真实运行中规划耗时急剧上升：val108 pre-close 278 s（原 25 s）、val105 245 s（原 28 s）、Cup_30 S0022 97 s（原 17 s），val108 与 val105 全部 `attempt_active_budget` 超时。原因是 v0.8.0 的内核按“球数 × mesh 数”展开线程，上千个 mesh 乘以 IK/TrajOpt 的全部 seed 与时间步。该运行已中止，相关代码已恢复到 HEAD，部分结果保留在 `outputs/n5c-*-iso-20261028`。

### 13.3 几何验证（`outputs/experiments/diagnostics/n5-hull-20261028/`）

符号扫描（同第 12.4 节的采样点与口径；参照改为逐个凸包取最小 SDF）：

| 站位 | 误判为内部：修复前 → 后 | 误判为外部：修复前 → 后 | 规划系/世界系符号不一致：修复前 → 后 |
|---|---|---|---|
| val12 | 1627 → 0 | 0 → 0 | 1635 → 0 |
| val104 | 1633 → 0 | 69 → 23 | 1986 → 16 |
| val100 | 1445 → 0 | 267 → 12 | 2919 → 30 |
| val101 | 300 → 0 | 139 → 145 | 418 → 106 |
| val102 | 146 → 0 | 100 → 139 | 290 → 121 |
| val103 | 92 → 0 | 37 → 46 | 580 → 53 |
| val10 | 29 → 0 | 15 → 18 | 62 → 32 |
| 其余 5 站 | 0 → 0 | 14–575 → 10–451 | 44–176 → 19–135 |

- 修复后仍有的“误判为外部”位于凸包互相重叠处（射线在那里穿越两次）。这类点在家具实体内部，机器人从外部接近时先触及外表面；修复前同类误判量级相同。
- 凸包比原始网格多出的部分：12 站各自在表面附近的采样点中有 0–47 个“在凸包内但在原始网格外”，大多在 2.6 cm 以内；val32、val107 有个别凹形部件被填平约 20 cm（例如 L 形墙的内角）。这与 MuJoCo 物理一致，但会让规划比原始网格更保守。
- val10 lift 重放（第 12.2 节同一状态与目标）：seed 42、43 都在第一次尝试即规划成功，起点可行，TrajOpt 全部 seed 可行。
- val12：起点由不可行变为可行；5 次 IK 尝试中仅 1 次有可行目标，仍规划无解。这是去掉假碰撞后暴露出的独立问题，尚未归因。

### 13.4 固定 12 站受控运行（H：51 球 + 凸包网格）

运行方式同第 12.1 节（两张卡各一个队列，每个 run 独占一张 GPU，单 worker，种子同原 run），run 为 `n5h-<站>-iso-20261028`；对照复用 `n4b51-*-iso-20261028`（同版本，只差本修复）。汇总 `outputs/experiments/diagnostics/n5-hull-fixed12-20261028/ab_summary.json`。

| 站位 | 规划并执行 B51 → H | 任务成功 B51 → H | H 的失败位置 |
|---|---|---|---|
| val10 | 3 → 3 | 1 → 1 | seed 42 lift 已规划执行，跟踪超时；seed 43 lift TrajOpt 无解（执行到第 266 步，状态不同于 B51，第 13.3 节的重放不适用） |
| val100 | 0 → **3** | 0 → **2** | 1 条跟踪超时 |
| val101 / val102 / val103 / val104 | 0 → 0 | 0 → 0 | goalset |
| val134 | 3 → 3 | 3 → 3 | — |
| val108（Open） | 3 → 3 | 2 → 2 | 1 条 `open_no_grip`（同 B51） |
| val12 / val32 | 0 → 0 | 0 → 0 | goalset IK |
| val107 | 3 → 2 | 1 → 0 | seed 47 grasp 阶段 TrajOpt 无解（见下）；seed 45 lift IK、seed 46 跟踪超时同 B51 |
| val105 | 3 → 3 | 0 → 0 | 3 条跟踪超时（同 B51） |
| **合计 36 trial** | **15 → 17** | **7 → 8** | 预算超时 0 → 0 |

- Cup_30 对照：S0022 0/3、S0001 3/3，与 B51 相同。
- **耗时**：同等工作量的查询 H/B51 为 0.95–1.09；val108 最高预算占用 89% → 87%。
- **接触回放**：仅 val107 seed 46 的 `link5`–窗户接触（两组相同，与本修复无关）。
- **val107 seed 47 的退化已核实来自凸包**：在同一冻结 pre-close 输入上，用 B51 代码（HEAD 源码 + 51 球，`outputs/experiments/n5-hull/src-b51/src`）重放成功（抓取候选 7），用凸包代码重放失败；失败的 seed 混合了中段 cspace 与 scene_collision。目标附近被凸包明显填平的是两段 L 形墙（凸包体积比原网格多约 15%），它们在 MuJoCo 中与机器人碰撞。B51 那条轨迹物理执行时没有接触，说明凸包世界中仍存在可行路径，只是规划在新几何下没找到。只有 1 条 lane，未继续追查。（`n3_rejection_states.py` 对 cspace 项仍按“球”标注下标，cspace 的最后一维实际是关节，解读时以项名为准。）

### 13.5 结论与限制

- 修复消除了已证实的“自由空间被判为障碍内部”这一大面积假碰撞来源，没有增加规划耗时，并让规划几何与 MuJoCo 物理一致。
- 收益目前体现在 val100 恢复和 val10 lift 规划恢复上；val12/val101–104 仍在 goalset 失败，需要在符号正确的前提下重新归因。固定 12 站每站 3 trial，8/36 不是成功率估计。
- 凸包让凹形部件变成实体，会让少数规划更保守（val107 seed 47）；凸包重叠处的“误判为外部”仍存在。
- 第 12 节 12.4/12.5 中关于“每个 geom 一个 mesh”的建议已被本节取代；12 节的其他证据仍有效。代码未提交。

复现：

```bash
# 符号扫描（修复后）
CUDA_VISIBLE_DEVICES=<gpu> $PY scripts/experiments/n5_sdf_sign_scan.py \
  outputs/experiments/diagnostics/p2-geometry-20261027/selection.json --output <new.json>
# 固定 12 站 + Cup_30 受控运行
outputs/experiments/logs/n5h-fixed12-20261028.sh
# B51 对照代码重放（HEAD 源码 + 51 球）
PYTHONPATH=$PWD/outputs/experiments/n5-hull/src-b51/src:$PWD/scripts/experiments $PY scripts/experiments/n3_rejection_states.py \
  --query n5h-val107-s0004-iso-20261028:<scene_id>:S0004 --gpu <gpu> --assets-dir <assets> --output <new.json>
```

## 14. 凸包修复复核与下一步（2026-10-28）

### 14.1 决定：保留局部修复，暂不扩大批次

保留“51 球 + 每 geom 凸包后合并”的当前版本，撤回逐 geom 独立 checker 的方向：实际规划增加 6–11 倍且造成预算失败，已有足够证据，不再重复该方案。当前版本消除了已采样点的假内部误判，恢复 val10 旧 lift 查询和 val100 实际成功，且未观察到同工作量的明显耗时增长。

但不将它表述为“环境 SDF 已全面正确”或“低成功率已解决”。已知重叠区域假外部、val107 seed 47 退化及 seed 46 真实接触都还在。下一轮先做下述定向诊断，不重复整套 36 trial，也不增加预算或同时改机器人球。

### 14.2 需要纠正的解释

- **本地实现不是射线交点奇偶统计。** cuRobo 的 `data_mesh.py` 调用 `wp.mesh_query_point`；当前安装的 `warp/native/mesh.h::mesh_query_inside_ray_tracing` 沿三个正坐标轴，依据每条射线最近命中面的朝向投票。独立的 `mesh_query_point_sign_parity` 才统计交点奇偶。第 12 节及此前讨论的“穿越两次抵消”不能作为本版本的具体解释；开放或重叠网格破坏正确内外判定这一实测结论仍成立。[Warp 官方说明](https://nvidia.github.io/warp/latest/language_reference/_generated/warp.mesh_query_point.html)
- **外表面存在不能证明内部漏判无害。** IK 候选、优化中间状态可以直接落入内部；有限离散检查也不是连续碰撞保证。应把误判点换成实际机器人有效球半径，读取生产 checker 的最终可行性；球心符号错误不一定导致该有限半径球漏判，但必须核实，不能仅凭点位于家具内部免责。
- **凸包填平凹处主要是纠正规划/物理表示不一致。** 若物理本来按这个 geom 的凸包碰撞，就不应为恢复旧成功率而重新开放凹处。val107 旧执行的记录帧无接触，不证明旧规划轨迹满足当前球覆盖、buffer、关节与接触约束；在这些检查之前，“可行路径还在，只是优化器没找到”仍是待验证解释。
- **val12 不能概括为所有 IK 目标无解。** 第 13.3 节记录了 5 次中有 1 次可行目标；应追踪这一候选为何最终没有轨迹。val10 旧状态重放已修复，但新 rollout 的 seed 43 状态不同，仍需重放新的 lift 请求。

### 14.3 定向工作顺序

1. **核实实际漏判，处理 val107 seed 46。** 复用已有假外部点及实际窗户接触帧，用物理凸包作独立参照，检查实际球半径下的生产 checker；核对 link5 球覆盖、规划参考轨迹和实测轨迹，区别环境符号、球覆盖与跟踪误差。只检查已有反例，不扩成全库扫描。
2. **若确认合并网格导致实际球漏判，先试官方 winding-number 查询的小原型。** 继续一个合并网格，保持各凸包向外绕向，用 `mesh_query_point_sign_winding_number`，构建 mesh 时启用 `support_winding_number=True`；这是待测候选，不承诺正确性或速度。先测已知重叠反例、刚体变换和少量冻结查询，再决定是否接入生产；同时看梯度与完整求解耗时。不能只换函数名，也不直接修改共享 `.venv`。官方指出该方法更昂贵，需实测。[Warp 官方接口](https://nvidia.github.io/warp/latest/language_reference/_generated/warp.mesh_query_point_sign_winding_number.html)
3. **核实 val107 seed 47 的旧成功路径。** 将旧规划 q 序列按相同底盘、夹爪状态、球表和目标放进新 checker，分别列出 scene_collision 和 cspace；cspace 下标按关节解释。若旧路点通过，再作为初值或参考候选定位求解退化；若不通过，指出具体球/墙/间隙，先解释表示或约束差异。不要仅凭附近 L 形墙的体积变化认定它就是拒绝源。
4. **再处理残余失败池。** 优先 val12 那次可行 IK 后的流程和新 val10 lift；随后 val101/102/104，按起点、IK 位姿误差、关节限制、环境碰撞、handle_contact、轨迹阶段拆分。val103 保留近接触边界预期。`open_no_grip`、柱体与携带物覆盖问题分别跟进；不通过删站位、放宽成功判据或预算制造提升。

只有实际漏判和已知接触得到解释处理、退化可归因后，再扩 8–12 任务。若新增修复影响公共碰撞查询，先跑相关局部测试和代表冻结请求，再按影响范围补固定站位真实对照。当前 8/36 是该固定代表集的观测值，可以报告，不能当作全任务总体成功率估计。

本轮复核第 13 节、生产导出代码与本地 Warp 实现，补充决策并更新方案；未运行新实验、修改生产代码或提交。第 9、11、13 节实验记录未改写。

## 15. 独立几何建图、ESDF 查询的方向评估（2026-10-28）

**建议优先做该方向的小原型。** 保留逐 geom 的封闭物理凸包，建图时组合实体占用，规划时只查询一张局部环境 ESDF；不是为上千个 geom 各建一张 ESDF 再逐个查询，也不是把当前合并 mesh 的错误符号烘焙进栅格。这样有望同时消除合并判内部的语义问题和每次查询遍历上千个 mesh 的开销，但建图时间、分辨率误差和动态更新代价尚未验证。

### 15.1 当前版本可用的官方入口

本地 cuRobo 0.8.0 提供 `curobo.perception.Mapper/MapperCfg`、`Mapper.update_static_obstacles(SceneData)`、`Mapper.compute_esdf()` 返回 `VoxelGrid`，以及支持 voxel 的 `SceneCollision`。官方示例见[volumetric_mapping](https://nvlabs.github.io/curobo/latest/api/curobo.examples.getting_started.volumetric_mapping.html)。已知仿真几何可以直接写入静态几何通道，不需要引入深度相机或感知误差。

本地实现的限制：静态 stamping 对独立障碍 SDF 取最小值，但仍遍历障碍，并只在截断带内写入；`update_static_obstacles` 清空静态通道后重新写全部障碍，不是廉价增量更新。voxel 查询使用三线性插值，越界可能返回正距离。因此必须验证重叠深内部、薄障碍与边界，不能只确认接口能调用。

### 15.2 最小原型边界

- 静态环境采用一个局部 ESDF，使用逐 geom 物理凸包建图。各分量内部距离为负时，取最小值能表达实体并集的占用；它在重叠内部的数值不一定等于并集边界的严格距离，最终 ESDF 的距离和梯度仍需验证。不能退回有重复面/不一致表面的原始 mesh。
- 目标、把手以及会运动的门/抽屉保留少量独立精细几何，按实际位姿更新；维持当前目标排除、手指接触掩码及携带物挂接语义。不能把会运动的部件永久写进静态图，导致打开后旧障碍残留。首次原型固定任务和站位，不先实现跨任务通用共享地图。
- 地图坐标系在一次运行内保持稳定，机器人查询点做坐标变换；不用每次重规划都随机器人重新建图。范围覆盖实际底盘/机械臂/携带物的规划空间及查询邻域，出界不当成已知自由空间。
- 先显式用 1 cm ESDF 做规模和耗时实验，再对误差敏感区域对比 5 mm；不要沿用 `MapperCfg.esdf_voxel_size=0.05` 的默认值。TSDF 分辨率与 ESDF 分辨率分别设置。当前只有 2 mm buffer，不能假定能覆盖厘米级离散误差；先量化表面和间隙误差，不先统一加大 buffer。
- 示例：4×4×2 m 范围，1 cm 约 3200 万 voxel，单份 FP16 距离约 64 MB；5 mm 约 2.56 亿 voxel、512 MB。该估算不含 TSDF、距离变换中间数组、缓存和多 worker 副本，实际峰值显存需测。

### 15.3 最小验证及采用条件

1. 用已有假内部/假外部点、重叠凸包深内部、薄墙和地图边缘，比较物理几何与 ESDF 的符号、距离、梯度和实际球可行性。至少覆盖 val12（1791 geom）、val107（退化与接触）及 val108（长 Open）；必要时加入 val10 冻结 lift 查询。
2. 重放相同规划请求，分别计首次几何装载、stamping、ESDF 生成、复用查询和动态部件更新。官方接口只说明实现路径，不证明本场景能实时建图；构图成本必须计入任务活动预算或明确的原有初始化阶段，不能从总成本中消失。
3. 比较 `建图成本 + 更新次数×更新成本 + 查询次数×ESDF 查询成本` 与当前合并凸包的对应总成本。若收益只在静态 Pick 出现，而 Open 更新频繁，则先保留静态 ESDF 与少量动态 mesh 的混合形式，不强行整屋每步重建。
4. 只有几何误差可接受、已知漏判得到处理、真实任务没有新增碰撞/预算退化，才进入固定站位对照并决定替换生产。原型独立运行，已有实验结果仍按其实际碰撞表示解释。

本轮只检查本地官方接口和实现、评估方案并更新文档；未构建 ESDF、运行性能实验或更改生产碰撞表示。用户已有 `05-repair-summary.md` 修改保留。

### 15.4 CoACD 是否适用（2026-10-28）

**能用于非凸资产的离线碰撞建模，但不是当前 ESDF 路线的前置条件，也不能直接替代环境查询修复。** CoACD 用碰撞相关凹度、平面切割和多步树搜索，将实体近似为多个凸块，适合保留把手孔洞、凹槽与 L 形内角。依据：[论文全文](https://arxiv.org/html/2205.02961v1)、[官方实现](https://github.com/SarahWeiii/CoACD)。用户提供的 NSF 地址本次返回 502，改查同一论文的 arXiv 全文；未安装或运行 CoACD。

- 当前已有碰撞件多为接近凸块的资产，第 05 号摘要第 6 节的抽查支持这一点；先提取干净凸包即可，不全库重做凸分解。这个抽样不等于每个资产均已证明凸。
- 若只在 planner 中对原始 L 形 geom 做 CoACD、保留内角，但 MuJoCo 仍使用原 geom 的单凸包，就产生规划/物理不一致。要保留凹处，应在新的派生场景中将每个凸块作为同一 body 下独立 mesh geom，同时给 planner 使用；不得把所有块重新作为一个 MuJoCo mesh geom，否则又会整体凸化。保持原场景只读，这属于物理资产变更的独立实验。
- CoACD 解决“如何表示形状”，ESDF 解决“如何高频查询场景”。推荐当前先沿用逐 geom 物理凸包验证 ESDF；未来确有凹形资产需求时，CoACD 输出的凸块可作为 ESDF 建图输入。它不自动降低已有上千 geom 的逐次查询开销，也不自动解决不同物体之间的重叠。
- 论文的无交叠性质针对单个实体的切分构造，不能外推为全场景独立处理后无重叠；共享切割面也不等于实体并集的外边界，不能据此直接恢复大 mesh 拼接判符号。
- 原型只选已确认的 L 形墙或把手。三角网格输入存在非流形问题时保留官方预处理；记录固定 seed、块数、处理时间、凹槽尺寸与新增/缺失体积。官方当前支持 `real_metric=True` 的米制阈值；默认阈值不是米。阈值不是整个“预处理→分解→简化”链路的无条件逐点误差保证，仍需核对实际间隙。
- 如做该资产实验，先不强制限制最大凸块数，不开启额外挤出，以免引入新的外扩/重叠；官方说明最大块数限制可能迫使结果超过凹度阈值。按离线步骤保存结果，不在每次规划中运行分解。验证新的物理接触和规划表示一致后，再评估任务收益，不能把物理变更与当前纯 planner 修复混为同一 A/B。

本次仅补充算法适用性判断，不改变 ESDF 原型优先级，未修改生产代码、资产或用户其他报告改动。

### 15.5 原 RBY-1、当前方案、ESDF 与 CoACD 对比（2026-10-28）

本表比较环境表示；若后续移植做 A/B，应固定机器人球、任务、预算和求解参数。CoACD 是几何预处理，必须另配查询后端；这里指离线凸分解并在物理与规划侧同步使用。

**“原 RBY-1”明确指本地上游 MolmoSpaces 的旧 `MotionGen/WorldConfig` 路线。** 它的 `CuroboPlanner.update_world()` 为每个传入场景对象创建 `Cuboid(pose=object.pose, dims=object.aabb_size*2)`，`scene/table` 另有固定尺寸特例；服务端 `_apply_world()` 同样构建 `WorldConfig(cuboid=...)`。虽然配置选择 `CollisionCheckerType.MESH`，实际场景输入仍是 cuboid，不能据枚举名认定它在查询环境三角网格。旧版 mesh checker 包含 primitive checker，见[官方碰撞表示说明](https://nvlabs.github.io/curobo/v0.7.6/get_started/2c_world_collision.html)。

**本仓库当前的 RBY-1 已不同于上述原方案。** `runtime/control/robot.py::planning_model()` 选择 RBY 模型，`planning/pool.py` 使用统一 `V2Planner`，再走同一个 `collision_world`。因此在当前 V2 中，RBY-1 与 PandaOmron 的差别主要在本体模型与运动自由度，环境都使用当前凸包拼接导出，不能拿机器人名字当作两种查询后端。

| 维度 | 原 RBY-1：对象级独立长方体 | 当前：逐 geom 凸包后拼接 mesh | ESDF：独立物理几何建图、局部距离场查询 | CoACD：非凸资产分解为凸块 |
|---|---|---|---|---|
| 主要目的 | 用简单包围盒近似环境 | 清理网格表面并减少假碰撞 | 正确组合占用，摊销重复查询 | 保留孔洞、凹槽与内角 |
| 查询方式 | 球对独立 cuboid 的专用查询 | 单大 mesh 的 BVH 距离/内外查询 | 栅格插值距离和梯度，少量动态几何另查 | 本身不查询，需 mesh/凸体/ESDF 后端 |
| 网格符号问题 | 不做环境三角网格射线判内部，避开该类错误 | 重叠区域已有内部漏判 | 并集构建正确时可处理，深内部/薄障碍仍需验证 | 不自动解决跨物体重叠或后端符号错误 |
| 几何细节 | 对象/部件级盒子可能填掉桌下、孔洞等空隙 | 保留不同 geom 之间空隙，单 geom 凹处填平 | 保留输入形状的近似，另有栅格误差 | 可更好保留凹形细节，仍有分解/预处理误差 |
| 物理一致性 | 包围盒不是实际物理表面；中心/尺寸/对象分组需正确 | mesh 形状向物理凸包对齐，checker 仍有误差 | 沿用物理形状，增加离散误差 | 改形状须同步 MuJoCo 与 planner |
| 速度 | 单次 primitive 查询通常便宜；无同条件端到端对照 | 已测同工作量为旧 51 球版本的 0.95–1.09 倍 | 预期复用查询更便宜，建图和总成本未测 | 本项目未测，块数与后端决定代价 |
| 动态更新 | 已有按障碍名字更新 pose、enable 的接口 | 当前重导出流程 | 推荐静态 ESDF + 少量动态精细几何 | 凸块随 body 运动，后端同步位姿 |
| 显存/准备代价 | pose、尺寸及对象缓存；无需环境网格 BVH | 网格与 BVH | 新增地图及中间数组，分辨率加倍时 dense voxel 数约增 8 倍 | 离线分解和凸块资产，运行成本依后端 |
| 对当前工程的改动 | 可移植 cuboid 表示，但会降低环境细节，不能直接恢复旧 API | 已实施，最小 | 中等：建图/缓存/动态层/精度 | 同步物理时较大：派生资产和接触验证 |
| 项目证据 | 已核对上游源码，未与当前 12 站做公平对照 | 17/36 规划执行、8/36 成功，仍有接触/退化 | 尚未实验 | 尚未实验 |

原 RBY 代码依据（只读）：`/data1/wenyifan/lmdf/molmospaces/molmo_spaces/planner/curobo_planner.py:409`、`curobo_planner_server.py:150`、`env/data_views.py:279`。`aabb_size` 来自所选 body BVH 的半尺寸，不等于已核实的整件家具精确几何；此轮没有验证所有对象中心、关节运动覆盖或包围盒保守性。原代码的机器人构型、旧 API 与求解参数也不同，不能直接推导成功率或速度倍数。

结论：借鉴原 RBY 的解析 primitive 查询用于真正的 box geom，不能为了速度把全部家具退化成对象级大盒子。当前凸包方案保留为已测基线，优先验证 ESDF；CoACD 用于确有需求的局部资产变更。可组合为“精确 primitive / 独立物理凸包 → 静态 ESDF + 少量动态精细几何”。此前独立 mesh 的 6–11 倍耗时既不是原 RBY cuboid 的结果，也不是 CoACD 的结果。

本次读取上游原 RBY 路线、本仓库 RBY 模型与统一 V2 调用链，并扩充此表；未导入外部工程、运行新实验或改动生产配置。

## 16. 面向局部站位标定的性能预估（2026-10-28）

### 16.1 应用口径与估算前提

用户明确关注机器人、目标、支撑面及机器人/目标附近物体，用于判断各站位的操作成功率。用户随后澄清：提供应用场景是要求权衡物理精度与运行性能，不是要求重新定义成功率或评价流程。因此本节只比较碰撞表示的精度与代价，不新增成功判据，也不把规划器完成动作擅自改为另一种统计口径。

四种环境处理都移植到同一 PandaOmron/cuRobo V2/51 球条件比较，不直接比较旧 RBY 机器人与当前机器人。局部范围覆盖完整动作中底盘、双臂/躯干与携带物可能经过的区域，不能只按目标距离选最近几个物体。目标/支撑面的必要接触与非允许碰撞分别处理，不能通过删除支撑面提高通过率。

以下是方案选择用的假设估算，非实测结果或置信区间。假设只保留局部操作区域、GPU 独占、已预热、求解轮数相同；局部场景暂按 10–30 个对象、几十至约百个碰撞部件作规模假设，实际筛选数量未测。固定同等求解工作量后，假设环境碰撞查询占当前规划时间的 40%–70%；现有日志没有 profiler 数据证明该占比。

令当前规划时间为 P，碰撞部分占比为 f，新查询成本比为 q，则热态规划耗时比约为 `(1-f)+f*q`。加速只作用于这一部分，不能把单次碰撞查询加速倍数当作整条规划或真实执行的加速倍数。依据仅限：官方说明 cuboid 专用查询较廉价、ESDF 提供定长插值查询；当前合并凸包实测稳定；此前上千独立 mesh 实测严重变慢。

### 16.2 四方案数字对比

| 方案与明确实现 | 假设碰撞查询成本 q（当前=1） | 同工作量热态规划耗时（当前=1） | 假设当前规划需要 20 s 时 | 额外成本/适用限制 |
|---|---:|---:|---:|---|
| 原 RBY 思路：局部对象级 cuboid，迁入同一 V2 | 0.25–0.5 | 约 0.5–0.8 | 约 10–16 s | 不含首次配置；盒子可能吞掉操作空隙，不能把提前误拒绝当作加速 |
| 当前：逐 geom 凸包拼成单 mesh | 1.0 | 1.0 | 20 s（仅换算基准） | 保留现有建图/更新成本；有已知重叠内部漏判 |
| ESDF：一张局部静态图 + 少量动态精细几何 | 0.1–0.3 | 约 0.4–0.75 | 约 8–15 s，另加建图/更新摊销 | 以地图可复用且精度足够为前提；局部 geom 极少时优势可能消失 |
| CoACD：离线分解后，每凸块独立 mesh 查询 | 1.5–4 | 约 1.2–3.1 | 约 24–62 s | 低置信度敏感性估算，块数/三角形数决定实际结果；分解成本另计 |

这些 q 是显式建模假设，不是对三个未运行方案的性能测量。官方旧版文档的 cuboid 查询速度提示不能作为本项目固定倍数；ESDF O(1) 查询也不能证明总耗时更短。若 CoACD 输出用于 ESDF，运行时归入 ESDF 行，另计离线分解和新几何引起的求解变化，不存在独立的“CoACD 查询速度”。四种方案的完整任务耗时排序也可能随重试和执行阶段改变。

### 16.3 建图摊销与真实运行时间

对同目标/同静态场景的 K 条完整 station trial，比较 `总准备/建图 + 所有更新 + 所有规划 + 所有物理执行`。ESDF 每条摊销为 `G/K`，仅当几何、目标排除集合与地图范围一致时共享；每条 trial 的物理状态仍按既有独立仿真实验语义运行。

例如，假定一次地图构建 G=30 s（仅用于算账，未实测）：复用 60 条时每条 0.5 s，只用 3 条时每条 10 s。以上 20 s 基准下，ESDF 对应约 8.5–15.5 s 或 18–25 s，加上必要动态更新。若每条重建就是 38–45 s，可能比当前更慢；这说明先实现可靠复用比盲目追求更细分辨率更重要。

若规划只占完整任务时间的 40%，即使规划减至 0.4–0.75 倍，任务总时间也只是约 0.76–0.90 倍，尚未计建图。进入真实执行的 trial 增多还会降低表观 trial/min，不能据此判定修复无价值。吞吐应同时报告完整 trial/h、规划链成功数/h、操作成功数/h与完整计划分母上的成功比例。

### 16.4 对站位成功率判断的影响与建议

- 原 RBY 的对象大盒子偏粗，容易把本可抓取/绕行的空间判成障碍，导致站位成功率偏低。若只用它筛掉站位，最终标定会有选择偏差；可保留作低成本对照，不直接用其失败删除完整评估集合。
- 当前方案几何比对象盒子细，但内部漏判可能导致规划通过虚高；真实执行检查仍有价值。8/36 等固定代表集结果不用于推算其他方案会提升多少百分点。
- ESDF 最符合“同一个目标周围评很多站位、重复查询大量次”的摊销模式。推荐支撑面若本来就是 box 则保持精确 primitive，复杂静态邻近障碍建 ESDF，目标/把手/动态部件保持少量精细几何。1 cm 只作性能原型，5 mm 对照近接触误差；不能未经检查就用粗图判定全部站位。
- CoACD 适合物理资产的孔洞/凹槽本身建模不合理的情况，优先级低于查询后端；多数现有碰撞件已接近凸，不预期全库重分解带来普遍收益。同步修改物理几何后任务本身也变了，结果须作为新场景版本解释。

下一步只选“简单桌面 Pick、复杂邻近障碍 Pick、长 Open”各一个目标，固定站位与 seed，先记录真实局部部件数与当前碰撞耗时占比，再对当前/cuboid/ESDF做同条件检查。CoACD 仅在其中确有凹形资产问题时加入派生资产实验。采用标准是几何判定可解释且完整试验总成本下降，不是单纯成功率更高或更快返回失败。

本次按用户应用口径建立了可核查的性能估算模型，未运行 profiler、ESDF 或 CoACD；相关数字均保留为估算。官方接口依据见第 15 节，生产代码和现有运行未改。

### 16.5 用户澄清后的精度/性能取舍

目标是保留会改变站位可操作性的几何，而非追求整屋几何同等精细。第 16.2 节的速度区间只是条件假设，不能据此认定实际方案优劣；推荐依据是本应用的误差影响位置和可复用计算量。

| 几何范围 | 精度要求 | 建议表示与性能取舍 |
|---|---|---|
| 目标、把手、夹爪附近，以及物体起落处支撑面 | 最高：小误差会改变抓取/接触/抬起可行性 | 保留物理一致的局部几何；本来为 box 的支撑面用解析 cuboid，既精确又便宜；不把整张带腿桌子当成一个实心盒 |
| 底盘、机械臂、携带物运动范围内的邻近静态障碍 | 保留实际间隙、薄壁和实体占用 | ESDF 是主要候选；毫米级间隙/薄件处保留精细几何，不能统一用厘米栅格决定成败。5 mm 可作为对照起点，不是无条件精度保证 |
| 确实在整段动作范围之外的背景 | 不需要近接触精度 | 可按保守范围筛选或粗包围盒处理；范围须覆盖手肘、躯干、底盘和携带物，不只看 TCP/目标距离 |

四方案取舍：原 RBY 对象包围盒可用于背景，通常不适合目标近场；当前合并凸包是已测的过渡基线，但不能忽略内部漏判；静态 ESDF 配合少量精细近场/动态几何最值得优先验证；CoACD 仅对确实因单凸包填平凹处而影响操作的资产离线使用，且同步物理表示。无需为了采用 ESDF 先重做所有资产，也不要求所有障碍均转入同一种表示。

接受近似的条件是它不改变关键接触关系和可通行间隙；只降低无关背景细节是有价值的提速。由漏碰撞造成的“成功率提高”、由近场大盒子造成的“更快失败”，都不满足这个用途。此澄清仅修正文档重心，未改配置或执行任何实验。
