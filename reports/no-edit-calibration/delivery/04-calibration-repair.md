# PandaOmron 标定修复：阶段交付

- 状态：active（P1 完成；重构阶段 1–4 完成；P2 部分完成后按要求暂停）
- 日期：2026-10-27
- 关联：[修复诊断方案](../plan/03-calibration-repair-and-diagnosis.md)、[重构计划](../plan/calibration-repair-schedule.md)、[复用缺陷分析](../analyze/03-planner-reuse-target-binding.md)、[Cup_30 顺序对照](../analyze/02-cup30-reuse-order-experiment.md)

所有改动都在本 worktree 的工作区，尚未提交。

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

## 3. P2：几何/碰撞对照（按当前进度暂停）

已完成：

- **12 站诊断集**（`outputs/diagnostics/p2-geometry-20261027/selection.json`），用 `failure_analysis._planning_failure` 从 `no-edit-val-all-c001…c005` 的 6,050+ 站位确定性选出，覆盖 12 个 house：goalset 底座约束主导 6（Pick/Open 各 3）、原成功 2、低目标不可达 2、pregrasp 跟踪超时 2。
- **初始站位状态的球距离**（`scripts/experiments/p2_collision_diagnosis.py`，结果 `distances.json`、`summary.json`）：在未 begin 的新 sim 中恢复站位 `state.npz`，按规划参考系（`base_site` 位置 + `mobilebase0_base` 朝向，URDF 偏移 omron_base +0.21 m、panda_link0 +0.70 m）放置 cuRobo 球，与 `collision_world` 导出给 cuRobo 的同一组场景网格求点到网格距离。
  - `omron_base`：4/12 站已有球侵入场景网格（-0.014 至 -0.068 m；val10 椅子、val103 墙、val12 餐桌、val32 抽屉柜），其余 8 站最小净空 +0.02 至 +0.34 m。
  - `panda_link0`：12 站都没有侵入（最小 +0.049 m）。
  - 编译模型中底盘 box 为 0.70×0.50×0.38 m，固定柱/升降柱为 r≈0.12 m 的圆柱；12+8 球的包络比实体外扩，与问题梳理中的已知值一致。

未完成，结论只限于初始状态：

- `distances.json` 里的物理几何距离（`physical[*].distance_m`）不可用：`mj_geomDistance` 对远处 mesh 对返回 0，被误判为接触。改用三角网格测距的版本单站耗时过长，已停止。因此目前不能区分"实体相交"和"球近似误拒绝"。
- 未对 goalset IK 末态、TrajOpt 失败轨迹做同状态对照，未做坐标/尺度三层核对和可视化。
- 由于缺少物理侧距离，P2 的五类定性归因（真实冲突/球近似误拒绝/坐标错误/SDF 错误/证据不足）尚未给出，P3 的球重拟合不应基于当前数据启动。

## 4. 未开始

- P3 单因素实验（A/B/C/D 组）、P4 可达性与跟踪排查、P5 小批次验收。
- GPU 复核：重构与目录重组后的代码，以及 Pick/Open 混合队列。

## 5. 复现入口

- GPU run 启动脚本：`outputs/logs/p1fix-run.sh <input-dir> <run-id> <gpu>`。本机 MuJoCo EGL 需要 glvnd 库路径，脚本已设置 `LD_LIBRARY_PATH`（其中 `/tmp/lmdf-egl-libs` 在重启后可能丢失）。
- P2：`PYTHONPATH=src .venv/bin/python scripts/experiments/p2_collision_diagnosis.py outputs/diagnostics/p2-geometry-20261027/selection.json --output <json>`
