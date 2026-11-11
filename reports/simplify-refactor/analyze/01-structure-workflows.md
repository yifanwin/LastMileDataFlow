# 第 1 路：目录结构 + workflows / scripts / config / gpu / utils

审查对象：pro5000 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify`，分支 `refactor/simplify`。
内容以变基前的分支尖端 `bfd707d`（ORIG_HEAD）为准。审查时（11-12 00:20 CST 起）**这个工作树里有人正在做交互式变基**，停在 `0f8648c` 上，`pool.py`、`workers.py`、`scene_datasets.py` 里还有冲突标记，`workflows/collection.py` 处于 deleted-by-us 状态。本文只读代码，没有改动工作树，也没有运行任何程序。

行号对应 `bfd707d` 中的文件。行数估计是净删减量（负数表示删减）。

---

## 一、目录结构

### 1.1 现状：六个领域目录互相循环依赖

按 `from ..x import` 统计领域目录之间的引用（数字是引用次数）：

| 从 → 到 | runtime | scenes | planning | data | policies |
|---|---|---|---|---|---|
| runtime | – | 26 | 8 | 10 | 4 |
| scenes | 15 | – | 2 | 3 | – |
| planning | 13 | 12 | – | – | – |
| data | 6 | 4 | 3 | – | 1 |
| policies | 4 | 3 | – | – | – |

几乎每两个目录之间都是双向依赖，所以「六个领域目录」只是按名字分了组，并没有形成分层。几处典型的倒挂：

- `planning/pool.py`、`planning/remote.py` 引用了 `runtime.execution.{context,lifecycle,manipulation,protocol}`。它们实际做的是调度执行（GPU 规划服务进程池、CPU 端代理），不是规划算法。
- `planning/*` 有 12 处引用 `scenes.geometry`，后者其实是纯 MuJoCo 几何工具（`collision_geoms`、`body_pose`、`tcp_pose`、`quat_yaw`、`SupportRegion`），并不属于场景这一层。
- `planning/navigation/{astar,geometry}.py` 引用了 `scenes.stations.initialization`（`room_triangles`、`floor_support`），也引用了 `scenes.stations.sampling`。
- `scenes/stations/sampling.py` 和 `scenes/edits/search.py` 引用了 `data.rendering.{head_views,edit_views}`。这两个文件做的是目标可见性计算（`TaskVisibility`、`ray_visibility`、`CameraRig`、`in_frame`），属于构建期的判定，不是数据产出。
- `runtime/simulation.py` 引用了 `data.rendering.{camera_calibration,third_person}`，`runtime/control/robot.py:293` 引用了 `data.export.molmobot_dataset.state22`（控制层依赖了导出层）。
- `runtime/execution_config.py` 是执行配置的 dataclass，却放在 runtime 下，还引用了 `policies.config`。配置 dataclass 本该集中在 `config.py`，结果分散在 `config.py`、`runtime/execution_config.py`、`workflows/station_map.py:RunResources`、`scenes/dataset_config.py`、`policies/config.py`、`data/evaluation/benchmark.py:BenchmarkConfig` 六处。
- RL 文件分散在 9 个目录里：`runtime/rl`、`policies/sac`、`workflows/rl_*`、`scenes/rl_observation.py`、`scenes/stations/rl.py`、`scenes/loading/rl_scene.py`、`data/rendering/rl_{capture,video}.py`、`data/visualization/sac_monitor.py`、`data/evaluation/station_q.py`，另外 `gpu.py:sampler_entry` 是 RL 进程的入口。

### 1.2 建议的分层（自下而上，只允许上层引用下层）

```
lastmile_dataflow/
  config.py            # 所有配置 dataclass 和 read_config（并入 ExecutionConfig、StationRun/RunResources 合并后的类）
  gpu.py, utils/       # 设备绑定、io、logging（不变）
  geometry.py          # ← scenes/geometry.py（纯 MuJoCo 几何），planning/scenes/runtime 共用
  sim/                 # ← runtime/simulation.py + runtime/control/ + runtime/construction/
                       #   + runtime/outcomes/{contacts,health}.py（物理事实）
                       #   + data/rendering/{camera_calibration,third_person}.py（仿真内相机）
  planning/            # curobo/、robots/、navigation/、waypoints.py、evidence.py（纯规划，只引用 sim/geometry）
                       #   floor 三角形相关（room_triangles/inside_triangles/floor_support）下沉到 sim/ 或 geometry.py
  scenes/              # loading/ tasks/ stations/ edits/ + visibility.py（← data/rendering/{head_views,edit_views} 里的可见性部分）
  execution/           # ← runtime/execution/ + planning/{pool,remote}.py + runtime/outcomes/{pick,pick_state,evaluation}.py
                       #   + data/recorder.py（一次 attempt 的实时记录）
  policies/
    molmobot/          # ← molmobot.py、molmobot_service.py、multitask/（与 pi05/ 对称）
    pi05/  sac/  config.py  task.py
  data/                # 只放离线产物：export/ evaluation/ visualization/ rendering/{analysis_video,replay}
  workflows/           # scene_datasets / station_map（标定）/ collect / lastmilebench / rl_*
```

依赖方向是 `config/gpu/utils/geometry → sim → planning → scenes → execution → data → workflows`，policies 只被 execution 和 workflows 引用。

收益：消除循环依赖，可以去掉现在为了躲循环而写在函数里的导入（例如 `collect.py:271,276,316,365,409`、`station_map.py:220,643`、`lastmilebench.py:266`），也能看清哪些模块是纯函数、哪些会推进仿真。
代价：只移动文件不减少代码量（AGENTS.md 也说「不以移动文件冒充精简」），而且所有测试的 patch 路径都要改。建议**放在各文件精简之后单独做一个提交**，一次只移动、不改逻辑，用 `git mv` 保留历史。

### 1.3 RL 文件的处理

按 11-11 定下的规矩，RL 文件只能改 import 行。如果做 1.2 的迁移，RL 文件里引用了被移动模块的 import 行要跟着改，这在规矩允许的范围内。RL 文件本身先**不搬**，留到 actor-critic 合并之后再统一收进 `rl/` 包，免得和 actor-critic 分支上仍在进行的提交冲突。

RL 文件依赖的共享模块如下，迁移时要同步改它们的 import：

- `workflows/rl_training.py`：`config.REPO_ROOT`、`gpu.sampler_entry`、`policies.sac.*`、`scenes.rl_observation`、`data.visualization.sac_monitor`、`runtime.rl.environment`
- `workflows/rl_evaluation.py`：`data.evaluation.station_q`、`policies.sac.agent`、`scenes.rl_observation`、`data.visualization.sac_monitor`、`runtime.rl.environment`
- `workflows/rl_sampling.py`：`policies.sac.{networks,replay}`、`scenes.rl_observation`、`runtime.rl.environment`
- `gpu.py:110-133`（`sampler_entry`、`isolate_training`、`isolate_from_argv`）只给 RL 入口使用。`sampler_entry` 让 gpu.py 反过来引用 `workflows.rl_sampling`，按理应该移到 rl_sampling.py，但那属于修改 RL 文件，这次不动。

### 1.4 其他结构问题

| 位置 | 问题 | 建议 |
|---|---|---|
| `workflows/station_map.py` 与 `workflows/collect.py` | 名字和内容对不上：`scripts/station_map.py` 调用的是 `collect.run_stations(collect=False)`，而 `station_map.evaluate_stations` 又在函数里 `from .collect import _run_dataset`，两个文件循环引用 | 标定的全部内容（调度器、汇总、热图）放 `station_map.py`，对外暴露 `run_station_map()`；连续采集放 `collect.py`，只引用 station_map 的公开函数。`_run_dataset` 的回调写法（见 2.2）一起拆掉 |
| 各目录下的 `__pycache__` | 留有已删除模块的 pyc（`cli`、`workflows/collection`、`scenes/loading/asset_{library,profiles}`、`test_cli`、`test_mode_downstream`） | 等变基结束后 `find . -name __pycache__ -exec rm -r {} +`。不影响运行，只是 grep 时会被干扰 |
| `tests/workflows/*` | 大量 patch 私有名字（`test_collection.py` 685 行里 patch `_execute`、`_station_trials`、`_recovery_trials` 共二十多处，另有 `test_station_parallel.py`、`test_station_preparation.py`） | 第 2 节的每条重构都会让这些 patch 失效。建议测试改成走公开函数，并注入假的执行函数（见 2.1 的 `trial_worker`/`execute` 参数），不再 patch 模块里的私有名字 |
| `scripts/` | 五个阶段脚本的骨架完全相同（argparse → `select_devices` → `setup_logging` → 延迟导入 → print），`station_map.py` 和 `collect.py` 两个脚本 90% 一样 | 可以保留（每个阶段一个脚本是用户定的）。只做两处小改：`bench_build.py:17` 去掉无用的 `gpu_ids=[None]`；`build_scenes.py:20` 直接传 `args.output`，不要先拆成 parent/name 再在里面拼回去 |

---

## 二、逐文件精简

### 2.1 `workflows/station_map.py`（1100 行，大约可删 400 行）

**A. 两套标定引擎并存（最大的一块，约 -330 行）**

- 旧引擎：`_station_records`、`_station_worker`、`_parallel_station_records`、`_serial_station_records`、`_station_trials`，以及 `_execute` 中标定用的部分，在 L131–L529。每个场景单独起进程，进程里自己规划自己执行。
- 新引擎：`_CalibrationScheduler`（L677–L1081），CPU 准备、GPU 规划池、CPU 执行三段分开调度。
- 用哪一套由 `ExecutionConfig.shared_station_preparation` 决定（`collect.py:350`）。Panda 的四份配置都是 `true`，**只有 RBY-1 的 `configs/execution/curobo_v2.json` 没设，仍走旧引擎**。

建议：先确认 RBY-1 能走新引擎（`prepare_job`/`prepare_station` 和 `PlanningService` 是否支持 rby1m，第 2、3 路审查 runtime/planning 时一并核对），然后删掉旧引擎、`shared_station_preparation` 字段、`StationRun.station_workers` 和 `--station_workers` 文档，以及 `tests/workflows/test_station_parallel.py`、`tests/runtime/test_station_preparation.py` 里测旧引擎的部分。
风险：RBY-1 的标定结果可能变化（执行进程和规划进程分开了），要用 GPU 跑一次 RBY-1 小场景，对照新旧两份 `trials.jsonl`。**渲染设备隔离修好之前不能跑这个对照**（EGL 修复正是现在正在变基的那个提交）。

如果 RBY-1 暂时不能迁移，至少先删下面的死代码：
- `_station_records` L215–L233 的 `prepared` 分支：只有 `shared_station_preparation=True` 时才会进入，而这时 `_run_dataset` 走的是新引擎，根本不会调用 `_station_trials`。生产中不可达，只有 `test_station_preparation.py` 测到它。（约 -20 行）
- 整条链上的 `policy` 参数（L140、209、249、415、433、445、468、593、611，以及 `collect.py:402,487`）：标定和采集都只用 cuRobo（`evaluate_stations`/`collect_lastmile` 开头就检查了），`policy` 永远是 `None`，`_execute` 也没有用它。（约 -15 行）
- `_station_records` L217 写的是 `getattr(config, "shared_station_preparation", False)`，config 是 dataclass，应直接取属性。

**B. `_CalibrationScheduler`：用元组拼装状态，`close()` 太长（约 -60 行）**

- 队列里的元素是裸元组，靠下标取用：`self.ready` 存 `(scene, index, prepared, response, budget, candidates)`，L908、L1015、L1022、L1027、L1045 用 `job[3]`、`job[4]` 或解包取值；`self.preparations` 存 `(scene, index, identities, budgets)`；`self.batches` 存 5 元组；`self.recordings` 存 3 元组。
  改法：顶层定义 `@dataclass(frozen=True) class StationJob: scene; index; identities; budgets`，以及 `class ReadyTrial: job: StationJob; prepared; response; budget; candidates`，按字段名访问。
- 计划试验数的计算写了两遍：L682–L692 的 `planned` 和 L753–L758 的 `remaining` 是同一个式子 `min(len(stations)*tps, max_trials or ∞)`。改法：提一个 `def _scene_trials(scene, tps, max_trials)`。（-10 行）
- 「基础设施失败」字典 `{"status": "infrastructure_error", "failure_scope": "shared", "reason": ...}` 手写了三遍（L903、L1008、L1051），「CPU worker 退出」的处理也写了两遍（`_check_workers` L900–L918 和 `close` L1043–L1057）。改法：`def _infrastructure_failure(reason)`，再加一个 `_reap_exited(reason)` 给两处共用。（-25 行）
- `__init__` 有 70 行，构造时就启动了所有进程，进程池和 GPU 池也在里面创建；中途出错的话，已经起来的进程无人关闭。改法：`__init__` 只组装状态；`run()` 改成 `with self._started():` 统一负责启动和收尾。
- `close()` 有 77 行，混着四件事：给运行中的试验发停止应答、排空执行中的试验、记录未执行的试验、关闭进程和队列。拆成 `_fail_inflight()`、`_drain_active()`、`_flush_recordings()`、`_shutdown()` 四个方法。
- L694 和 L1078–L1081 会保存并恢复 `CUDA_VISIBLE_DEVICES`，但这个类以及它调用的 PlanningPool 构造过程都没有修改这个环境变量（pool.py:241 只是读取）。核实之后可以删掉（-6 行）。
- L725 的 `**{"service_type": service_type} if service_type else {}`：让 `PlanningPool` 自己处理默认值，这里直接传 `service_type=service_type`。
- `run()` 里 L986–L989 的 `results.get(timeout=0.01)` 与 L893–L898 的 `_trials_completed` 重复，可以把 `_trials_completed` 改成「最多阻塞 10ms 后排空」。

**C. 小问题**

- L45 `calibration_recording`：只有两处调用（`collect.py:317,410`），而且都在 `if split` 分支里。旧引擎删掉以后，可以并进 `StationRun` 的一个方法 `recording()`。
- L54–L93 `RunResources` 和 `config.StationRun`（config.py:146）的字段重合了四个（`cpu_workers`、`preparation_workers`、`recording_mode`、`planning_workers_per_gpu`），`_run_dataset` 还要从关键字参数把它们重新组装一遍。改法：合并成 `config.StationRun` 一个类，由它负责校验和 `planning_workers_per_gpu` 的归一化，`gpu_ids` 在运行时作为参数单独传入。（-40 行，collect.py 里那十几个透传参数也一起删掉）
- L98–L128 `_trial_record`：返回 21 个键的裸字典，三处构造（L253、L787、`collect.py:180`）后都要再补上 `station_index`。改法：`_trial_record(..., station_index)` 一次性返回完整记录。要不要改成 dataclass 见 2.2-E。
- L131–L197 `_execute`：L147 的注释写在 docstring 之前；L160、L176 的注释说「按后端选择」，实际只有 cuRobo。日志级别用 if/elif 判断，改成模块级常量 `_RESULT_LEVEL = {"initialization_error": logging.ERROR, ..., "timeout": logging.WARNING}`，然后 `logger.log(_RESULT_LEVEL.get(status, logging.DEBUG), ...)`。（-12 行）
- L558 `display = [{**row, "attempt": row.get("rollout_id")} ...]`：只是为了适配 `aggregate()` 而做的转换，但 `data/evaluation/statistics.py:31` 读的已经是 `rollout_id`，这个 `attempt` 键没人用。可以直接删掉，传 `trials`。

### 2.2 `workflows/collect.py`（655 行，大约可删 200 行）

**A. `_run_dataset` 用回调参数兼做两件事（约 -100 行）**

`_run_dataset(process_scene=...)` 有 230 行，靠 `collecting = process_scene is _collect_scene`（L403）判断当前是采集还是标定，随后在 L408、L424、L463、L491–L495、L504、L522–L544 一共 7 处按 `collecting`/`split` 分支，收尾的 summary 字典也用 `**({...} if collecting else {})` 嵌套拼成。`evaluate_stations` → `_run_dataset` → `process_scene` 和 `collect_lastmile` → `_run_dataset` 每一层都原样转发 12 个关键字参数；`run_stations(collect: bool)` 又用一个布尔参数在两者之间选择。

改法：
```python
@dataclass
class StationRunContext:          # _prepare_run 的返回值，替代 5 元组
    root: Path; scenes: list[dict]; config: ExecutionConfig
    collection: CollectionConfig; prior: list[dict] | None

def run_station_map(dataset, run: StationRun, output, gpu_ids, samples=None) -> Path:
    ctx = _prepare_run(...)
    trials, calibration = calibrate_stations(ctx, run, gpu_ids)
    summaries = [_station_summary(ctx, scene, trials) for scene in ctx.scenes]   # 热图需要 sim 时用 _scene_sim
    _write_summary(ctx, summaries, calibration=calibration)

def run_collection(dataset, run: StationRun, output, gpu_ids, calibration=None, samples=None) -> Path:
    ctx = _prepare_run(..., calibration)
    trials = ctx.prior if ctx.prior is not None else calibrate_stations(ctx, run, gpu_ids)[0]
    pairs = _plan_collection(ctx, trials, source)
    try: for scene in ctx.scenes: _recovery_trials(...)
    finally: _finish_collection(ctx, pairs)
```
两个脚本分别调用 `run_station_map` 和 `run_collection`，删掉 `evaluate_stations`、`collect_lastmile`、`run_stations`、`_evaluate_scene`、`_collect_scene` 和 `_run_dataset` 这一层转发。
风险：`summary.json` 和 `config.json` 的键必须保持不变，要用已有 run 的输出做对照（只对比 JSON 键和数值，用 mock 执行，不需要渲染）。

**B. 重复的仿真打开和关闭（约 -20 行）**

`Simulation.from_snapshot(scene["scene_path"], scene["robot_config"], target=scene["task"]["target_body"]); sim.scene_id = ...; try/finally sim.close()` 出现了 4 次：`station_map.py:281`、`collect.py:428`、`collect.py:475`、`lastmilebench.py:128`。
改法：在 `scenes/dataset.py` 里加一个上下文管理器 `open_scene(scene)`，调用方写 `with open_scene(scene) as sim:`。最好让 `scene_id` 作为 `from_snapshot` 的参数传进去，不要在外面补设属性。

**C. 导航栅格构建重复（约 -15 行）**

`_recovery_trials` L110–L120 和 `lastmilebench._navigation_region` L59–L65 都是「量 footprint → 调 `scene_grid(..., radius, margin, robot_height=... if whole_body else .4, support_obstacles=task_support_obstacles(...))`」。
改法：在 `planning/navigation/astar.py` 加 `navigation_grid(sim, task, settings, footprint)`，两处共用；`0.4` 这个高度也收进去，变成命名常量。

**D. `_plan_collection` 和 `_recovery_trials` 内部冗余（约 -40 行）**

- L55–L85：先构造 `candidates` 字典列表（含 s0、s1、两个成功率），再展开成 pair 字典，又用 `"s0": start["station_id"]` 把 s0、s1 重复写了一遍。改成直接遍历 `generate_pairs(...)`，一次构造出 pair。（-12 行）
- L69 和 L158–L161 手写欧氏距离和折线长度，改用 `math.dist(start["xy"], goal["xy"])` 和 `float(np.linalg.norm(np.diff(path["xy"], axis=0), axis=1).sum())`。
- L86–L98：`direct_rows` 和 `seen` 先过滤一遍，再 `dict(row)` 复制，再做第二次 `successful(..., observations=True)` 检查。`successful(row, observations=True)` 本身就蕴含 `successful(row)`，改成一个循环 `if successful(row, observations=True) and row["rollout_id"] not in seen`。（-8 行）
- `_recovery_trials` 有 100 行，把单个 pair 的处理（L132–L188）拆成 `_run_pair(navigation, scene, pair, stations, ...)`，主循环只负责写盘、更新进度、遇到共享失败时停止。
- L189–L190 每处理一个 pair 就把整个 `pair_index.jsonl` 重写一遍，复杂度是 O(n²)。行为可以保留（中途断掉也能看到进度），但应注释说明这是有意为之。

**E. 裸字典（可选，中等改动）**

pair 是一个有 20 多个键的可变字典，经过 `_plan_collection`、`_recovery_trials`、`_run_dataset` 收尾三处原地修改。可以引入 `@dataclass class CollectionPair`，带 `to_row()`，写盘时用 `asdict`，键名保持不变。从 jsonl 读回来的行仍然当字典用，下游 `data/export`、`data/evaluation` 不用改。

**F. 小问题**

- L238–L324 `_prepare_run`：L271 和 L276 在循环里导入；L316–L323 先写一次 `config.json`，读回来补上 `calibration_collection` 后又写一次。改成先把完整的配置字典组装好，再调用一次 `create_run`。（-8 行）
- L355–L357 和 L378：两个脚本已经调用过 `select_devices`（它会设置 `LASTMILE_EGL_DEVICES` 并绑定第一张卡），这里又调一次 `select_devices` 和 `bind_device`。`egl_devices` 从脚本一路传到 `_parallel_station_records`，但子进程完全可以从环境变量 `LASTMILE_EGL_DEVICES` 里取到映射。可以删掉 `egl_devices` 参数，只在入口解析一次。（-10 行，涉及 gpu.py 的约定，改之前要先看完 EGL 修复提交的最终版本）
- L463 `if stopped and (collecting or not split)`：拆成两个函数后自然就消失了。

### 2.3 `workflows/scene_datasets.py`（479 行，大约可删 90 行）

- L123–L220 `_select_tasks`（98 行）：5 种排除原因，各自写一个 `skipped.append({"task_id":..., "reason":...}); continue`。
  改法：其中「是否在配方范围内」的判断（selectors、exclude_tasks、include_categories、include_assets、exclude_categories）移到 `SceneRecipe.rejects(task, house) -> str | None`（放在 scenes/dataset_config.py）；剩下的物理条件写成 `_physical_rejection(sim, task, recipe, floor) -> str | None`，同时返回 surfaces 和 coarse。主循环只剩 `reason = recipe.rejects(...) or ...; if reason: skipped.append(...)`。（-35 行）
- `selector in (task["instance_id"], task["asset_id"], task["task_id"])` 在 L69 和 L130 各写了一遍，提成 `task_matches(task, selector)`。
- L49 `spatial_select`：参数名 `rows` 遮住了模块导入的 `io.rows`；`preferred` 和 `min_separation_m` 只有测试在用，唯一的生产调用（L208）没传，核实测试用途后删掉（-15 行）；L106 的内嵌闭包 `score` 依赖外层的 `rooms` 和 `supports`，改成模块级的 `_spread_key(row, distance, rooms, supports)`，用 `max(candidates, key=partial(...))` 调用。
- L257–L359 `_build_house`（103 行，缩进最深 6 层）：把单个任务的处理（L289–L353）拆成 `_build_one_task(sim, task, task_index, recipe, house, root, baseline_cache) -> tuple[list[dict], dict]`。`_task_preparation_rejections` 和超时分支各自构造的拒绝记录（L296–L307、L336–L352）统一成 `_rejection(house, task, reason, detail, intents)`。
  L281–L284 `if not hasattr(sim, "source_metadata")` 是在补 `Simulation.from_snapshot` 没设置的属性，应该由 Simulation 自己保证（第 2 路），这里删掉。
- L362–L451 `_build_into_root`：「保存一个 house 的结果」在主循环（L392–L403）和 finally 收尾（L429–L436）里各写了一遍，提成 `_save_house(result, root, summary)`。（-12 行）
  L411 `except Exception as exc: error = {...}`：父进程里的任何异常（包括代码 bug）都被转成字典，再在 L448 包成 `RuntimeError` 抛出，原始调用栈就丢了。worker 端的异常已经由 `_build_house` 转成 `source.error`，父进程这一层应该删掉这个 except，让异常带着调用栈直接抛出，summary 照样在 finally 里写。（-5 行）
  `pending` 只在 `pool` 分支里定义，L408、L413 却依赖 `if pool:` 来保护它。改法：串行分支也用 `[]` 初始化。
- L454 `_create_build_root`、L466 `_build_worker_limit` 都只有一个调用方，内联进 `build_scenes`。`build_scenes` 的 `workers=` 参数没有调用方，删掉。（-12 行）
- `report` 和 `records` 都是裸字典。house 的报告可以做成 `@dataclass HouseReport`，写盘时用 `asdict`；拒绝记录的格式交给 scenes/edits 那一侧统一（第 3 路）。

### 2.4 `workflows/lastmilebench.py`（304 行，大约可删 50 行）

- L71–L218 `build`（148 行）：拆成 `_scene_report(scene, calibration, choices, settings)`、`_eligible_episodes(sim, scene, region, choices, subset, settings, rng) -> (eligible, exclusions, diagnostics)`（L141–L181）和 `_write_episodes(...)`（L183–L199）。主流程只剩遍历场景和收尾写盘。
- L71 的 `gpu_ids=None` 参数没有使用；L73 的 `calibration_dir is None` 检查在脚本里已经由 `required=True` 保证。两处都删。
- L188–L190 `region_id = eid` 只是多起了一个名字，删掉这个变量，oracle 的格式不变。
- L121–L122 `reason = (... if ... else ... if ... else ...)` 是嵌套三元表达式，`{reason: low_count or 1}` 的语义也不清楚。改成一个小函数 `_no_choice_reason(complete, low, high)`，计数直接写 1。
- L221–L304 `evaluate`：
  - L247 `getattr(policy_config, 'embodiment', 'rby1m')`、`policies/config.py:71` 的 `hasattr(config, 'embodiment')`：MolmoBot 的配置没有 `embodiment` 字段。给 MolmoBot 的配置加上 `embodiment: str = "rby1m"`，两处都改成直接访问属性（第 4 路）。
  - L254–L262 `parent`/`sim` 互相赋值（MolmoBot 要包一层 `native_scene`，π0.5 不用），改成上下文管理器 `_episode_sim(snapshot, robot, backend)`。
  - L264–L275：ExecutionConfig 和 CollectionConfig 的构造提成 `_evaluation_configs(saved, settings, backend, policy_path, sim, seed)`；L266 函数里的 `from dataclasses import replace` 挪到文件顶部。
  - L232 `bind_device(gpu_ids[0])`：脚本已经调用过 `select_devices`，这里重复，删掉，同时去掉 `gpu_ids` 参数。
- 这个文件没有统一格式化（逗号后不加空格、一行超过 150 字符），而 collect.py 和 station_map.py 已经按 black 格式化过。建议统一跑一次 `ruff format`，单独提交，提交里只有格式改动。

### 2.5 `config.py` + `runtime/execution_config.py`（409 行，大约可删 70 行）

- **只允许一个值的字段**：`RobotConfig.name`/`namespace`/`planning_adapter`（L38、L46）、`ExecutionConfig.planner_backend`/`max_attempts == 5`/`torso_error_policy`（execution_config.py L93、L99、L115）。这些字段只能取一个值，存在的作用只是让 `__post_init__` 报错。改法：改成模块常量，删掉字段和对应的检查。
  风险：`asdict(config)` 会写进 `config.json`，`calibration_execution` 的对比也会受影响，旧的标定 run 将无法复用。要么接受这一点，要么在 `calibration_execution` 里统一忽略这几个键。
- **`calibration_execution` 里排除的键大多已经不存在**（execution_config.py L196–L207）：`mode_config`、`torso_heights`、`lastmile_nearby_m`、`start_count`、`max_s1_candidates`、`smoothing_support_distance_m` 都已不是 ExecutionConfig 的字段，留着只是为了兼容旧的标定。按 AGENTS.md「不留内部旧路径兼容壳」，可以删掉；代价同样是旧标定无法复用，请用户决定。
- `ExecutionConfig.__post_init__` 有 100 行，其中逐字段的正数、整数检查占了 60 行。改法：在类里列出 `_POSITIVE_INTS`、`_POSITIVE_FLOATS`、`_NONNEGATIVE` 三个元组，用一个循环检查。（-30 行）`CollectionConfig`、`RobotConfig` 用同样的写法。
- `ExecutionConfig` 搬进 `config.py`；`execution_profile`/`calibration_execution` 只有 collect.py 用，搬到那里（或 station_map.py）。这样 `runtime/` 就没有配置文件了。
- `LocalConfig.output_dir`（L171）在 src 里没有任何调用方，删掉；`CollectionConfig.output_dir` 的默认值 `"outputs"` 实际上总会被 `_prepare_run` 覆盖，第 2 路可以核实它是否还有用。
- `RobotConfig` 和 `PandaOmronConfig` 有 11 个字段相同（`model_path`、`arm_working_reach_m`、`base_radius_m`、三个 `max_*_delta`、`control_hz`、`physics_dt`、`cameras`、`gravity_compensation`、`control_profile`）。可以用一层 dataclass 继承（`_MobileManipulatorConfig`）共享这些字段和速率检查，约 -15 行。但用户的规范是「优先组合」，所以这一项可做可不做，建议不做。
- `StationRun` 与 `RunResources` 合并，见 2.1-C。

### 2.6 `gpu.py`（133 行，基本合理，大约可删 10 行）

- `resolve_egl_devices`、`select_devices`、`bind_device`、`bind_cpu_renderer` 这一套在入口解析一次、子进程从环境变量继承，思路清楚。问题在于调用方没有遵守这个约定，反复传 `egl_devices` 和 `gpu_ids`（见 2.2-F、2.4）。约定统一后，`bind_device(gpu, egl_device=None)` 的第二个参数和 `resolve_egl_devices` 的显式参数都只在入口用到。
- L110–L133 都是 RL 专用（见 1.3），这次不动。
- 正在变基的 `0f8648c`（EGL 修复）会改这一块。上面的建议需要在它合入之后重新对照一次。

### 2.7 `utils/`（145 行，大约可删 5 行）

- `io.diagnostic` 的名字不贴切，它实际是「转成可 JSON 化的值」，改名为 `jsonable`。`runtime/simulation.py:294` 里另有一个同名的内嵌函数，L342 的 `json.loads(canonical(value))` 是绕了一圈再转回来（第 2 路）。
- `create_run(prepared=...)`：`prepared=True` 的含义是「目录可以已存在，但 config.json 不能已存在」，改名为 `allow_existing_dir`。
- L5–L6 多了一个空行。`logging_config.py` 没有问题。

---

## 三、建议的执行顺序

1. 等当前的交互式变基做完（EGL 修复合入），清理 `__pycache__`。
2. 只删死代码、不改行为：2.1-A 的死代码部分、`policy` 参数、`attempt` 适配、`region_id`、`LocalConfig.output_dir`、`spatial_select` 的未用参数、未用的 `gpu_ids`、函数里的导入。单元测试用 `MUJOCO_GL=disable`。
3. workflows 结构重组：2.2-A（拆掉 `_run_dataset`）、2.1-B（调度器加 dataclass、拆 `close`）、2.1-C 和 2.5（合并 StationRun 与 RunResources）、2.2-B/C（共用 `open_scene` 和 `navigation_grid`）、2.3、2.4。同时把测试改成走公开接口。用已有 run 的 `config.json`、`summary.json`、`pair_index.jsonl` 对照键名。
4. RBY-1 改用新调度器，删除旧引擎（2.1-A）。需要 GPU 对照，**要等渲染隔离确认修好**。
5. 配置字段的取舍（2.5 的前两条），需要用户决定是否放弃复用旧标定。
6. 最后单独提交 1.2 的目录分层迁移，只移动文件、不改逻辑。

第 1 路的范围（workflows、config、gpu、utils）合计约 2970 行，预计能删掉 700–800 行（-25%），其中旧标定引擎约占一半。
