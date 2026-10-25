- 状态：active
- 日期：2026-10-25
- 替代：无
- 关联：docs/architecture.md、docs/运行手册.md、reports/README.md、reports/curobo-time-optimization/info/02-parallelism-and-batch.md

# build-scenes → cuRobo 标定的 seed 汇总

本文汇总当前代码中 `build-scenes` 到 `evaluate-stations`（cuRobo V2 标定）链路上的随机种子来源、派生关系、作用范围和落盘位置，并核对本地 `nvidia-curobo 0.8.0` 的 seed 相关配置默认值。

范围：`build-scenes` → `evaluate-stations`。`collect-lastmile`、`build-bench`、`evaluate-bench` 及策略推理只在与本链路容易混淆时提及。

## 1. 总览：两个互相独立的 seed 入口

| 阶段 | 入口字段 | 当前值 | 配置文件 |
|---|---|---|---|
| `build-scenes` | `SceneDatasetRecipe.seed` | `42` | `configs/scene_datasets/*.json` |
| `evaluate-stations` | `ExecutionConfig.seed` | `42` | `configs/execution/curobo_v2.json` |

两者默认都是 `42`，但**互不派生**：

- 场景库 `config.json` 中的 `geometry.seed` 只用于场景构建，标定不会读取它。
- `evaluate-stations` 从场景库 `config.json` 继承的几何字段白名单不包含 `seed`（`src/lastmile_dataflow/workflows/collection.py:371`）。

## 2. build-scenes 阶段

| 种子 | 值 / 派生 | 代码位置 | 控制内容 |
|---|---|---|---|
| `recipe.seed` | 配置值 `42` | `src/lastmile_dataflow/scenes/dataset_config.py:91` | 场景构建阶段总种子，所有派生的根 |
| `geometry.seed` | 强制等于 `recipe.seed` | `src/lastmile_dataflow/scenes/dataset_config.py:144` | `park_robot` 的基座候选位置采样 |
| 单元 seed | `recipe.seed + unit_index` | `src/lastmile_dataflow/workflows/scene_datasets.py:559` | 每个选中目标的障碍物增强总种子 |
| 资产检索 seed | 等于单元 seed | `src/lastmile_dataflow/workflows/obstacle_augmentation.py:88`、`src/lastmile_dataflow/scenes/loading/asset_library.py:34` | THOR 候选资产的 `rng.permutation` |
| 变体 seed | `SeedSequence(unit_seed).spawn(variant_count)[i].generate_state(1)[0]` | `src/lastmile_dataflow/workflows/obstacle_augmentation.py:103,113` | 每个变体的完整随机放置过程 |

派生关系：

```text
recipe.seed (42)
├─ geometry.seed = recipe.seed
│   └─ park_robot 基座停放采样
├─ unit_seed = recipe.seed + unit_index
│   ├─ ThorLibrary 资产候选 permutation
│   └─ variant_seed = SeedSequence(unit_seed).spawn(n)[i] 的 32 位状态
│       └─ build_variant 的 rng
│           ├─ 障碍物数量
│           ├─ 资产类别 / 实例选择
│           ├─ 放置点与 yaw
│           └─ 物理试验的重试序列
└─ Case3（open 场景）不进入增强，无变体 seed
```

关键语义：

- 变体 rng 是一个连续流，数量、资产、放置点、yaw、物理重试都从中取数，不是每次失败重新播种（`src/lastmile_dataflow/scenes/augmentation/builder.py:21`）。
- `unit_index` 是 discovery 选中计划的**全局下标**，跨房屋连续递增。前面房屋少选或多选一个目标，会改变后续房屋所有单元的 seed。
- 站位采样本身不受 seed 控制：`sample_stations` 是确定性格点（`src/lastmile_dataflow/scenes/stations/sampling.py:15`），筛选按 ID 与评分排序。seed 只影响 `park_robot` 的基座停放采样（`sampling.py:109`）。
- `park_robot` 对所有单元使用同一个 `recipe.seed`（不是单元 seed），各单元的停放采样序列相同，但起点快照不同。
- 任务发现与资格检查无随机：`spatial_select` 按 `task_id` 和分数确定性排序。
- 落盘：`scenes.jsonl` 每条记录的 `seed` 是变体 seed；场景库 `config.json` 的 `seed` 是 `recipe.seed`。

## 3. evaluate-stations（cuRobo 标定）阶段

| 种子 | 值 / 派生 | 代码位置 | 控制内容 |
|---|---|---|---|
| `config.seed` | 执行配置 `42` | `src/lastmile_dataflow/runtime/execution_config.py:59`、`configs/execution/curobo_v2.json` | 标定阶段总种子；planner 创建时的 `random_seed` |
| `collection.seed` | 被替换为 `config.seed` | `src/lastmile_dataflow/workflows/collection.py:374` | 随记录落盘；标定准备阶段不使用随机初始化 |
| trial seed | `config.seed + station_index * trials_per_station + trial` | `src/lastmile_dataflow/workflows/station_calibration.py:58` | 每个 trial 的全部 cuRobo 随机性 |
| 批量 planner seed | 该 station 的 trial seed 列表 | `src/lastmile_dataflow/planning/pool.py:56` | 每个 batch slot 独立的 solver 随机流 |
| planner 复用 reset seed | `seeds[0]`（该 station 首个 trial seed） | `src/lastmile_dataflow/planning/curobo_v2.py:414,466` | 复用时重置 solver 随机流 |
| 抓取候选 seed | 等于 trial seed | `src/lastmile_dataflow/runtime/execution/manipulation.py:13` | `rng.permutation` 打乱候选抓取位姿 |
| 手臂顺序 | `seed % 2` | `src/lastmile_dataflow/runtime/execution/manipulation.py:49` | 偶数先左臂、奇数先右臂 |
| 重试 seed | `seed + retry` | `src/lastmile_dataflow/runtime/execution/curobo.py:74` | 仅连续采集（有导航路径）时生效；标定无 path，retry 不执行 |

trial seed 注入 cuRobo 的路径：

```text
trial_seed
├─ planner_options(..., seed)
│   └─ V2Planner 创建 random_seed
├─ bind_batch_seeds(seeds)
│   ├─ IK SeedManager
│   ├─ TrajOpt SeedManager
│   └─ LM-IK Halton SampleBuffer
├─ candidate_pool rng(seed)
│   └─ 抓取候选顺序
└─ seed % 2
    └─ 左右臂执行顺序
```

关键语义：

- 同一 station 的 `trials_per_station` 个 trial 一起提交 GPU。`bind_batch_seeds` 为每个 lane 建独立随机流（`src/lastmile_dataflow/planning/curobo_v2.py:48-83`），一个 lane 失败不影响其他 lane 的 seed。
- 不同场景会出现相同 seed：公式只含 scene 内的 `station_index`，所以每个场景的 station 0 / trial 0 都是 `42`，station 0 / trial 1 都是 `43`。
- 落盘位置：
  - `trials.jsonl` 的 `seed`
  - `planning/<query_id>/query.json` 的 `identities[].seed`
  - attempt 的 `config` 记录
  - `batch_grasp_result.json` 的 `diagnostics.seed` / `batch_seeds`
  - `planner_reuse.json` 的 `seed`

## 4. cuRobo 0.8.0 seed 相关参数核对

本地安装版本：`nvidia-curobo 0.8.0`（`.venv/lib/python3.11/site-packages/curobo`）。

| 参数 | 0.8.0 官方情况 | 是否官方默认配置项 | 本项目实际值 |
|---|---|---|---|
| `num_ik_seeds` | `MotionPlannerCfg.create` 默认 `32` | 是 | `32`（显式传入） |
| `num_graph_seeds` | 仅 `SolveState` 有同名可选字段，默认 `None`，未接入 `MotionPlannerCfg` | 否 | 未设置 |
| `num_trajopt_seeds` | `MotionPlannerCfg.create` 默认 `4` | 是 | `4`（显式传入） |
| `num_trajopt_noisy_seeds` | 包内不存在该字段 | 否 | 未使用 |
| `num_batch_ik_seeds` | 包内不存在该字段 | 否 | 未使用 |
| `num_batch_trajopt_seeds` | 包内不存在该字段 | 否 | 未使用 |

依据：

- `curobo/_src/motion/motion_planner_cfg.py:56-57` 定义 `num_ik_seeds=32`、`num_trajopt_seeds=4`；包内测试断言同一默认值。
- `curobo/_src/solver/solve_state.py:45` 的 `num_graph_seeds` 只是 `SolveState` 元数据，整个包内没有把它接入 planner 配置的地方。
- 全包搜索 `num_trajopt_noisy_seeds`、`num_batch_ik_seeds`、`num_batch_trajopt_seeds` 均无结果。

0.8.0 中对应的真实机制：

- **Graph Planner** 的随机节点采样由 `graph_planner/exact_graph_planner.yml` 的 `sampler_seed: 0` 和迭代参数控制，不是顶层 `num_graph_seeds`。本项目未覆盖 `sampler_seed`。
- **Batch 模式**没有单独的 `num_batch_*` 参数：

  ```text
  总 IK 问题数     = num_ik_seeds × max_batch_size
  总 TrajOpt 问题数 = num_trajopt_seeds × max_batch_size
  ```

  见 `SolveState.get_ik_batch_size()` / `get_trajopt_batch_size()`（`curobo/_src/solver/solve_state.py:86-95`）。本项目传 `max_batch_size=batch_size`（`batch_size = len(trial seeds)`），再为每个 batch slot 绑定 trial seed。

本项目的实际设置：

- `num_ik_seeds=32`、`num_trajopt_seeds=4` 写在 `src/lastmile_dataflow/runtime/execution_config.py:52-53` 和多个 `configs/execution/*.json`，并显式传给 `MotionPlannerCfg.create`（`src/lastmile_dataflow/planning/curobo_v2.py:258-259`）。数值等于官方默认值，但项目是显式配置，不依赖默认值。
- 真正的随机种子是 `random_seed`：官方默认 `123`，本项目传 `config.seed`（trial seed，默认 `42`），见 `curobo_v2.py:261`。
- `bind_batch_seeds` 替换 IK/TrajOpt `SeedManager` 与 LM-IK Halton 采样器；Graph Planner 节点采样仍使用固定的 `sampler_seed: 0`。

## 5. 注意事项

1. build-scenes 与 evaluate-stations 的 seed 互相独立。修改场景配方 seed 不会改变标定 seed，反之亦然。
2. 修改 `recipe.seed` 会改变增强种子与 `park_robot` 基座停放；修改 `ExecutionConfig.seed` 会改变所有 trial seed。
3. 场景构建的单元 seed 与 discovery 选择顺序耦合，增删前面房屋的目标会改变后续房屋的增强结果。
4. Case3（open）场景构建不进入障碍物增强，没有变体 seed。
5. `num_ik_seeds` / `num_trajopt_seeds` 是**每次求解的种子数量**，不是随机种子值；真正的种子是 `random_seed`（项目传 trial seed）。
6. `num_graph_seeds`、`num_trajopt_noisy_seeds`、`num_batch_ik_seeds`、`num_batch_trajopt_seeds` 不能作为当前 0.8.0 的官方参数引用。
7. `collect-lastmile` 的恢复试验使用 `config.seed + len(pairs)`（`src/lastmile_dataflow/workflows/collection.py:267`），不属于标定链路。
8. `initialize_robot` 使用的 `collection.seed` 仅在 `src/lastmile_dataflow/runtime/construction/preparation.py` 路径中，当前五个 CLI 主链不经过。

## 6. 快速核对命令

将 `<run>` 替换为实际的运行目录（例如 `outputs/<run_id>`）。

```bash
# 场景构建实际使用的变体 seed
grep -o '"seed": [0-9]*' <run>/scenes.jsonl | sort | uniq -c

# 标定实际使用的 trial seed 分布
grep -o '"seed": [0-9]*' <run>/trials.jsonl | sort | uniq -c

# 本地 cuRobo 官方默认值
sed -n '50,75p' .venv/lib/python3.11/site-packages/curobo/_src/motion/motion_planner_cfg.py
```
