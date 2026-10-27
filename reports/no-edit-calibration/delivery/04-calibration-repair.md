# PandaOmron 标定修复：阶段交付

- 状态：active（P1 完成；重构阶段 1–4 完成；方案 N0–N2 完成，见第 7 节；N3 未开始）
- 日期：2026-10-27
- 替代：无；第 6 节为只读复核，第 7 节记录 N0–N2 实施结果，不改写前文已有实验结果
- 关联：[修复诊断方案](../plan/03-calibration-repair-and-diagnosis.md)、[重构计划](../plan/calibration-repair-schedule.md)、[复用缺陷分析](../analyze/03-planner-reuse-target-binding.md)、[Cup_30 顺序对照](../analyze/02-cup30-reuse-order-experiment.md)

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

- **12 站诊断集**（`outputs/diagnostics/p2-geometry-20261027/selection.json`），用 `failure_analysis._planning_failure` 从 `no-edit-val-all-c001…c005` 的 6,050+ 站位确定性选出，覆盖 12 个 house：goalset 底座约束主导 6（Pick/Open 各 3）、原成功 2、低目标不可达 2、pregrasp 跟踪超时 2。
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

- GPU run 启动脚本：`outputs/logs/p1fix-run.sh <input-dir> <run-id> <gpu>`。本机 MuJoCo EGL 需要 glvnd 库路径，脚本已设置 `LD_LIBRARY_PATH`（其中 `/tmp/lmdf-egl-libs` 在重启后可能丢失）。
- P2：命令已随脚本修正变化，当前入口见 7.5。

## 6. 方案重梳与严重性判断（2026-10-27，只读复核）

**需要修复的是当前诊断工具中的确定错误；尚未确认主规划链路出现新的严重回归。** 已将下一步重写到[修复与排查方案](../plan/03-calibration-repair-and-diagnosis.md)，本轮未恢复暂停的实验。

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
  本机 MuJoCo EGL 需要 `LD_LIBRARY_PATH` 包含 `/tmp/lmdf-egl-libs/root/usr/lib/x86_64-linux-gnu`（同 `outputs/logs/p1fix-run.sh`）；少了这项，导入 mujoco 时 OpenGL EGL 初始化失败，测试会成批报错。
- **修复导入回归**：`planning/pool.py`（`PlanningService.__init__`、`execute`）和 `planning/remote.py` 从 `.curobo_v2` 改为 `.curobo.planner`。新增 `tests/planning/test_pool.py::test_resident_service_uses_the_current_planner_module`。用静态扫描检查了 `src/`、`tests/`、`scripts/` 中所有指向本包的 import，没有发现其他不存在的模块。
- **`costs.py` 规范整理**：`ContactManager`、`ContactManagerCfg` 移到模块顶层，`compute_costs` 拆成目标/把手接触、工作区圆、头部 FOV 三个方法，行为不变。cuRobo 0.8.0 要求 `type(cfg)` 与 `cost_manager_config_instance_type` 完全相同，而且只通过 `type.create(data_dict, ...)` 创建配置，所以 `manager_type(context)` 返回一个只带 `planner_context` 类属性的子类，不再用闭包捕获 context。由于 `costs.py` 现在导入时就加载 cuRobo，`planner.py` 改为在 `_create_motion` 内才导入它，CPU 执行路径不受影响。新增 `test_curobo.py::test_cost_manager_type_carries_its_own_planner_context`。
- **`collision_world.py`**：把 geom 选择与网格转换抽成 `exported_geoms()`，`collision_world()` 与诊断脚本共用同一套导出规则，输出不变。
- **离线回归**（主仓库解释器 + worktree `PYTHONPATH` + 上述 EGL 路径）：`tests/planning` 72/72 通过；全量 `tests/` 396 条通过（跳过 2 条）。2.1 节记录的 9 个多进程失败这次没有出现；在 HEAD 临时检出上用同一环境单独跑这三个模块，也是 10/10 通过。因此那 9 个失败更可能与当时的环境有关，但具体原因未确认。

### 7.2 N1：当前版本 GPU 检查

**Cup_30 真实执行**（`n1-cup30-s0022-s0001-20261027`，GPU 6，单 worker，规划复用开启）。输入只保留原第 8、9 站（S0022、S0001），执行配置只把 seed 改为 66，使 6 个 trial 的种子与 `p1fix-cup30-only-20261027` 的 66–71 完全相同。输入和配置在 `outputs/_calibration_inputs/n1-cup30-s0022-s0001-20261027/`。

| 站位 | 原结果（P1 修复后、重构前） | 当前代码 |
|---|---|---|
| S0022 | 3 × planning_no_solution | 3 × planning_no_solution |
| S0001 | 3 × success（执行 421/271/302 步，抬升 0.1015/0.0993/0.0984 m） | 3 × success，步数与抬升逐条相同 |

第二站 `planner_reuse.json` 为 `solver_reused: true`，run `status=completed`，没有 episode 错误。

**Pick/Open 家族复用**（`scripts/experiments/n1_family_reuse_check.py`，结果 `outputs/diagnostics/n1-family-reuse-20261027-r3/family_reuse_check.json`，GPU 7，只做规划、不执行）。用已有 run 的 `state.npz`、任务、抓取候选和原 trial 种子重建冻结的 pre-close 请求，按 Pick → Open A → Open B → Pick 的顺序发给同一个 `PlanningService`，再把每个请求单独发给 fresh planner（`reuse_planners=False`）作对照。

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

**固定 12 站初始状态**（`outputs/diagnostics/p2-geometry-n2-20261027/initial-states.json`，每站一张俯视叠加图 `initial-states-<house>-<station>.png`；val103 的首次单站核验结果在同目录的 `val103.json`）：

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
D=$PWD/outputs/_calibration_inputs/n1-cup30-s0022-s0001-20261027
$PY -m lastmile_dataflow.cli evaluate-stations --dataset $D --config $D/execution_seed66.json \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output-dir $PWD/outputs \
  --run-id <new-run-id> --gpu <gpu> --station-workers 1 --planning-workers-per-gpu 1 \
  --cpu-workers 1 --preparation-workers 1 --recording-mode numerical

# N1 家族复用（查询格式 run:scene_id:station_id，依次给出）
$PY scripts/experiments/n1_family_reuse_check.py --gpu <gpu> \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output-dir <new-dir> --queries ...

# N2 测距（cuRobo checker 用 CUDA_VISIBLE_DEVICES 指定的卡）
CUDA_VISIBLE_DEVICES=<gpu> $PY scripts/experiments/p2_collision_diagnosis.py \
  outputs/diagnostics/p2-geometry-20261027/selection.json [--stations val103/S0000] --output <new.json>
```

日志：`outputs/logs/n1-cup30-s0022-s0001-20261027.log`、`n1-family-reuse-20261027-r3.log`、`p2-geometry-n2-20261027.log`、`n0-unittest-full-20261027.log`。

## 8. 基于第 7 节的下一轮建议（仅文档整理）

- N0–N2 的最小验证已完成，不再重复环境安装、完整 P1 队列或扩大 MuJoCo 薄板测距排查。按用户已授权方式继续使用主仓库解释器加载 worktree 源码。
- 4 个初始站位已有“实体分离、planner 球碰撞”的同状态证据，允许进入 **chassis 球局部修正 A/B**；不必等待全部 IK/轨迹失败完成解释。固定柱、升降柱和 link0 在这批初始状态没有侵入，先保持不变。
- 优先 val10（同障碍实体间隙约 47 mm）观察假碰撞是否消除；val103 只有约 4.2 mm，作为保守边界样本，不为强行通过它而损害实体覆盖。val12/val32 仍可能受低目标可达性限制。
- 同时定位 Cup_30 S0022 的真实 start/end/trajectory 拒绝状态，保持原 seeds 66–68；S0001 的 seeds 69–71 和三条真实成功作为对照。新增候选证据对应实际 query/phase/lane/seed，不凭日志一句 `Start or End` 决定原因。
- 球数/顺序变化后同步诊断脚本 `PARTS` 的 chassis/column 下标，并重建 solver。先保持 2 mm buffer 和 10 mm activation，避免同时调整多个因素。
- 验证分为几何一致、规划通过、物理执行、任务成功四层；初始假碰撞消失但仍因可达性失败，保留为有效分层结果。Open 真实执行尚未覆盖，出现可行轨迹后选一站 3 trial 补齐，不能用规划复用一致代替操作成功。

已把这些建议写入方案第 3.2、6、7 节，并将过时的待修项标为完成、同步最新测试结论。本次保留已有程序改动与实验产物，仅更新两份文档；未修改碰撞球、运行新测试或启动实验。
