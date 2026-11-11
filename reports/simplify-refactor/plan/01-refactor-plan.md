# LastMileDataFlow 简化重构计划

- 日期：2026-11-11
- 分支：`refactor/simplify`，从 `actor-critic` 的 `1b2f454` 拉出，worktree 在 `.worktrees/simplify`
- 状态：active；阶段 0–2 已完成，阶段 7 的控制、策略配置、本体公共工具已实施；阶段 3–6 及其余收尾待完成
- 替代：无
- 关联：../delivery/01-handover.md、../delivery/02-control-policy-refactor.md

## 1 范围（已和用户确认）

| 项 | 决定 |
|---|---|
| 主线 | 只保留论文图里的 (a) 场景输入 → (b) 场景准备 → (c) 站位图 → (d) 数据产物 → (e) 数据使用 |
| (c) 站位图 | 规划器成功率法（cuRobo）暂时保留；RL 是新的研究主线 |
| RL 代码 | 这次逻辑一行不动，只允许改 import 行 |
| (a)(b) 场景构建 | 只留资产与场景加载、找目标物、障碍物编辑（桌面和地面两类）、预览可视化、多进程构建；删掉八类任务模式 |
| (d) Last-mile 数据集 | 采集直接产出自包含数据集，删掉引用型格式和 `--materialize-from` |
| (d)(e) Benchmark 和策略 | 保留最简逻辑；MolmoBot、π0.5 都先保留 |
| 本体 | Panda 和 RBY-1 都保留 |
| 入口 | 删掉 `cli.py`，每个阶段一个脚本 |
| 文档 | `reports/` 全部保留 |
| 代码规范 | 少用 try/except，单一职责，相同逻辑复用，依赖显式传递不靠闭包，用 dataclass 不用裸 dict，不加没有实际价值的封装，尽量保持行为和输出不变 |

## 2 现状

- `src/` 共 203 个模块，约 2.6 万行。其中第三方模型代码约 5.9K 行（`policies/multitask` 3.9K，`policies/pi05/core` 2K），RL 自身代码约 2.5K 行。
- 去掉这两部分，需要精简的自有代码约 1.76 万行。
- 五个 CLI 命令的 import 闭包是 115 个模块、1.6 万行。RL 的闭包是 62 个模块、8.6K 行，多出来的部分是顺着 import 链带进来的旧代码。

## 3 总体结构：保留六个领域目录，在目录内部做精简

RL 文件分散在各个领域目录里：

- `scenes/loading/rl_scene.py`、`scenes/stations/rl.py`、`scenes/rl_observation.py`
- `runtime/rl/`
- `policies/sac/`
- `data/rendering/rl_*.py`、`data/visualization/sac_monitor.py`、`data/evaluation/station_q.py`
- `workflows/rl_*.py`

如果把顶层目录换成新的结构，这些 RL 文件就得搬家。这和“RL 只改 import 行”冲突，以后合并 `actor-critic` 也会很麻烦。所以这次保留包名 `lastmile_dataflow` 和 `scenes/runtime/planning/policies/data/workflows` 这六个目录，“大改”落在目录内部：删文件、合并零碎的小文件、重写臃肿的流程，不新建平行目录。

流水线各环节对应的代码如下：

| 环节 | 入口脚本 | 编排 | 算法与执行 |
|---|---|---|---|
| (a)(b) 场景构建 | `scripts/build_scenes.py` | `workflows/scene_datasets.py` | `scenes/`（新的 `scenes/edits/`） |
| (c) 规划器站位图 | `scripts/station_map.py` | `workflows/station_map.py`（新） | `planning/`、`runtime/execution/`、`data/visualization/heatmap.py` |
| (c) RL 站位图 | `scripts/experiments/{train,score,evaluate}_sac.py`（不动） | `workflows/rl_*.py`（不动） | RL 各文件（不动） |
| (d) Last-mile 数据集 | `scripts/collect.py` | `workflows/collect.py`（新） | `runtime/execution/`、`data/export/lastmile_dataset.py` |
| (d)(e) Benchmark | `scripts/bench_build.py`、`scripts/bench_evaluate.py` | `workflows/lastmilebench.py` | `data/evaluation/`、`runtime/execution/vla.py`、`policies/` |

## 4 各目录的改法

“删”的文件都还在 git 历史里，需要时可以找回。下文所有行数都是估计。

### 4.1 `scenes/`：场景加载、找目标、障碍物编辑、站位采样

**现在的问题：** 障碍物是通过八类模式构造器放进场景的。地面障碍来自 `ground_obstacle` 和 `narrow_space`，桌面障碍来自 `arm_path_collision`（它放在支撑面上）。每类模式都有一套“意图 → 廉价几何判定 → 修复”的流程（`modes/` 10 个文件约 750 行，加上 `build/` 和 `mode_config`），目的是做出带标签的困难样本。用户已经决定不要模式标签，所以这套流程可以整体去掉。

**新做法：** 合成一个通用的障碍物编辑 `scenes/edits/`，共三个文件，约 350 行：

- `placement.py`：在给定区域里生成候选摆放，桌面一种、地面一种。
  - 桌面：在目标所在的支撑面上、目标周围一定半径内放置，不和目标重叠，不超出桌面边缘。
  - 地面：在站位圈和目标之间的可通行地面上放置。
  - 资产选择、按包围盒摆放、精确落位这几步，直接复用现有的 `role_profiles`、`asset_candidates`、`resolve_assets`、`sample_placement` 和 `support_regions`，搬过来并整理，算法不改。
- `checks.py`：物理校验。来自现有的 `augmentation/checks.py` 和 `build/validation.proposal_scene`：初始碰撞、沉降、支撑、对原场景物体的影响、求解器健康。判定条件不变。
- `search.py`：对每个任务，按配置的数量（比如桌面 1–3 个、地面 0–2 个）抽候选，过物理校验，再采站位，满足条件就冻结快照。它是 `build/builder.search_variant` 的简化版：去掉 cheap_validate、repair 和模式意图，保留尝试次数和超时上限，以及每种被拒原因的计数。

No edit（原场景）和 Edit 走同一个入口，区别只在于障碍物个数是不是 0。

| 文件 | 处理 |
|---|---|
| `loading/source.py`、`mjcf.py`、`local.py` | 保留（RL 也用） |
| `loading/assets.py`、`asset_library.py`、`asset_profiles.py` | 合并成一个 `loading/assets.py`，内容是资产索引、按角色选资产、几何缓存（约 250 行） |
| `tasks/discovery.py` | 保留（找目标物和 grasp 候选；RL 用 `local_bounds`） |
| `stations/sampling.py`、`initialization.py` | 保留。去掉“模式提示站位”等只给模式用的参数，RL 用到的 `disk_samples`、`robot_contacts`、`floor_support`、`room_triangles` 签名不变 |
| `geometry.py`、`graph.py` | 保留（RL 用 geometry；graph 给场景沉降用） |
| `dataset.py` | 保留 |
| `dataset_config.py` | `TaskModeRecipe` 改成更简单的 `SceneRecipe`：房屋、操作类型、障碍物数量和区域、站位参数、种子、进程数 |
| `modes/`（10 个文件）、`mode_config.py`、`build/`（5 个文件）、`augmentation/`（4 个文件） | 删除，有用的部分搬进 `edits/` |

### 4.2 `runtime/`：仿真、控制、执行、判定

| 文件 | 处理 |
|---|---|
| `simulation.py` | 保留。RL 用到的方法不改，只删确认没人调用的方法 |
| `construction/preparation.py`、`session.py`、`state.py` | 保留（沉降和障碍物编辑事务） |
| `construction/benchmark_scene.py` | 保留（benchmark 要用） |
| `control/robot.py`、`panda_omron.py`、`panda_robocasa.py`、`interface.py`、`native_actions.py` | 保留（Panda、RBY-1、VLA 原生动作） |
| `control/action.py`、`action_limits.py`、`torso_command.py` | 合并成一个 `control/action.py` |
| `control/dls.py` | 保留（RL 用） |
| `execution/context.py`、`curobo.py`、`manipulation.py`、`navigation.py`、`vla.py`、`preparation.py`、`lifecycle.py` | 保留，按代码规范整理。`lifecycle.py` 里的异常分类压到只剩必要的几类 |
| `execution/checks.py` | 并进 `outcomes/pick.py` |
| `execution/robocasa_tracking.py` | 并进 `context.py` |
| `execution/workers.py`、`protocol.py` | 是否保留由 4.3 的规划池决定 |
| `execution/fixed_pick.py` | **建议删除**（固定抓取后端，目前只有 smoke 测试在用），见第 8 节问题 2 |
| `execution_config.py` | 删掉只给已删功能用的字段：模式、诊断预算、IK probe、planner 复用、固定抓取 |
| `resources.py` | 并进新的 `workflows/station_map.py` |
| `outcomes/` | `contacts`、`pick`、`health` 保留；`evaluation.py` 和 `vla.py` 合并；`pick_state.py` 不动（RL 用） |

### 4.3 `planning/`：cuRobo、A*、本体规划模型

| 文件 | 处理 |
|---|---|
| `curobo/planner.py`（748 行） | 保留核心规划。删掉诊断用的 IK probe、`StationPlannerCache`（planner 复用）和只给诊断用的证据收集，预计约 500 行 |
| `curobo/tracing.py`、`planning/evidence.py` | 主要服务于诊断和失败分析，压到 planner 运行所需的最少记录，或者删掉 |
| `curobo/collision_world.py`、`costs.py`、`head_fov.py`、`context.py` | 保留 |
| `robots/rby1m_mobile_torso.py` | 把 Panda 也在用的通用函数（`ordered_points`、`retime_points`、`static_ik_state`、`chain_state`、`minimum_mesh_query_dimension`）抽到 `robots/common.py`，RBY-1 自己的部分留在原文件 |
| `robots/panda_omron.py`、`waypoints.py`、`navigation/` | 保留 |
| `pool.py`、`remote.py` | 保留（用户 11-11 确定）：“GPU 规划池 + CPU 端代理”这套架构不删，不做去留耗时对比。只在文件内部删掉已删功能用到的部分（如诊断用的 `diagnostic_ik` 探测），接口和行为不变 |

### 4.4 `data/`：记录、导出、统计、图

| 文件 | 处理 |
|---|---|
| `recorder.py` | 保留（RL 用 `video_writer`）。**建议**删掉逐物理步的 physics 日志和 depth 记录（见第 8 节问题 3） |
| `evaluation/benchmark.py`、`benchmark_config.py`、`recovery.py`、`statistics.py` | 保留，`benchmark_config` 并进 `benchmark` |
| `evaluation/selection.py` | 只留按成功次数选站位和生成配对的函数，删掉模式相关的函数 |
| `evaluation/diagnosis.py`、`diagnosis_inputs.py`、`failure_analysis.py` | 删除（多标签诊断和旧 no-edit 失败分析） |
| `evaluation/station_q.py` | 不动（RL） |
| `export/lastmile_dataset.py` | 重写，见第 5 节；`load_training_batch` 保留 |
| `export/molmobot_dataset.py` | 保留（`robot.py` 用 `state22`） |
| `export/pi05_dataset.py` | 保留（π0.5 微调数据转换） |
| `export/station_dataset.py`、`molmobot_images.py` | 删除（没有任何地方引用） |
| `rendering/third_person.py`、`rl_*.py` | 不动（RL） |
| `rendering/head_views.py`、`edit_views.py`、`camera_calibration.py`、`analysis_video.py`、`replay.py` | 保留（站位可见性、编辑前后对比图、相机、执行视频、单相机回放） |
| `rendering/rollout_video.py`（只有 CLI 的 grid 回放在用）、`obstacle_views.py`（只有测试在用） | 删除 |
| `visualization/heatmap.py`、`scene_preview.py`、`benchmark.py` | 保留 |
| `visualization/sac_monitor.py` | 不动（RL） |
| `visualization/benchmark_replay.py`、`navigation_map.py` | 删除 |

### 4.5 `policies/`

全部保留。`config.py` 和 `molmobot_config.py` 合并成一个配置入口。`multitask/` 和 `pi05/core/` 是第三方代码，原样保留，不按代码规范改写。`sac/` 不动。

### 4.6 `workflows/`

| 文件 | 处理 |
|---|---|
| `scene_datasets.py`（476 行） | 重写，约 250 行。逐房屋执行：找目标 → 原场景和编辑后场景 → 采站位 → 冻结快照 → 出预览图。房屋级多进程和超时隔离保留 |
| `collection.py`（673 行）+ `station_calibration.py`（361 行） | 按阶段拆成两个文件：`station_map.py`（逐站位标定 + 热图，约 300 行）和 `collect.py`（S0→S1→操作采集，直接写自包含数据集，约 250 行）。删掉 `--recipe`、诊断、离线回放和 planner 复用 |
| `lastmilebench.py`（357 行） | 删掉 `mode_run` 分支（它读多标签诊断），只留“按标定成功次数选困难 S0 和参考 S1”的 v0.1 路径和恢复区域，约 250 行 |
| `rl_training.py`、`rl_evaluation.py`、`rl_sampling.py` | 不动 |

### 4.7 根目录其他文件

- `cli.py`：删除，`pyproject.toml` 里的 `[project.scripts]` 一并删掉。
- `config.py`：保留（RL 用 `PandaOmronConfig` 和 `REPO_ROOT`），删掉只给 CLI 用的部分。
- `gpu.py`：不动。
- `utils/`：保留。
- `scripts/`：
  - 新增 5 个阶段脚本（`build_scenes.py`、`station_map.py`、`collect.py`、`bench_build.py`、`bench_evaluate.py`），每个二三十行：开头先隔离 GPU，再解析 `--config`、`--output`、`--gpu`，最后调用 workflow。
  - 实验参数（worker 数、记录开关等）全部写进配置 JSON。
  - 删掉 no-edit 系列 12 个脚本和 `scripts/experiments/` 里 13 个诊断脚本。
  - `{train,score,evaluate}_sac.py`、`install*.sh` 保留。
- `configs/`：
  - 删除 `task_modes/`、`case_edits/`、`scene_datasets/no_edit_val/`（25 份批次文件）。这里只删配置文件，不动 `outputs/` 里的数据。
  - 新增 `configs/scenes/` 存放新的场景配方。
  - `execution/` 只留 cuRobo（Panda、Panda RoboCasa、RBY-1）和 MolmoBot、π0.5 这几份。
  - `robots/`、`policies/`、`benchmark/`、`rl/` 保留。
- `docs/`：按新入口重写 README、运行手册、architecture、data-format；`docs/archives/` 保留。
- `tests/`：
  - 删掉对应已删功能的测试，包括 modes、diagnosis、failure_analysis、mode_downstream、no_edit、split_calibration、CLI 等。
  - 其余测试跟着改 import。
  - RL 测试（`test_sac`、`test_rl_rewards`、`test_rl_observation`、`test_rl_evaluation`、`test_rl_sampling`、`test_rl_video`、`smoke/rl.py`）只允许改 import 行。

**规模估计：** 需要精简的自有代码从约 1.76 万行降到约 8K 行。加上原样保留的第三方代码约 5.9K 行和 RL 约 2.5K 行，`src/` 总共约 1.6 万行。

## 5 采集直接产出自包含数据集

`workflows/collect.py` 执行完 S0→S1→操作以后，把成功示教直接复制进本数据集的 `rollouts/<rollout_id>/`，内容包括：

- 本次采集产生的连续执行示教；
- 从标定 run 复用的成功操作示教；
- 需要的相机观测。

复制完再写 `train_index.jsonl`，里面只存数据集内部的相对路径。

- 删掉的东西：`source_run` 引用、`kind: materialized_lastmile` 的分支、“输入不能是已物化数据集”之类的检查、`materialize_lastmile()`。
- 保留的东西：`successful()` 判定条件、轨迹边界检查（步数、动作维度、观测帧数）、`load_training_batch()` 的读取方式。
- 输出目录结构和现在物化后的结构一致，下游训练读取代码不用改。

## 6 RL 的 import 行

按本方案，RL 依赖的共享模块都留在原路径：

- `config`、`runtime/simulation`
- `scenes/loading/local`、`source`
- `runtime/control/robot`、`dls`
- `scenes/stations/initialization`、`sampling`
- `scenes/geometry`、`scenes/tasks/discovery`
- `runtime/outcomes/pick_state`
- `data/rendering/third_person`、`data/recorder`

**预计 RL 文件一行都不用改。**

RL 用到的这些函数和类视为冻结接口，精简时不改签名和行为：

- `PandaOmronConfig`、`REPO_ROOT`、`Simulation`
- `task_local_model`、`SceneSource`、`load_robot_spec`、`BaseFrameDLSCfg`
- `floor_support`、`room_triangles`、`body_pose`、`local_bounds`
- `disk_samples`、`robot_contacts`、`descendants`、`geom_points`
- `RL_PICK_THRESHOLDS`、`update_hold_window`
- `ThirdPersonCamera`、`ThirdPersonConfig`、`video_writer`

如果实施中某个模块不得不改名，就在这一节登记要改的具体 import 行。以后合并 `actor-critic` 时按登记手工处理。

2026-11-11 增量：采集已接入自包含导出，benchmark 已删 mode_run 并合并配置，fixed_pick 后端已删除。30 项局部测试通过；阶段 3、4、执行配置与整体收尾尚未完成，见 delivery/04-collection-benchmark.md。

## 7 分阶段实施和验证

每个阶段单独提交。2026-11-11 用户最新要求：减少测试、实机测试，优先完成构建；本轮只跑相关现有测试，不以旧计划中的全套/GPU 对照作为继续代码构建的前置条件。真实结果仍需单独验证，不声明已通过。

| 阶段 | 内容 | 验证 |
|---|---|---|
| 0 基线（已完成） | 在 `3a2366e`（`1b2f454` + 相机校验修复）上用固定种子跑一组小规模参考，结果见第 9 节 | 同一份 cuRobo 标定重复跑两次，旧代码本身没有波动，后续对照按逐 trial 一致来卡 |
| 1 删除死代码 | 删掉孤立模块、一次性脚本、`rollout_video`、`benchmark_replay` 等，以及对应测试 | 单元测试全过；import 闭包里没有悬空引用 |
| 2 入口 | 删 `cli.py`，加 5 个阶段脚本（先调用旧 workflow），参数挪进配置 | 用新脚本重跑阶段 0 的场景构建、标定和 benchmark 构建，输出和基线一致 |
| 3 场景构建 | 用新的 `scenes/edits/` 替换 modes、build、augmentation；换新配方；重写 `scene_datasets.py` | 原场景这条路的站位和快照与基线一致。编辑后场景的结果本来就会变（不再按模式放障碍物），所以检查接受率、各类拒绝原因、物理校验通过情况，并把编辑前后的对比预览图交给用户看 |
| 4 规划器站位图 | 拆 `collection.py` 和 `station_calibration.py`；删 recipe、诊断、planner 复用、固定抓取；`pool.py`/`remote.py` 保留并在内部精简；精简 planner、tracing、evidence | 5 站位 × 3 次的成功率、热图与基线相差不超过阶段 0 量出的波动；单站位耗时不变慢 |
| 5 采集 | `collect.py` 直接写自包含数据集 | 示教条数、每条轨迹的数组和基线物化结果逐位一致；`load_training_batch` 能读出来 |
| 6 Benchmark | 删 `mode_run`、诊断和选择里的模式函数 | benchmark 构建输出（选出的 S0/S1、恢复区域）和基线一致；MolmoBot 评测 2 个 episode 能跑通 |
| 7 控制、策略、本体 | 合并动作相关文件和策略配置；抽出本体通用函数；整理 `execution_config` | 全套单元测试；Panda cuRobo 和 RBY-1 cuRobo 各跑 1 个站位，π0.5 推理 smoke |
| 8 收尾 | 文档、配置、测试清理；登记 RL import 变化（预计为零） | RL smoke 和短训练日志与基线一致；全套单元测试 |

要用 GPU 的步骤（阶段 0、3–8 的真实运行），开跑前先看 GPU 占用情况，选空闲的卡，并事先告诉用户。

渲染设备绑定已于 2026-11-11 修复，并完成准备/规划子进程的真实最小出图验证，见 delivery/03-egl-device-binding.md。完整业务对照仍待完成；9.4 保留历史问题记录。

## 8 已确认的决定（用户 2026-11-11）

1. 站位标定只用 cuRobo；MolmoBot 和 π0.5 只在 benchmark 评测里用。
2. 删除 `fixed_pick` 固定抓取后端。
3. 记录器删掉逐物理步的 physics 日志和 depth 记录，只保留数值记录、RGB 观测和视频。
4. `pool.py`、`remote.py` 保留，阶段 4 不做去留对比，只在文件内部精简（见 4.3）。
5. 障碍物编辑默认值：桌面 1–3 个，放在目标 0.3 m 内的同一支撑面上；地面 0–2 个，放在站位圈和目标之间；每个任务 1 个原场景加 1 个编辑场景。
6. Benchmark 只保留“按标定成功次数选困难 S0 和参考 S1”这一条路，删掉多标签诊断那条。
7. 阶段 0 基线用 GPU，只用空闲的卡。
8. 去掉 `cli.py`，每个阶段一个脚本；MolmoBot、π0.5、Panda、RBY-1、`reports/` 都保留。
9. RL 部分逻辑一行不动，最多只改 import 行；不碰 `actor-critic` 分支和它的 worktree。
10. 渲染设备隔离放到下一次需要 GPU 渲染之前修；先从阶段 1 开始执行。

## 9 阶段 0 基线结果

实验单元：`outputs/experiments/simplify-baseline/`（本 worktree，Git 忽略）。源码用本 worktree 的 `src`（`PYTHONPATH`），环境借用主仓库 `.venv`（cuRobo 栈，MuJoCo 3.16.0）和 `actor-critic` 的 `.venv`（RL 栈），不新装环境。共用环境变量在单元的 `env.sh`，各 run 的配置在单元的 `configs/`，对照脚本在 `analysis/`。CUDA 计算用 GPU 0–3（当时空闲），没有动 GPU 4–7 上其他用户的进程。

### 9.1 开跑前必须的修复（`3a2366e`）

`1b2f454` 上 Panda RoboCasa 的场景构建全部被拒，原因是 `compiled_robocasa_camera_profile_mismatch`：main 的 `c8f3c5a` 把 Panda 整机按 0.9 缩放，三台相机的局部位置随之变成出厂常量的 0.9 倍（FOV 和朝向不变），而 `data/rendering/head_views.robocasa_camera_calibration` 仍按出厂位置比对。修复只改这一处：期望位置乘以 `planning.robots.panda_omron.SCALE`。这是对现有行为的修复，不属于重构本身。

### 9.2 结果

场景用 v2 配方，house 103、目标 Cup_30（`pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2`）、仅 pick、每场景 5 个站位、种子 42。标定执行配置为 `configs/execution/panda_omron_robocasa_curobo.json`（每站位 3 次）。

| 环节 | Run | 结果 |
|---|---|---|
| 原场景构建 | `noedit` | 接受 1，5 个站位，2.6 s |
| 模式场景构建（ground_obstacle、arm_path_collision、narrow_space） | `modes` | 接受 2（地面障碍、窄空间，各 5 站位），拒绝 1（arm_path_collision：`no_channel_contrast`） |
| 原场景 cuRobo 标定 ×2 | `cal-a`、`cal-b` | 15 个 trial 逐个一致：14 成功，S0013 第 2 次 `planning_no_solution`；热图 npz 所有数组一致；单次 118 s；可配对 0 |
| 模式场景 cuRobo 标定 ×2 | `cal-modes-a`、`cal-modes-b` | 30 个 trial 逐个一致：地面障碍 11/15、窄空间 12/15；热图一致；单次 190 s；各有 3 个可配对 |
| 采集（基于 `cal-modes-a`）×2 | `collect`、`collect-b` | 计划 6 对、执行 6 对、成功 6 对；训练视图 6 条，全部是 `s0_to_s1_continuous`（数值标定没有图像，`s1_direct_operation` 为 0） |
| 物化 | `collect-mat` | 6 条示教自包含复制，506 MB；`load_training_batch` 读出 state (1,1,16)、actions (1,1,12)、三路 256×256 RGB |
| Benchmark 构建（`configs/benchmark/panda_omron.json`，每场景最多 2 个 episode） | `bench` | 2 个场景各 1 个 episode（S0 为 S0020，恢复锚点 S0045/S0001/S0006） |
| Benchmark 评测 | `bench-eval-pi05` | π0.5 跑完 2 个 episode，0/2 成功（`execution_limited`），覆盖率 1.0 |
| RL smoke | `rl-smoke` | 通过：909 个合法站位，划分 668/91/150；2 环境、5 步、2 次更新，数值有限 |
| RL 短训练（warmup 500、2000 transition、16 环境、种子 7） | `rl-train` | 到 2000 transition、11 次更新；0 和 2000 两次验证都完成并写出 `sampling_complete`；最后的视频渲染时被我停掉（见 9.4），`metrics.jsonl`、`best.pt`、`latest.pt` 保留 |
| 单元测试 | — | 主环境 431 项：1 失败、1 错误、15 跳过；RL 环境 433 项：1 失败、9 错误、4 跳过。失败都是已知的 `test_task_identity_survives_body_renumbering`；错误是两个环境各缺对方的依赖（主环境没有 RL 依赖、RL 环境没有 cuRobo） |

与原计划的差异：

- 原场景的 5 个站位成功率都很高，没有可配对的 S0，所以采集、benchmark 改在两个模式场景上做；这两份场景以后由新的障碍物编辑生成，阶段 5、6 的逐位对照要用阶段 0 的 `modes` 快照做输入。
- Benchmark 评测用 π0.5 而不是 MolmoBot：基线是 Panda RoboCasa，MolmoBot 是 RBY-1 的权重，用在 Panda benchmark 上只会记 `unsupported_policy`。

### 9.3 对照的容差

旧代码在固定种子下没有波动：标定逐 trial（状态、原因、步数）一致、热图逐数组一致；采集物化后的轨迹数组和图像逐字节一致。后续各阶段的对照按“完全一致”来卡，对照脚本是单元里的 `analysis/compare_calibration.py` 和 `analysis/compare_dataset.py`。

### 9.4 渲染设备隔离（已修复）

2026-11-11 已修复，并完成真实子进程出图验证。本节随原修复提交 `a51e622` 一并维护；详细实现与后续验证范围见 [渲染设备绑定修复报告](../delivery/03-egl-device-binding.md)。

**修复前的历史问题：** `collect-b`（`--gpu 1`）曾在 GPU 6 上占用约 1.5 GB；RL 短训练（`--gpu 2`）的验证渲染曾出现在 GPU 7；为确认映射启动的 4 个带录像标定（`--gpu 0 --egl-devices 0..3`）的子进程曾在 GPU 4–7 上各建立约 1.5 GB 的 EGL 上下文，发现后已全部停止。CUDA 计算在指定卡上，额外占用来自 EGL。此前通过 `eglQueryDeviceStringEXT` 未能取得 DRM 节点，且部分子进程没有显式绑定渲染设备；这些描述不再代表当前实现。

**已落实的修复：** 在不受继承的 CUDA 可见列表影响的独立进程中查询 `EGL_CUDA_DEVICE_NV`，解析 GPU 与 EGL 枚举的对应关系；本机验证 GPU 0 对应 EGL 7。入口以及准备、执行、规划等子进程统一绑定渲染设备，并保留 `--egl-devices` 的一一对应手动覆盖，不再将物理 GPU 编号直接当作 EGL 序号。

**修复提交时的验证：** 17 项相关测试通过；真实准备子进程和 PlanningPool 子进程的上下文仅出现在 GPU 0，GPU 4–7 未出现这次测试进程，退出后 GPU 0 显存释放。该最小验证不等于全量录像标定、采集或 RL 验收；后续实际运行及未完成项以修复报告和最终总结为准。原“修好之前不跑带渲染的运行”的前置阻塞已解除。
