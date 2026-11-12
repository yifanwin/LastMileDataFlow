# 第 4 路：data/ 与 policies/ 精简审查

> 2026-11-12 更新：用户改了决定，『RL 文件为达到目标，可以做修改』。本文中“RL 文件只改 import 行、不搬、不动”的限制已作废，以方案 02 第四节第 7 条为准。

审查对象：`pro5000:/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify`（分支 `refactor/simplify`，HEAD `6b6b149`）。本次只读，没有运行任何程序。

> **注意**：审查时这个工作树正处在合并的中途，`git status` 显示 `UU planning/pool.py`、`UU runtime/execution/workers.py`、`UU workflows/scene_datasets.py`、`DU workflows/collection.py`、`UU reports/.../01-handover.md`。动手精简之前，要先把这次合并收尾（或者中止）。下面的行号以审查时的工作区为准。

规模：data/ 约 3,000 行（不含 RL），policies/ 自己写的那层约 700 行；`multitask/`（约 4,000 行）和 `pi05/core/`（约 1,900 行）是移植进来的模型代码，只做了粗看。

RL 相关文件（`data/evaluation/station_q.py`、`data/rendering/rl_capture.py`、`data/rendering/rl_video.py`、`data/visualization/sac_monitor.py`）按规矩不改，只记录它们依赖了什么，见第 6 节。

---

## 0. 结论速览（按收益排序）

| # | 改动 | 预计删减 | 风险 |
|---|---|---|---|
| A | 删除没有生产调用方的死代码：`edit_views.render_edit_pair/framing_points/in_frame`、`replay.render_run/render_observations`、`head_views.camera_state/ray_visibility`、`camera_calibration.capture_sensors` 的深度分支，以及 `Simulation.capture_sensors` | ≈330 行，另删对应测试 ≈150 行 | 低 |
| B | 删除已经失效的“任务模式（labels）”统计分支：`benchmark._counts(strict)`、`summarize` 里的 `mode_run`、可视化 `_facts` 的 labels 分支。其中 `strict` 分支 import 了一个已经不存在的函数 `mode_trial_evaluable` | ≈35 行 | 低 |
| C | 删除深度记录和引用型数据集的残留：`lastmile_dataset` 里的 `observations_ref/record_depth/replayed`，以及 `load_training_batch` 的深度部分 | ≈35 行 | 低 |
| D | 统一试次状态的判定：`selection.evaluable`、`statistics.is_episode_terminal`、`statistics.aggregate` 内联的状态集合，合并成一个模块、一组常量 | ≈25 行，少一个文件 | 低 |
| E | 抽出共用的分割掩码函数，替换 5 处重复的 `segmentation → mask` 写法，顺带去掉 2 处 IndexError 兜底 | ≈40 行 | 中（预览图的兜底行为会变） |
| F | `Simulation.render` 每帧都在算相机标定再丢掉，改成只渲染 RGB；`rendered_camera` 只留给 scene_preview 用 | ≈40 行，录制更快 | 低（runtime 归第 2 路） |
| G | 两个策略服务进程共用一个 `serve()`，客户端用组合代替继承，返回值改用 `PolicyAction` dataclass | ≈50 行 | 中（vla.py 读取字段的地方要一起改） |
| H | 闭包、函数内的辅助函数、压缩成一行的排版：`analysis_video.frame`、`render_scene_preview.load`、`edit_views.rotated`（随 A 一起删掉） | ≈20 行，可读性提升明显 | 低 |
| I | 绘图公共件：`_plotting/matplotlib.use` 出现两次、网格 extent 计算两次、字体查找两份 | ≈25 行 | 低 |
| J | `molmobot_dataset.export_molmobot` 只有测试在用，`state22` 被 runtime 反向 import | ≈45 行 | 要用户定 |

合计源码约 −650 到 −700 行（data/ 约减 22%），测试约 −200 行。

---

## 1. data/recorder.py（231 行）

1. **recorder 往 sim 的私有字段里写缓存**（`recorder.py:80-85`）。`capture()` 直接写 `self.sim._raw_frame_cache`，`Simulation.render` 再读出来用。两个对象共享一份私有状态，职责交叉。
   - 改法：按 `data.time` 缓存当前帧这件事由 `Simulation.render` 自己负责（`self._frame_cache = (time, w, h, frames)`），recorder 只调用 `self.sim.render(w, h)`。
   - 删减约 6 行。风险低。
2. **`video_cameras` 有 None 和 dict 两种语义，判断散在三处**（`:74-77`、`:111-121`）。
   - 改法：加载配置时把它规整成 `dict[camera, name]`（`record_video=False` 时为空 dict），`video_frame` 里只剩 `for camera, name in self.video_cameras.items()`。“同一帧可能被提交两次”的去重保留一行。
   - 删减约 8 行。
3. **`finish()` 写 recording.json 时有 9 处 `if self.sim else None`**（`:190-200`）。`finish` 只会在 `bind_simulation` 之后调用（execution 里都是先绑定再执行），这些三元判断没有意义。
   - 改法：开头加一句 `assert self.sim is not None`，或者把 sim 作为构造参数。同时把 `self.result` 改成 `@dataclass RolloutResult(status, reason, recording_complete, replay_complete, observation_complete, executed_steps, recording_errors)`，下游用 `asdict()` 写盘。
   - 删减约 10 行。风险：`result` 的下游读取方在 runtime/execution（第 2 路），要一起改。
4. **`abort_close()` 在函数体里 import `classify_episode_exception`**（`:226`），但文件顶部第 11 行已经 import 过同一个模块。直接在顶部一起 import。
5. 层次问题，交给第 1 路定：recorder 依赖 `runtime.execution.lifecycle`，runtime 又依赖 recorder，两个包互相引用。建议把 recorder 挪到 `runtime/execution/recorder.py`，它本来就是绑定在 stepping 上的；`data/` 只留离线的统计、导出和出图。

## 2. data/evaluation/

### benchmark.py（174 行）

1. **死分支：任务模式已经删掉，统计里还留着模式分支**（`:108-133`、`:141-166`）。`summarize` 收到的 groups 是 `oracle['episodes']`（`lastmilebench.py:298`），里面只有 `case/failure_family`，从来没有 `labels`，所以 `mode_run` 恒为 False。而 `strict=True` 分支 `from .selection import mode_trial_evaluable` 引用的函数根本不存在，一旦走到就是 ImportError。
   - 改法：删掉 `strict` 参数、`mode_run`、`label_denominator_rule`、`execution_*_counts`；`summary['primary_macro']` 固定为 `'macro_by_case'`，分组字段固定为 `('case', 'task', 'failure_family')`。
   - 删减约 25 行。行为不变。
2. **`reference_calibration` 用 `SimpleNamespace(operation_backend=backend)` 去适配 `station_calibration(config)`**（`:54-62`），这是为了凑参数造出来的假对象。
   - 改法：`station_calibration(stations, trials, expected, binding, backend: str)`，station_map 和 collect 两处调用传 `config.operation_backend`。
   - 删减约 2 行，并去掉 `SimpleNamespace`。
3. **`select_starts(low=.2, high=.6)` 的默认值和 `BenchmarkConfig.low/high` 重复**（`:65`）。改成参数必填，调用方传 `config.low/high`。
4. **`BenchmarkConfig.__post_init__` 有 15 行校验**（`:32-47`），其中 `navigation_margin_m < 0` 判断了两次（`:39` 和 `:41-43`）。配置属于外部边界，校验可以保留；去掉重复，把三个循环合成一张 `(字段, 下限, 是否允许等于)` 表。删减约 5 行。
5. 返回的都是大 dict（`_counts`、`summarize`），落盘就是 summary.json。可以定义 `@dataclass OutcomeCounts`，用 `asdict` 落盘。收益中等，可以放到最后做。

### selection.py（38 行）+ statistics.py（40 行）→ 合并成 `calibration.py`

1. **“哪些状态算有效试次”有三份定义**：`selection.evaluable`（`:4`）、`statistics.is_episode_terminal`（`:9`）、`statistics.aggregate` 里内联的元组（`:27`）。前两份还不一样：一份含 errors，一份不含。
   - 改法：
     ```python
     EVALUABLE = frozenset({'success', 'failure', 'planning_no_solution', 'timeout'})
     TERMINAL = EVALUABLE | {'initialization_error', 'episode_error', 'recording_error', 'simulation_failure'}
     ```
   - `is_episode_timeout` 只是一行包装，内联掉。`statistics.aggregate` 只有 station_map 在用，和 `station_calibration` 放在一起。
   - 删减约 15 行，少一个文件。
2. **`generate_pairs` 只是一行包装**（`:36-38`），调用方直接写 `list(itertools.product(starts, goals))`。
3. **`eligible_stations(…, config)` 只用到两个阈值**，改成 `low, high` 两个参数。

### recovery.py（159 行）

1. **`RecoveryRegion` 里的 patch 是两种结构共用一个 dict**：几何导航给的是 `{'anchor', 'boxes'}`，栅格给的是 `{'anchor', 'cells'}`。`contains`、`goals`、`to_dict` 每个方法都在用 `if 'boxes' in patch` 分流（`:56`、`:82`、`:101`）。
   - 改法：拆成两个 dataclass，都实现 `contains(xy, yaw)` 和 `goal_poses()`：
     ```python
     @dataclass
     class BoxPatch:
         anchor: Station
         boxes: list

     @dataclass
     class CellPatch:
         anchor: Station
         cells: list
         distances: dict
     ```
     `RecoveryRegion.contains` 只保留“最近实测站位裁剪”加 `any(p.contains(...) for p in patches)`。`build()` 里的栅格分支挪到 `CellPatch.build(grid, station, radius)`。
   - 删减约 10 行，主要收益是可读性。风险：oracle.json 里 `patches` 的序列化格式要保持不变（`to_dict/from_dict` 负责转换）。
2. **`motion_diagnostics`**（`:132-159`）里的 `unknown` 模板 dict 加 `{**unknown, 'diagnostic_status': ...}`，可以改成 `@dataclass MotionDiagnostics`，所有字段默认 None，出错的地方只设 `diagnostic_status`。删减约 5 行。

### station_q.py：RL 文件，不改

## 3. data/export/

### lastmile_dataset.py（164 行）

1. **深度记录和引用型数据集的残留**。物理和深度记录已经删掉，`--materialize-from` 也已经删掉，但还剩：
   - `_copy_rollout` 里的 `observations_ref`、`replayed`、`record_depth` 分支（`:56-71`）；
   - `training_record` 复制 `observations_ref/observation_source`（`:27`）；
   - `finalize_dataset` 的 view 过滤里的 `observations_ref`（`:93`）；
   - `load_training_batch` 的 `depths`（`:153-156`、`:163`）。
   - 改法：只保留 `cameras = profile.robot.cameras if record_images else []`，固定复制 `{step:06d}.png`；metadata 里去掉 `record_depth`。
   - 删减约 25 行。行为不变（现在的采集不会再产生这些字段）。
2. **旧格式的默认值兜底**：`layout or {'state_key':'state22', ...}`（`:45`）、`layout.get('action_layout', 'rby1-20d')`（`:76`）、`metadata.get('state_layout', 'state22')`（`:143`）。现在的 recorder 总会写 layout，按 AGENTS.md“内部按约定直接使用”的规矩，直接用 `layout['…']`。删减约 4 处。
3. **`finalize_dataset` 用 try/finally 保证写出 summary**（`:87-123`）。改法：成功后再写 `status='completed'`；中途抛错就没有 summary.json，读取方本来就会检查 `status == 'completed'`，缺文件同样会报错。再顺手删掉无意义的别名 `dataset = root`。删减约 6 行。
4. `training_record` 用字段名元组拼 dict（`:22-31`）。可以改成 `@dataclass TrainingRecord`，S0→S1 的字段放在一个可选的子 dataclass 里，写 JSONL 时 `asdict`。收益中等。
5. `load_training_batch` 只有测试在用（`tests/data/test_lastmile_dataset.py`、`test_panda_recording.py`），它是给训练端用的读取接口。去掉深度部分后留着即可；如果训练端不打算用，可以整函数删掉（约 38 行）。

### pi05_dataset.py（129 行）

1. **`convert()` 有 75 行**（`:36-111`），里面混着校验、逐帧写入和归一化统计。拆成三个函数：`_check_rollout(rollout, metadata) -> (state, action, task)`、`_write_episode(writer, …)`、`_norm_stats(states, actions)`。主流程保持在 15 行以内。
2. **三处重复的 RBY-1 相机映射**：`CAMERAS`（`:17`）、`pi05/observation.py:13-14`、`pi05/inference.IMAGE_KEYS`。合并成 `pi05/config.py` 里的一个 `PI05_CAMERAS = {'rby1m': {...}, 'panda_omron': {...}}`。删减约 6 行。
3. 模块自带 `main()`/argparse（`:114-129`）。按“每个阶段一个脚本”的约定，挪到 `scripts/export_pi05.py`，源码模块里只留函数。

### molmobot_dataset.py（50 行）

1. **`export_molmobot` 和 `UnsupportedMolmoBotData` 没有任何生产调用方**，只在 `tests/data/test_dataset.py` 里用到，也没有脚本入口。
2. **`state22` 被 `runtime/control/robot.py:293` 在函数内反向 import**，也就是 runtime 依赖了 data.export。
   - 改法：`state22` 挪到 `runtime/control/robot.py`，或者第 2 路建议的 layout 模块；`export_molmobot` 删掉，或者给它补一个脚本入口。
   - **要用户定**：MolmoBot 的训练数据导出还要不要？删的话约 −45 行，另删测试约 −40 行。

## 4. data/rendering/

### edit_views.py（266 行）：大半是死代码

1. **`render_edit_pair`（`:140-266`，127 行）、`framing_points`（`:87-118`）、`in_frame`（`:121-137`）在 src、scripts、tests 里都没有调用方**，是旧编辑配对图的残留。其中还带着函数内闭包 `rotated()`、`except Exception` 转 RenderError，以及一段 `OpenGL` 的 `except Exception`。
2. 剩下真正被用到的只有 `ViewConfig`、`CameraRig`、`RenderError`。`ViewConfig` 只用了 `width/height/fovy_rad` 三个字段：`max_expansions`、`max_review_retries`、各种 tolerance、`*_aux_views`、`max_camera_trials` 全是给 `render_edit_pair` 用的，连同 `__post_init__` 里对应的 13 行校验一起删。
3. 改法：删掉上面这些，把留下的 `ViewConfig(width, height, fovy_rad)` 和 `CameraRig` 并进 `third_person.py`，统一改名为 `rendering/cameras.py`。
   - 删减约 200 行，少一个文件。风险低（`tests/data/test_rendering.py` 只测 `CameraRig` 和 `ViewConfig`）。

### head_views.py（250 行）

1. **`camera_state`（`:11-16`）没有调用方**，删掉。**`ray_visibility`（`:182-213`）只有 `tests/data/test_visibility.py` 在用**，生产代码都走 `TaskVisibility`，连同测试一起删。删减约 38 行。
2. **`robocasa_camera_calibration`（`:19-40`）只有 `scenes/edits/search.py:106` 在用**，而且在函数体里 import 了 `panda_robocasa` 和 `panda_omron`。它校验的是机器人相机的出厂参数，应该挪到 `runtime/control/panda_robocasa.py`（`ROBOCASA_CAMERAS` 本来就在那里），同时去掉函数内的 import。
3. **`TaskVisibility.measure` 用临时替换 `self.sim` 再在 finally 里换回来的方式传参**（`:148-167`）。改成 `_camera(sim, name)` 显式接收 sim，不再改动成员变量。删减约 5 行，也去掉了一个 try/finally。
4. **`head_frame` 的异常处理层层叠加**（`:221-250`）：内层 `except IndexError` 退回“分割不可用”；外层 `except (ValueError, KeyError): raise` 是空转；`except Exception` 又把错误包成 RenderError。而 `_dense_segids` 本来就是用来修 IndexError 的（`TaskVisibility._mask` 用了它之后就没再兜底）。
   - 改法：调用 E 节的共用函数（先 `_dense_segids` 再分割），删掉 IndexError 兜底、两层 except，以及 `renderer=None` 时自建 renderer 的分支（两个调用方都传了 renderer：`sampling.py:91,222` 和 `vla.py:45`，需要确认 vla 那一处）。
   - 删减约 15 行。风险中：原先“segmentation_unavailable”的降级路径会消失。如果 `_dense_segids` 修好之后还会越界，错误会直接暴露出来。

### E. 抽出共用的 `geom_mask(renderer, sim_or_model, geom_ids)`

同样的写法“打开分割渲染 → render → 比对 `seg[:,:,1]==mjOBJ_GEOM & isin(seg[:,:,0], ids)` → 关闭分割渲染”出现了 5 次：`head_views.TaskVisibility._mask`、`head_views.head_frame`、`scene_preview.observer`、`third_person.render`（逐个角度打分）、`edit_views.render_edit_pair`（随 A 删掉）。另外“某个 body 的所有后代 geom”这一步（`descendants` + `isin(geom_bodyid)`）也重复了 4 次。

```python
def body_geoms(model, body) -> np.ndarray:
    return np.flatnonzero(np.isin(model.geom_bodyid, list(descendants(model, model.body(body).id))))

def geom_mask(renderer, geoms) -> np.ndarray:
    _dense_segids(renderer.scene)
    renderer.enable_segmentation_rendering()
    try:
        seg = renderer.render()
    finally:
        renderer.disable_segmentation_rendering()
    return (seg[:, :, 1] == mujoco.mjtObj.mjOBJ_GEOM) & np.isin(seg[:, :, 0], geoms)
```

删减约 40 行。`third_person.render` 也被 RL 的 rl_video 用到，但它的公开签名 `render(sim, renderer, w, h)` 保持不变，RL 文件不用动。

### camera_calibration.py（85 行）与 Simulation 渲染（F，和第 2 路交叉）

1. 生产代码里 `Simulation.render` → `capture_sensors(depth=False)` 每一帧都会对每个相机算一遍完整的 `rendered_camera` 标定 dict，然后只取 rgb，其余全部丢掉（`simulation.py:304-313`）。录制和评测的每一帧都在白算。
2. `depth=True` 只在 `Simulation.capture_sensors` 里出现，而这个方法只有 `tests/data/test_recording.py` 在调用。深度记录已经删掉，这条路径是死的。
3. 改法：`Simulation.render` 直接逐个相机 `update_scene + render`（第三人称相机走 `ThirdPersonCamera`）；删掉 `capture_sensors`（两份）、`_capture_depth_requested`、`_sensor_result`。`rendered_camera` 只有 scene_preview 在用，挪进 `cameras.py` 或 scene_preview，返回值只保留它用到的 `T_world_camera_optical/near_m/projection_type/K`，其余 15 个描述性字段都删掉。
4. 删减约 60 行，录制也会更快。注意 `simulation.py` 归第 2 路。

### analysis_video.py（84 行）

1. **`frame()` 有 60 行，里面定义了三个闭包 `pixel/dot/station_marker`**（`:51-78`），都依赖闭包变量 `lo/span/left/top/size/draw`。改法：把 `MapPanel(left, top, size, center, radius)` 做成一个小 dataclass，提供 `pixel()`、`dot(draw, …)`、`marker(draw, …)` 三个方法。`frame` 拆成 `_draw_views`、`_draw_facts`、`_draw_map` 三段。
2. `labels` 这个阶段名映射在每帧都重建一次（`:37-41`），提到模块常量。字体查找和 scene_preview 重复，见 I 节。
3. 全文件是压缩排版（分号连写、逗号后不空格），重新格式化。行数不一定减少，但能符合“清晰命名，四空格缩进”。

### replay.py（106 行）

1. `render_run` 没有调用方；`render_observations` 只有测试在用，它是引用型训练观测的残留，已随 `--materialize-from` 失效。两者都删，约 −45 行。
2. `render_rollout`（离线回放视频）目前只有测试在用。记忆里记着这件事待用户定：去掉 cli.py 后它没有入口了。**要用户定**：留的话补一个 `scripts/replay.py`，不留就整个文件删掉（约 −106 行，测试约 −97 行）。

### third_person.py（84 行）

压缩排版，需要格式化。可以和 `CameraRig/ViewConfig` 合并到 `cameras.py`。`render` 用 E 节的 `geom_mask` 替换内联的分割代码。它的公开接口被 RL 用到，保持不变。

## 5. data/visualization/

### benchmark.py（222 行）

1. **`_plotting()` 每次调用都执行 `matplotlib.use('Agg')`**（`:12-16`），heatmap.py 里又内联了一遍（`:28-30`）。改法：新建 `visualization/plot.py`，在模块顶部做一次 `matplotlib.use('Agg')` 并 import pyplot，同时放 `save_figure()`、`grid_extent(grid)`。网格 extent 的计算在 `benchmark._map:37-38` 和 `heatmap:37-38` 重复，也可以直接做成 `Grid.extent` 属性。
2. **`_map()` 有 60 行**（`:30-90`）。拆成 `_draw_grid_and_region`、`_draw_high_stations`、`_draw_start_target`、`_draw_trace` 四个函数。
3. `_facts` 里的 `'labels' in evidence` 分支已经失效（同 2-B），删掉。
4. `_summary(…, results=None)` 用 None 区分两种用途。拆成 `_benchmark_summary`、`_evaluation_summary` 两个函数，各自十来行。
5. `if __name__ == '__main__'` 的 CLI（`:214-222`）删掉：lastmilebench 已经会调用这两个出图函数。需要单独出图时走阶段脚本。
- 本文件合计删减约 20 行。

### heatmap.py（83 行）

1. `export_heatmap` 有 55 行，绘图和存数值混在一起。拆成 `_save_values()` 和 `_plot()`。
2. `heatmap.json` 里有 8 个纯说明性的字符串字段（`'kernel'`、`'overlap'`、`'obstacle_mask'`、`'unknown_is_not_zero'`、`'physical_navigation_validation'` 等），这是在用代码写文档。只保留 `sigma_m`、`max_support_distance_m`、`support_obstacles` 和文件名，说明挪到 `docs/data-format.md`。删减约 6 行。改输出格式之前要问用户。

### scene_preview.py（319 行）

1. **兼容旧记录的兜底**：`edit_summary`（`:24-29`）同时读 `record['edits']` 和顶层的 `placements/target_moves`。`before_scene_location`（`:32-44`）在记录没写 `before_scene` 时按目录名猜路径。现在的构建是自己写记录，应该让 scene_datasets 总是写出 `edits` 和 `before_scene`（要和第 1、3 路确认），然后删掉这两个函数里的兜底和猜测，约 −15 行。另外 `placement.py:22` 里的 `target_moves` 恒为 `[]`，“目标移动”这项已经是死字段，可以一起去掉（第 3 路）。
2. **`render_scene_preview` 有 90 行，里面有闭包 `load()`**（`:196-200`，捕获了 `stack/root/task`，还在函数体里 import）。改法：
   - 拆出 `_open_snapshot(stack, root, location, target)`；
   - 拆出 `_fit_rigs(after, before, record) -> (top, side)`，包括按 4 个方位角挑侧视角；
   - 拆出 `_draw_sheet(...)`。
3. **`observer()` 每调用一次就新建、销毁一个 `mujoco.Renderer`**（`:116`）。每个场景要调用 6–8 次：4 次挑角度，加上 2–4 次正式渲染。改成每个 sim 只建一个 renderer 传进去，代码更清楚，预览也更快。
4. `observer` 里 `except IndexError` 的分割兜底（`:130-134`）用 E 节的 `geom_mask` 替换。
5. 字体：`font(size)` 每次调用都要遍历路径并检查 `exists`，`cjk_available()` 每画一个标签也查一次。用 `functools.cache` 缓存，并和 analysis_video 的字体查找合并成 `visualization/fonts.py`。删减约 8 行。
6. `export_scene_previews` 里逐条记录的 `except Exception`，是 AGENTS.md 允许的“批量失败隔离”，保留。
- 本文件合计删减约 40 行，结构会清楚很多。

### sac_monitor.py：RL 文件，不改

## 6. RL 文件对 data/ 的依赖（重构时要保持不变的接口）

| RL 文件 | 依赖 |
|---|---|
| `rendering/rl_video.py` | `recorder.video_writer`；`third_person.ThirdPersonCamera` 和 `ThirdPersonConfig(context_margin_m=…)`；`ThirdPersonCamera.render(sim_like, renderer, w, h)`，其中 sim_like 只有 `model/data/target_id/robot.config.namespace`；`.metadata()` |
| `rendering/rl_capture.py` | `rl_video.render_rollout` |
| `evaluation/station_q.py` | 只依赖 torch 和 scipy |
| `visualization/sac_monitor.py` | （未细读）只读 RL 训练日志 |
| `workflows/rl_evaluation.py` | `station_q.*`、`rl_capture.RolloutCapture` |

如果 `third_person.py` 并入 `cameras.py`，原路径要么保留一行 re-export，要么修改 rl_video 的 import 行（规矩允许改 import）。`video_writer` 如果随 recorder 搬家，同样只需要改 import 行。

## 7. policies/（自己写的那层）

### G. 两个策略进程：服务端共用、客户端改为组合

1. **两份服务端骨架几乎一样**：`molmobot_service.main()`（70 行）和 `pi05/service.main()`（42 行），都是“prctl 父进程退出时自杀 → 检查 getppid → 带认证的 Listener → accept → 发 ready → 循环（attempt 变了就按 seed 重置；每个请求出错都把错误包成 dict 回传）”。两份的差别只在：日志初始化（`setup_logging` 和 `basicConfig`）、错误回包字段（pi05 少了 `invalid_action`），以及 accept 和模型加载的先后顺序。
   - 改法：新建 `policies/serving.py`：
     ```python
     class PolicyRunner(Protocol):
         def ready_info(self) -> dict: ...
         def reset(self, seed: int) -> None: ...
         def infer(self, request: dict) -> dict: ...

     def serve(socket: str, load: Callable[[], PolicyRunner]) -> None:
         ...  # prctl、监听、加载、逐条处理，错误回包统一
     ```
     两个 service 只剩“加载模型、构造 runner”这一部分，各自 20–30 行。
   - 删减约 35 行。风险中：模型加载和 accept 的先后会影响启动超时，要保持各自原来的顺序（`load` 在 accept 前还是后，可以作为参数）。
2. **用错误字符串判断异常类型**：`str(exc) == 'invalid_model_action_chunk'`（`molmobot_service.py:108-111`）。定义 `class InvalidModelChunk(ValueError)`，用 isinstance 判断。
3. **函数体里的 import**：`import random`、`ctypes`、`torch`。其中 `random/ctypes` 挪到顶部；torch 和 multitask 要在 prctl 之后才加载，可以保留，或者放进 `load()`。
4. **客户端继承**：`Pi05Client(MolmoBotClient)` 靠覆盖 `load_config/service_module/policy_name/prepare_environment` 再改写 `action` 来复用，其实 `MolmoBotClient` 就是一个通用的“策略子进程 RPC”。
   - 改法：
     - 把 `molmobot.py` 改名为 `policies/process.py`，类名 `PolicyProcess(config, service_module, name, env_overrides)`，负责启动、`request(dict)`、`receive`、`close`；
     - `MolmoBotClient` 和 `Pi05Client` 都持有一个 `PolicyProcess`（组合）；
     - `prepare_environment` 这个钩子改成数据参数 `drop_env=('LD_LIBRARY_PATH',)`。
   - `close()` 里的 `getattr(self, 'process', None)` 是多余的：`process` 在 try 之前就已经赋值。
   - `receive()` 里函数内 import `classify_episode_exception`，挪到顶部；如果有循环引用，说明 lifecycle 应该下沉到更底层的模块。
5. **返回值是裸 dict**：两个客户端的 `action()` 返回同一套 10 个键（`action/raw_action/new_chunk/chunk_index/chunk_serial/inference_s/processed_images/model_prompt/model_chunk_state/conditioning_points`）。改成 `@dataclass PolicyAction`，由客户端把服务端回包转换成它。读取这些键的是 `runtime/execution/vla.py`（第 2 路），要一起改。
6. `action()` 的 docstring 里有 8 行修 bug 的经过（`molmobot.py:86-94`），压成一句“timeout 固定为 rpc_timeout_s，与尝试预算解耦”，经过留在报告里。
- 第 7 节合计删减约 50 行。

### config.py（75 行）

1. **`load_policy_config` 和 `create_policy` 各写了一遍 if 分支**，`backend()` 还要单独再解析一次 JSON。改成一张表：
   ```python
   BACKENDS = {'molmobot': (load_molmobot_config, MolmoBotClient), 'pi05': (load_pi05_config, Pi05Client)}
   ```
   pi05 的 config 和 client 都不依赖 jax（jax 只在服务进程里加载），可以在顶部 import。
2. **`check_profile` 用 else 隐式表示 pi05，又用 `hasattr(config, 'embodiment')` 兜底**（`:69-72`）。改成显式的 `elif operation_backend == 'pi05'`，其余情况抛 ValueError，`hasattr` 就不需要了。
- 删减约 10 行。

### pi05/

1. `inference.normalize`（`:48-63`）和 `data_config.Rby1Dataset.__getitem__`（`:50-54`）各写了一遍 quantile 归一化，公式是等价的。抽成 `pi05/normalization.py` 里的 `normalize(values, stats, mode, inverse=False)`。删减约 8 行。
2. `Rby1DataConfig(values: dict)`（`data_config.py:13-16`）里装的是整份裸 dict，实际只读了 `repo_id/fps/action_horizon/norm_stats/task_prompts/images` 六个键。改成显式字段的 dataclass。它目前只有测试在用，应该是给外部 openpi 训练用的，**确认还要不要**。
3. `Pi05Inference(require_stats=False)` 和 `infer(normalized=True)` 这两个“只做归一化冒烟”的开关，只有 `tests/smoke/pi05.py` 在用。如果冒烟不再需要，删掉约 10 行。
4. `actions.py` 里分两行 import 同一个模块，合成一行。`observation.py` 里的相机名和第 3 节的 `PI05_CAMERAS` 合并。
5. `core/sharding.py` 只有一个恒等函数，属于移植代码，可以不动。

### multitask/ 和 pi05/core/（移植代码，粗看）

- 按名字扫描，没有被引用的函数有：`crops.setup_pil`、`policy._BufferedPolicy.prepare_model`、`vision._expand_token`、`action_expert.apply_compile`、`llm.get_causal_attention_bias`、`connector.Residual`、`connector.reset_connector_parameters`、`backbone.get_default_model_name`、`pi0_config.get_freeze_filter`、`model.fake_act`、`nnx_utils.state_map`。可以删，估计 −80 行；要逐个确认没有经由字符串或配置间接引用。
- `policy.py` 里 `_BufferedPolicy` 只有 `MolmoBotRBY1MultitaskPolicy` 一个子类；Pick 和DoorOpen 两个 Config 子类只是改了默认字段。可以合并成一个类，两个配置实例。约 −30 行，需要细读后确认。
- dropout 相关的配置字段要保留：checkpoint 的配置解析需要它们，删掉会让 `strict` 加载失败。`lora.py` 被 gemma 引用，保留。
- `outputs.py`（9 行）、`utils.py`（30 行）、`tensors.py`（54 行）是三个小文件，可以合并，但收益很低，不建议为了这个去动移植代码。

## 8. data/ 的目录建议（交给第 1 路汇总）

```
data/
  evaluation/   calibration.py（由 selection 和 statistics 合并）、benchmark.py、recovery.py、station_q.py(RL)
  export/       lastmile_dataset.py、pi05_dataset.py（CLI 挪到 scripts/）；molmobot_dataset 删除或补入口（待定），state22 挪到 runtime
  rendering/    cameras.py（ViewConfig、CameraRig、ThirdPersonCamera、geom_mask、rendered_camera）、visibility.py（原 head_views 去掉死代码）、
                analysis_video.py、replay.py（待定）、rl_capture.py(RL)、rl_video.py(RL)
  visualization/ plot.py（Agg、保存、extent、字体）、benchmark.py、heatmap.py、scene_preview.py、sac_monitor.py(RL)
recorder.py → runtime/execution/recorder.py（消除 data 和 runtime 互相引用）
```

## 9. 需要用户拍板的点

1. 离线回放视频 `replay.render_rollout`：补脚本保留，还是删掉？
2. MolmoBot 数据导出 `export_molmobot`：还要不要？
3. π0.5 的训练侧：`data_config.Rby1Dataset`、`load_training_batch` 是否仍由外部训练代码使用？
4. 删除 heatmap.json 里的说明性字段，以及 scene_preview 的旧记录兼容，会改变输出和输入格式，可以接受吗？
5. 动手之前，先把当前没完成的合并（UU/DU 文件）处理掉。
