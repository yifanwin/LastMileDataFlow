# 第 3 路：planning/ 与 scenes/ 审查

> 2026-11-12 更新：用户改了决定，『RL 文件为达到目标，可以做修改』。本文中“RL 文件只改 import 行、不搬、不动”的限制已作废，以方案 02 第四节第 7 条为准。

审查对象：pro5000 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify`（分支 refactor/simplify，HEAD 6b6b149）。只读审查，没有改代码，也没有运行任何程序。

## 0. 先说两件事

1. **工作树里有一次没做完的合并**：`git status` 显示 `UU planning/pool.py`、`UU runtime/execution/workers.py`、`UU workflows/scene_datasets.py`、`UU reports/.../01-handover.md`、`DU workflows/collection.py`。看样子是有人正在把 actor-critic 合并进来，冲突还没解决完。我读到的 pool.py 里没有冲突标记，但动手重构前，一定要先确认这次合并由谁、在什么时候收尾，避免互相覆盖。
2. **RL 对这两个目录的依赖**（这些文件 RL 只允许改 import，所以下面这些接口要保持可用，或者只做搬家）：
   - `scenes/stations/rl.py` 依赖 `stations.initialization.floor_support/room_triangles`、`stations.sampling.disk_samples/robot_contacts`、`geometry.body_pose/descendants/geom_points`、`tasks.discovery.local_bounds`
   - `scenes/loading/rl_scene.py` 依赖 `loading.local.task_local_model`、`loading.source.SceneSource`（`local.py` 现在**只有 RL 在用**）
   - `runtime/rl/*`、`workflows/rl_*`、`policies/sac/*` 依赖 `scenes/rl_observation.py`、`scenes/stations/rl.py`、`scenes/loading/rl_scene.py`
   - RL 不直接 import planning/。

## 1. 跨文件的共性问题（收益最大，建议优先做）

| # | 问题 | 位置 | 改法 | 预计删减 | 风险 |
|---|---|---|---|---|---|
| X1 | **head_fov 整套功能从没启用过**：所有 configs 都写的 `head_fov_enabled: false`，dataclass 的默认值却是 `True`（execution_config.py:28），两者还互相矛盾 | head_fov.py 全文 208 行；costs.py:56-60、104-124、158-171；planner.py 里约 70 行（`_setup_fov`、`_check_head_camera_fk`、`validate_fov_result`、`_fov_target`，以及 grasp/plan/refresh/attach 里的 FOV 分支）；tracing.py:35-42；context.py 的 5 个 fov 字段 | 整套删除，同时删 `tests/planning/test_head_fov.py`、`tests/smoke/head_fov.py`；如果想保留，至少要把默认值改成 False | src 约 −340，tests 约 −340 | 属于功能取舍，**需要用户拍板**（RBY 头部视野约束可能还是研究备选） |
| X2 | “足迹是否落在支撑面矩形内（带余量）”这段判断写了 5 遍 | geometry.py:144-146、graph.py:101-105、placement.py:80-84、placement.py:133-138、checks.py:90-95 | 给 `SupportRegion` 加 `contains(points, margin)` 方法，5 处都改成调用它 | −20 | 低（注意各处的 margin 符号不同：有的是 +eps，有的是 −0.01） |
| X3 | “这个 geom 是否算环境碰撞体”（排除 robot_0/、floor、plane、不参与碰撞的 geom）的筛选规则写了 5 遍 | collision_world.py:26-28、astar.py:204-211、initialization.py:48-51（取反，只要 floor）、local.py:32-35、navigation/geometry.py:99-104 | 在 `scenes/geometry.py` 加 `environment_geoms(sim, *, floors=False)` 生成器 | −15 | 低；每处的细节差别（`'floor' in name` 和 `GEOM_PLANE` 两个条件）要逐一对齐 |
| X4 | 点是否落在三角形内的判断写了两份（一份向量化光栅、一份逐点） | astar.py:186-194、initialization.py:29-37 | 统一成一个 `points_in_triangles(xy[N,2], tri) -> bool[N]`，两处都调用它 | −10 | 低（`inside_triangles` 在三角形为空时返回 True 的语义要保留） |
| X5 | 偏航角四元数 `[cos(y/2),0,0,sin(y/2)]` 手写了 4 遍，而 `geometry.quat_yaw` 本来就有 | placement.py:50、143、graph.py:274 | 统一调用 `quat_yaw(yaw).tolist()` | −3 | 无 |
| X6 | “世界坐标 xy 换到基座局部坐标”`np.linalg.solve(base, np.r_[xy, base[2,3], 1])[:2]` 写了 4 遍 | planner.py:286-288、panda_omron.py:95、121、rby1m:113 | 加 `robots/common.py: base_local_xy(base, xy)` | −4 | 无 |
| X7 | “承重接触”判据（`dist` 在容差内、法向 >0.7、力 >1e-8）写了 3 遍 | graph.py:108-109、252-254、checks.py:84-85 | 抽成 `is_load_bearing(contact, support_tol, penetration_tol)` | −6 | 低 |
| X8 | 偏航旋转后的外接尺寸 `c*w+s*d, s*w+c*d` 写了两遍 | placement.py:125-127、assets.py:328-330 | 抽成 `rotated_extent(width, depth, yaw)` | −4 | 无 |
| X9 | 分层倒置：planning 和 scenes 反过来 import runtime | waypoints.py:5、pool.py、navigation/geometry.py:12-15、sampling.py:8-12、search.py:8-12、local.py:7-8、initialization.py:6、geometry.py:157 | 见第 3 节目录建议；最简单的一步是删掉 X10 里的死函数，这样 waypoints 对 runtime 的依赖自然消失 | — | 搬家会和 actor-critic 合并冲突 |

## 2. 逐文件

### planning/curobo/planner.py（1049 行）

| 行 | 问题 | 改法 | 删减 | 风险 |
|---|---|---|---|---|
| 706-860 | `grasp()` 和 `grasp_batch()` 约 70% 相同：分阶段取点、diagnostic 字典里的公共键（reference_world、control_dt_s、time_dilation、joint_names、两个 `*_ref`）、`finally` 里的五行清理 | ① `@contextmanager def _trial(self, phases, on_query)` 统一处理 phases、on_query 和 finally 清理；② `_stages(result, slot, fields)` 统一取点；③ `_common_diagnostics()` 返回公共键；两个方法各剩 15–20 行 | −60 | 中；输出 JSON 的键要保持不变 |
| 744-765、1014-1041 | `failure_reason`、`status` 用三层嵌套的三元表达式，在 grasp 和 plan 里各写一遍 | 拆成 `_failure_reason(success, fov_failed)`；删掉 FOV（X1）后只剩一行 | −15 | 低 |
| 72-149 | `reset_motion_seed` 和 `bind_batch_seeds` 构造 `seed_manager` 的 7 行代码完全一样 | 抽成 `_seed_manager(core, seed)`；`_batch_actions` 的 `batch_size` 参数没有用到，删掉 | −10 | 低 |
| 45-53 | `_compute_target_bodies`、`_compute_handle_bodies` 是只用一次的包装 | 直接内联到 `bind_target` | −8 | 无 |
| 247-270 | `_native_types()` 返回一个装着类的裸 dict，`native["..."]` 一路往下传 | 改成模块级的 `_import_curobo()`，只负责检查 CUDA 和抛 `PlannerUnavailable`；需要类的地方直接局部 import（文件里其他地方本来就是这么做的） | −15 | 低 |
| 296-331 | `_environment_world` 和 `_target_worlds` 里三次调用 `collision_world(self.sim, Path(path), self.world_range, reference_base=self.base, ...)` | 抽成 `_world(path, **kw)` | −12 | 无 |
| 335 | 用 lambda `checker` 构造 SceneCollision | 改成方法 `_scene_checker(world)` | 0 | 无 |
| 581-639 | `rebind_resources` 60 行，把“更新所有 rollout、LM-IK 限位、graph planner 采样器的运动学参数”和“换绑场景”混在一起 | 拆出 `_update_kinematics(parameters)` | 0（可读性） | 低 |
| 544-557、681-704 | `validate_fov_result` 和 `points()` 里“reorder → 截断 last → 按 names 排序”这段重复 | 抽成 `_ordered_positions(trajectory, last)` | −8 | 无 |
| 全文 | 每建一次 planner 就写 8–12 个小 JSON（planner.yml、planner_identity、mobile_base、fk_check、target_query_range、planner_reuse、carried_geometry、grasp_inputs、grasp_result、batch_grasp_result、solver_trace、head_camera_fk_check） | 合并成一个 `planner.json`（identity + mobile_base + fk_check + query_range），每次查询只写 grasp_result/solver_trace | −40 | **会改变输出文件**，需要用户同意；AGENTS.md 本身也反对堆积附件 |
| 152-199 | `PlannerResources.acquire` 的 finally 里用 `any(planner is item ...)` 判断是否被缓存；head_fov 打开时不进缓存 | 删掉 FOV 后不再有“不缓存”这条分支，改成直接看 `key in self.planners` | −5 | 低 |

### planning/curobo/tracing.py（136 行）
- 34、52：`lane_success(...)` 算了两次，只有 FOV 否决时两次结果才会不同。删掉 FOV 后合成一次。−3
- 54：`setdefault` 之后又写了一次 `kwargs.get('max_attempts', 5)`，改成 `kwargs['max_attempts']`。
- 动态子类 `native_grasp_planner_type` 是 cuRobo 的类型约束逼出来的，保留。

### planning/curobo/collision_world.py（97 行）
- 全文是用分号压成一行的稠密写法，和仓库其他文件风格不一致。按 black 重排，并把 33-49 行的“按 geom 类型细分网格”拆成 `_geom_mesh(model, g)`。行数会略增，但可读性明显提升。
- 72 行的 `reach` lambda 改成局部函数或预先算好的数组。
- 84-91 两个 `write_json` 分支只差几个键，合成一个 report 字典。−6
- 筛选规则统一按 X3 处理。

### planning/curobo/costs.py、context.py
- `manager_type` 和 `planner_context` 这套类属性写法是 cuRobo 接口逼出来的，注释也写得清楚，保留。
- 删掉 X1 后，costs 少 45 行，context 少 5 个字段。

### planning/evidence.py
- 45-46：`ik_feasible_ratio: None`、`ik_denominator: 'unavailable_selected_results'` 是常量字段，每一行都重复写。可以删（会改输出），−2。

### planning/waypoints.py（49 行）
- 28-49：`arm_waypoint_action`、`navigation_action` 在 src 里**没有任何调用**，只有 tests/planning/test_navigation.py 在用。删掉它们和对应的测试后，waypoints.py 就不再 import `runtime.control.action`，分层倒置也随之消除。−22
- `PlanResult.status` 用字符串元组校验，可以改成 `Literal[...]` 或一个小 Enum。

### planning/pool.py（338 行，**处于合并冲突状态**；按用户的决定只做内部精简）
| 行 | 问题 | 改法 | 删减 |
|---|---|---|---|
| 45-181 | `PlanningService.execute` 有 135 行 | 拆成 `_grasp_lanes(...)`、`_pose_lanes(...)`、`_responses(...)` 三个函数 | 0（可读性） |
| 100 | 用 `SimpleNamespace(sim=sim, config=config)` 伪造一个 ctx，只为调用 `candidate_pool` | 把 `candidate_pool` 的签名改成显式的 `(sim, config, ...)`（这个函数在 runtime，需要和第 2 路协调） | −1 |
| 56-150 | `request.inputs` 是一个有十几个键的裸 dict（task/config/root/assets_dir/candidates/side/phase/attached/goal/preparation_path/recording_step），lanes 也是 dict | 在 protocol.py 里定义 `PlanningInputs` 和 `GraspLane` 两个 dataclass | ±0，可读性提升明显 |
| 202-214 | `except Exception` 之后再用 `isinstance` 分流 | 拆成 `except (TimeoutError, OperationFailure)` 和 `except Exception` 两个分支；后者属于批量实验的失败隔离，保留 | −3 |
| 243-255 | `workers_per_gpu` 既接受 int 又接受列表，还做了范围校验 | 在配置边界统一转成 tuple，这里只接受 tuple | −6 |
| 18-25 | `_consume_plan_budget` 靠函数内 import 来绕开循环依赖 | 等 X9 搬家之后改成顶层 import | 0 |

### planning/remote.py（102 行）
- `PlanningClient.__init__` 有 8 个参数 → 改成 `@dataclass`；`RemotePlanner` 和 V2Planner 共用的那一组属性（model、names、base、h_step_limit、base_target_world）可以用 `robots/common.py` 里的 `Protocol` 写成文档。−5
- `refresh_world: pass` 是接口占位，保留。

### planning/robots/
- `rby1m_mobile_torso.py:7` 的 `V080_SHA` 没有任何地方引用，删掉。−1
- `rby1m:21-40` 对 torso 关节有两次循环，可以合成一次。−5
- `panda_omron.py:27-51` 的 `configuration(robot, fixed=True, mobile=False)`：src 只用到 `mobile=` 这一种；`fixed=False`（FULL_NAMES 分支）只有一个测试在用，locks 还会被算两次。收成 `configuration(robot, *, mobile)`，测试也一起改。−10
- 两个 `PlanningModel` 接口相同，却没有任何声明。在 common.py 加一个 `class PlanningModel(Protocol)` 写清属性和方法，可以替代各处的 `getattr(model, 'handle_contact_links', ...)`（planner.py:344）。
- `common.minimum_mesh_query_dimension` 只有一处调用，可以内联进 planner。−5

### planning/navigation/astar.py（265 行）
| 行 | 问题 | 改法 | 删减 | 风险 |
|---|---|---|---|---|
| 36-61 | `search()` 在 src 里没有调用，只有测试和 smoke/panda_omron_flow 在用 | 删掉，同时改测试 | −27 | 低 |
| 36-140 | `search`、`distance_field`、`search_region` 三份 Dijkstra/A* 主循环 | 剩下两份共用一个 `_dijkstra(grid, start, limit=inf) -> (distance, parent)` | −20 | 低 |
| 197-201 | 闭包 `occupy` 修改外层的 `occupied` 数组 | 改成模块函数 `_mark_box(occupied, xy, lo, hi, res)` | 0 | 无 |
| 229-265 | `task_support_obstacles` 是任务语义（catalog、父实例提示），放在 planning 里不合适 | 搬到 `scenes/tasks/`（和 discovery 放在一起）；返回的障碍物字典可以考虑改成 `SupportObstacle` dataclass，但它会写进任务 JSON，所以要保留 `to_dict` | 0 | 搬家要改 5 处 import |
| 13、149 | `Grid.support_obstacles`、`OccupancyMap.support_obstacles` 疑似只存不读 | 确认没人读之后删掉 | −3 | 低 |

### planning/navigation/geometry.py（336 行）
- 依赖 runtime.construction、runtime.outcomes、scenes.stations，本质上是“在 sim 克隆体上做探测的执行期导航”，放在 planning 里分层不对，建议搬到 `runtime/execution/navigation.py`（那个文件现在只有 41 行，可以并进去）。
- 29-33：`NavigationSettings.from_config(config, reference)` 用 `vars(config)` 的字典把戏取速度 → 改成显式的 `from_config(config, *, speed, yaw_speed)`。
- 118-134：`hold()` 和 `compatible()` 都在算“去掉底座字段后的初态” → 抽成 `_posture(initial)`。−5
- 241-244：`_heuristic` 手写了 yaw_gap 公式，改成复用 `yaw_gap`（向量化）。−2
- 317-336：`plan()` 返回 `{'path','reason',...}` 裸字典，`pose_path` 也返回字典 → `NavigationResult`、`PosePath` 两个 dataclass，序列化时 asdict。±0
- 187-197：`check_edge` 里双向写缓存的代码写了两遍 → 抽成 `_cache_edge(key, result)`。−3

### scenes/geometry.py（163 行）
- 133-148：`placement_pose` 没有任何调用，删掉（quat_yaw 留着给 X5 用）。−16
- 156-158：`tcp_pose` 是对 runtime 的反向包装，只有 planner.check_fk 在用 → planner 直接调用 `runtime.control.interface.tcp_matrix`。−4
- 加 `SupportRegion.contains`（X2）、`environment_geoms`（X3）、`points_in_triangles`（X4）。
- 和 collision_world 一样有分号压行的写法（11、42、66、69-71、152），按 black 重排。

### scenes/dataset.py + dataset_config.py
- `dataset.select_stations` 只有 smoke 在用，删掉。删完 dataset.py 只剩 6 行的 `load_scenes`，把它并进 dataset_config.py，再把文件改名为 `scenes/dataset.py`，两个文件合成一个。−15
- `SceneRecipe.local_task_models`（104、136）只允许取 False，等于死字段，删掉。−3
- 132-133：`radius_m != 1 or spacing_m != .25` 就报错，于是 `SceneStationConfig` 里这两个“可配”字段实际上是常量。二选一：要么删掉这条硬校验，要么把它们改成常量。
- 97、154-156：`station_config: dict` 加 `geometry` 属性，每访问一次就重新构造一个 SceneStationConfig → 改成字段 `station: SceneStationConfig`，在 `__post_init__` 里转换（和 `edits` 的处理一样）。`settle: dict` 同理改成 `SettleConfig`（search.py:94 本来就会转）。
- 116-129：四段 `isinstance` 列表校验 → 抽成 `_require_names(value, what)`。−8
- 删掉 `sample_stations` 之后，`SceneStationConfig.require_target_visibility`、`require_navigation_clearance` 在 scenes 里就没人读了（execution_config 里另有同名字段），可以删。−2

### scenes/graph.py（279 行）
- 117-279：`build_scene_graph` 有 160 行 → 拆成 `_catalog_nodes`、`_world_surface_nodes`、`_part_and_region_edges`、`_contact_pairs`、`_support_observations`，主流程只剩约 15 行。0（可读性）
- 56-63：`SceneGraph.from_dict` 没有调用，删掉。−8
- 76：`_hull` 里嵌套的 `cross` 提到模块级。
- 承重判据按 X7、区域判断按 X2 处理。−10

### scenes/tasks/discovery.py（192 行）
- 45-133：`discover_tasks` 有 90 行 → 拆成 `_pick_task(sim, instance, ...)` 和 `_open_tasks(sim, instance, obj, ...)`。0
- 136-192：`grasp_candidates` 有两条完全独立的路径 → 拆成 `_dataset_grasps(...)` 和 `_pinch_grasps(...)`；候选是 `{'pose_local','body','source','row'}` 裸 dict，可以改成 `GraspCandidate` dataclass（runtime/pool 的消费方要一起改，和第 2 路协调）。
- `allow_fallback` 参数和 `task['require_data_grasps']` 标志表达的是同一件事（search.py:89 往 task 里写这个标志，再由下游读出来）。统一成显式参数，不再往 task 字典里塞控制标志。−3
- 26-42：`robot_footprint` 返回 dict，调用方只取 `['radius_m']` 和 `['max'][2]` → 改成 `Footprint` dataclass。±0
- task 本身是贯穿全链路的裸 dict，还会写进 scenes.jsonl，RL 也在读。**不建议**整体改成 dataclass；最多加一个 `TypedDict` 当文档。

### scenes/loading/
- **mjcf.py:23-56**：函数里定义了异常类 `NativeRequired`，再用闭包 `expand` 修改外层的 `includes`、`seen`，并且靠异常做流程控制，同时违反了用户的三条规范。改法：先用模块级的 `_needs_native(root_path)` 扫一遍所有 include 文件里的 compiler 目录覆盖，需要就直接走 native；再把 `_expand(element, current, root_path, includes, seen)` 写成显式传参的模块函数。±0
- **assets.py:121-159**：`EditAssetCatalog`（select/describe）在 src 里没有调用，只有 tests/runtime/test_construction.py 在用。删掉之后，`RuntimeGeometryCache` 的 `base_dir` 参数也没用了。−45
- assets.py:79-80、112-115：`measure_asset` 和 `geometry()` 返回的是 record 加 spec/model/data/body/local_points 拼起来的大 dict → 改成 `AssetGeometry` dataclass（字段：record、spec、model、data、body、local_points）。±0
- assets.py:291-301：`AssetRequest.room`、`yaw_rad`、`size_bounds`、`placement_margin_m` 只有测试在设置；`select` 里还有一个隐藏的上限 `min(5, request.limit)`。删掉这些字段和对应的过滤分支，上限只保留 limit。−15
- assets.py:67：用 `SimpleNamespace(model, data)` 伪造 sim 去调用 body_points，可以接受；更干净的做法是让 `collision_geoms` 和 `geom_points` 直接接收 `(model, data)`。
- source.py 和 local.py：没有明显问题；local.py 只有 RL 在用，加一句注释说明即可。

### scenes/stations/
| 行 | 问题 | 改法 | 删减 |
|---|---|---|---|
| sampling.py:158-212 | `sample_stations` 在 src 里没有调用（只有测试和 smoke 用） | 删掉 | −55 |
| sampling.py:215-224 | `record_station_visibility` 完全没有引用 | 删掉 | −10 |
| sampling.py:32-42 | `yaw_variants` 只有测试在用 | 删掉 | −11 |
| sampling.py:59-102 | 删掉 sample_stations 之后，`initialize_station` 的 renderer、`check_visibility`、`evidence_path` 分支就没人走了（唯一的调用方传的是 `check_visibility=False`），`head_frame` 的 import 也可以一起删 | 精简成纯几何判断 | −20 |
| sampling.py:62-79、105-125、128-155 | `initialize_station`、`place_frozen_station`、`park_robot` 都在做“只动机器人 qpos → 恢复物体速度 → mj_forward → 断言非机器人 qpos 没变” | 抽成 `_place_robot(sim, initial, *, aim_anchor=None)` | −15 |
| sampling.py:227 | `select_task_stations(ctx, ...)` 只用到 ctx 的 `recipe.max_final_stations` 和 `station_config` | 改成显式参数 `(sim, geometry, config, limit, *, measure_visibility)` | 0 |
| initialization.py | `room_triangles` 在同一个 sim 上会被反复计算（floor_height、park_robot、sampling 每次都重算） | 想优化的话，可以在 TaskGeometry 里缓存一次；不是必须 | 0 |

### scenes/edits/
**search.py（244 行）**
- 71-136：`BuildContext` 没有 `__init__`，靠 `prepare` 类方法往 `cls()` 上逐个挂属性，还用 `except BaseException: close; raise` 兜底。改法：写成 `@dataclass class BuildContext`，显式列出 recipe、station_config、settings、baseline、geometry、selector、assets、session、settle_config、grasp_evidence、timings、camera_calibration、baseline_snapshot；构造函数 `prepare_build(source_sim, task, recipe)` 内部用 `contextlib.ExitStack`，成功时 `pop_all()`。
- `ctx.parking`、`ctx.profile`、`ctx.grasps` 写了却没人读，删掉。−3
- 157-200：`build_natural_task` 和 `_freeze_candidate` 重复了“给每个站位写 clearance_base_m”的循环和 accepted 记录里的 8 个键 → 抽成 `_accepted_record(geometry, stations, sampling, scene, edits, checks, metadata)`。−12
- 159、241 和 169-172、243-244：prefix 和记录头（house、task_id、seed、source_split、dataset_split）各写了两遍 → 抽成 `_record_header(ctx, house, seed)`。−6
- 164-168：natural 的 metrics 里有 `cheap_rejected`、`physical_candidates`、`proxy_initializations` 等恒为 0 的历史字段，可以删（会改输出）。−3
- 139-154：`_freeze_natural_baseline` 的返回值没人用；177 行又重新推一遍“是否复用了缓存” → 让它直接返回 `reused`。−3
- 188-190 有多余空行。

**placement.py（191 行）**
- 11-22：`AddObject(placement: dict)` 只是包了一层 dict，`BuildProposal.runtime_changes()` 每次都返回 `'target_moves': []` → 去掉 AddObject，改成 `Placement` dataclass（asset_id、category、kind、point_xy、yaw_rad、pose、support_height_m、support_geom、support_instance、world_bounds，以及可选的 measured_size_m 等），`BuildProposal.placements: list[Placement]`，**target_moves 整条链删掉**（checks.proposal_scene 里的 moves 代码，以及 runtime `session.candidate(target_moves=)`，后者要和第 2 路协调）。−25
- 153-155：`isinstance(edit, AddObject)` 和 `profile_proxy` 判断永远为真，是死分支。−4
- 161：`SimpleNamespace(placement_gap_m=.003)` 只是为了传一个浮点数 → 改成模块常量 `PLACEMENT_GAP_M`，`sample_placement` 不再接收 config。−2
- 65-94：`sample_placement` 用 `region is None` 分成地面和台面两条路，`point` 参数也是 dict → 拆成 `_ground_placement(...)` 和 `_surface_placement(...)`，`point_id` 和 `xy` 显式传入。±0
- 97：`role_profiles` 的 `size_bounds` 参数从来没人传，删掉。−1

**checks.py（188 行）**
- 12：`collision_pairs` 的 `settled` 参数没有用到；`body_names` 的默认分支也永远不走（调用方总会传）→ 删掉这两个参数默认值相关的代码。−3
- 42、73：“这个 geom 是不是这块摆件的正确支撑”判据写了两遍 → 抽成 `_is_support_geom(model, geom, placement)`。−3
- 82：`placement.get('reference_up', [0,0,1])` 里的 `reference_up` 从来没有被设置过 → 改成常量。−1
- 108-111：在 for 循环里定义函数 `identity` → 提到模块级 `_geom_identity(model, g)`。
- 144-150、162：`PhysicalLimits` 只用默认值实例化 → 改成模块常量 `LIMITS = PhysicalLimits()`，或者并进 `SceneEditConfig`。
- 153-188：`proposal_scene` 里 no-edit 分支（placements 为空）实际走不到（natural 构建不经过这里，tabletop 至少放 1 个），moves 也恒为空 → 删掉；两层嵌套的 try/finally 换成 `ExitStack`。−15

## 3. 目录建议（只针对这两个目录，供第 1 路汇总）

- `planning/` 只保留真正的“规划算法 + cuRobo 适配”：`curobo/`、`robots/`、`navigation/astar.py`（纯栅格算法）。
  - `navigation/geometry.py`（在 sim 克隆体上做探测的导航）→ 搬到 `runtime/execution/navigation.py`。
  - `pool.py` 和 `remote.py`（GPU 进程池和 RPC）按用户的决定留在原处，但它们本质上是执行基础设施；以后如果继续调整，可以考虑放到 `runtime/execution/planning_pool.py`。
  - `waypoints.py` 删掉两个死函数后只剩 `PlanResult` 和一个常量，可以并进 `robots/common.py`，或者改名为 `planning/result.py`。
  - `evidence.py` 只给 tracing 用，可以并进 `curobo/tracing.py`。
- `scenes/`：`dataset.py` 和 `dataset_config.py` 合成一个文件；`astar.task_support_obstacles` 搬到 `scenes/tasks/`；`local.py`、`rl_scene.py`、`stations/rl.py`、`rl_observation.py` 这些只有 RL 用的文件，等 RL 定型后可以集中到 `scenes/rl/`（这次不动）。
- `scenes/edits` 和 `runtime/construction` 互相紧耦合（ObstacleEditSession、matched_control、settle_scene 都在 runtime 里）。可以考虑把 `runtime/construction/` 整体并进 `scenes/edits/`（场景构建本来就是 scenes 的职责），这样 scenes→runtime 的反向依赖就少了一大块。这件事要和第 1、2 路的结论一起定。

## 4. 预计删减汇总

| 范围 | 不删 head_fov | 删 head_fov |
|---|---|---|
| planning/ src（约 4,400 行） | 约 −290 | 约 −630 |
| scenes/ src（约 3,900 行，不含 RL 文件） | 约 −330 | 同左 |
| 相应测试/smoke | 约 −250 | 约 −590 |

需要用户决定的：① head_fov 删不删；② 要不要合并 planner 写出的那些小 JSON、删掉恒为 0 或常量的输出字段（会改输出）；③ 搬家类改动（navigation/geometry、task_support_obstacles、construction）是现在做，还是等 actor-critic 合并完再做。
