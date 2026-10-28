# 无编辑全体 val 直接标定实验方案

- 状态：active
- 日期：2026-10-26
- 替代：无
- 关联：[需求](../requirement/01-requirement.md)、[计划表](02-schedule.md)、[任务模式构建说明](../../task-modes/info/01-build-scenes-modes.md)、[val100 全量实验](../../curobo-time-optimization/delivery/2026-10-24-val100-experiment.md)、[运行手册](../../../docs/运行手册.md)

## 1. 方案摘要

不单独执行 `build-scenes`，也不产出 `outputs/scene_datasets` 交付 run。新增 `evaluate-stations --recipe` 直接标定入口：一条命令完成

```text
读取 v2 配方
  → 逐 house 发现并预筛 Pick / Open 任务
  → 每任务采样 1 m / 0.25 m 站位，真实初始化后最多保留 20 个
  → 冻结一次性无编辑场景快照
  → 每站 3 次 cuRobo V2 标定
  → 输出标定 run、失败明细和汇总表
```

标定 run 是唯一主交付物。内部快照缓存写到 `outputs/_calibration_inputs/<run-id>/`，不是 build run、不生成预览、不单独汇报；全部标定与视频回放完成后可删除。全程 8 卡并行。

## 2. 交付边界

| 交付物 | 路径 | 说明 |
|---|---|---|
| 标定 run | `outputs/<calibration-run-id>/` | `trials.jsonl`、`summary.json`、`figures/`、`rollouts/` |
| 失败明细 | 标定 run 内 | `planning_failures.jsonl`、`execution_failures.jsonl`、`failure_summary.json`、`failure_table.md` |
| 汇总报告 | `reports/no-edit-calibration/delivery/` | 最终 Markdown |
| 视频 | `outputs/replays/<replay-run-id>/` | 全部标定完成后统一渲染 |
| 内部输入缓存 | `outputs/_calibration_inputs/<run-id>/` | 非交付物；含 `scenes.jsonl` 和冻结快照 |

不交付：独立 build run、场景预览、构建报告、任务模式诊断标签、seed 级 IK probe（默认不跑）。

## 3. 直接标定架构

```text
evaluate-stations --recipe <recipe>
  ├─ prepare_scene_inputs(recipe, outputs/_calibration_inputs/<run-id>/)
  │    ├─ 原始场景只读加载
  │    ├─ discover_tasks + 预筛（抓取、支撑、采样圆内地面）
  │    ├─ 每任务 BuildContext.prepare
  │    ├─ select_task_stations：最多 20 个真实合法站位
  │    └─ 冻结 model.mjb + initial.npz + scenes.jsonl
  └─ calibrate_stations(scenes, ...)
       ├─ 8 卡 PlanningPool
       ├─ CPU trial workers（60 起步，可提高）、12 准备 workers
       ├─ 每站 3 次
       └─ 标定 run 主记录
```

### 3.1 为什么仍需要一次性快照

完全无编辑不等于标定 worker 可以直接读原始 XML。现有标定 worker 通过 `Simulation.from_snapshot(scene_path, ...)` 载入场景，并要求同一任务的 3 次 trial 共享同一准备态。一次性冻结快照保证：

- 站位初始姿态与几何测量来自同一准备态；
- 3 次 trial 不因重复解析原始 XML 或重新静置产生差异；
- 不修改原始 XML/资产，快照只是无编辑场景的二进制副本。

该快照是内部输入，不是 build 交付。若后续确认要彻底去掉快照，需要新增“原始 XML + station initial”标定 worker 路径，不在本方案范围内。

### 3.2 无编辑构建逻辑

新增 `build_natural_task(ctx, root, *, house, seed)`：

- 不创建任何 `SceneEdit`，`edits=[]`、`pose_edit=None`、`station_hints=[]`；
- 调用现有 `select_task_stations(ctx, ctx.baseline, ctx.geometry, proposal)`，按 coverage 顺序选最多 20 个合法站位；
- 冻结 `ctx.baseline`；同房屋后续任务在非机器人 qpos 一致（容差 5 mm）时复用同一份 `scene-files/<prefix>/baseline`，避免每个任务重复保存约 300 MB 的 `model.mjb`；
- 写一条 accepted 记录：`proposal_intent='no_edit'`、`task_completion='unknown'`、`construction_checks={'valid': True, 'path': 'no_edit'}`；
- 无合法站位时写 rejected，不冻结、不补造。

`build_task` 在 `ctx.recipe.modes == []` 时直接调用该函数；非空时保持现有八类模式行为不变。

无编辑任务有 300 s 墙钟上限；超时写 `rejected / task_timeout` 并继续下一个任务，不冻结部分站位、不生成 trial，避免单个病态任务阻塞整批。

单个房屋准备有 20 min 墙钟上限；超时终止该房屋的独立进程，写 `house_timeout` rejected，其他房屋继续。

### 3.3 为什么保留 20，而不是全量 49

`select_task_stations` 不是先随机抽 20 个再验证；它按 coverage 顺序逐个在 clone 中真实初始化，合法即计入，达到 20 个立即停止。只有合法站位才做三相机可见性测量。

若改为全量 49：

- 每个任务都要初始化全部 49 个候选，合法站还要全部测量可见性；
- 标定 trial 数从每任务 ≤60（20 × 3）增加到 ≤147（49 × 3），规划/执行时间约 2.45 倍；
- 采样本身不是主要成本，标定 trial 才是。

因此保留 20。无编辑路径已关闭三相机可见性测量，并在同房屋复用一份冻结基线；热图在每批标定后离线导出，与下一批构建并行；这些优化不改变站位几何与标定语义。

## 4. 最小代码改动

| # | 文件 | 改动 |
|---|---|---|
| 1 | `scenes/dataset_config.py` | 允许 `modes=[]`；允许 `train_fraction=0.0` 表示全部来源记为 `val`；支持 `exclude_categories`、`include_categories`、`include_assets`、`exclude_tasks` 并校验。非空 `modes` 仍只能取八类。 |
| 2 | `scenes/build/builder.py` | 新增 `build_natural_task`；`build_task` 在空 modes 时委托给它；同房屋复用冻结基线；不测三相机可见性。 |
| 3 | `workflows/scene_datasets.py` | `_task_preparation_rejections` 在空 modes 时写一条 `proposal_intent='no_edit'` 的 rejected 记录；新增 `prepare_scene_inputs`，复用 `_build_house` 循环，输出根为 `outputs/_calibration_inputs/<run-id>/`；按 house 隔离错误；支持类别/资产白名单与房屋级任务排除。 |
| 4 | `workflows/collection.py` | 新增 `evaluate_recipe`：先 `prepare_scene_inputs`，再调用现有 `evaluate_stations`；`_trial_record` 增加 `attribution`、`retry_history`；标定阶段关闭内联热图。 |
| 5 | `cli.py` | `evaluate-stations` 增加互斥的 `--recipe`、`--build-workers`、`--exclude-categories`；`--recipe` 时走 `evaluate_recipe`。仍只有五个 CLI 命令。 |
| 6 | 新文件 `data/evaluation/failure_analysis.py` | 只读分析：资格、S0/S1、规划失败、执行失败、目标类别/task/station 成功率、排除建议、汇总表。 |
| 7 | 新脚本 `scripts/analyze_no_edit_calibration.py` | 调用 6，支持单个或批量标定 run。 |
| 8 | 新脚本 `scripts/prepare_no_edit_inputs.py` | 单独准备一个 chunk 的无编辑输入，供流水线调用。 |
| 9 | 新脚本 `scripts/run_no_edit_pipeline.py` | 准备下一个 chunk 与当前 chunk 的 8 卡标定重叠；按 chunk 更新排除类别；累计分析。 |
| 10 | 新脚本 `scripts/render_no_edit_replays.py` | 按标定 run 分组回放代表 rollout，合并视频清单。 |
| 11 | `tests/` | 配方空 modes/排除类别、natural 构建、trial 归因字段、失败分析、CLI 参数。 |
| 12 | `runtime/resources.py`、`planning/pool.py`、`cli.py` | `cpu_workers` 上限改为按机器 CPU 数；`preparation_workers`/`build_workers` 上限 64；新增 `--planning-workers-per-gpu`（1–4），`PlanningPool` 支持每卡多 worker。 |
| 13 | 新脚本 `scripts/export_no_edit_heatmaps.py` | 标定 run 完成后离线导出 `figures/`，与下一批构建/标定并行；不新增 rollout。 |

### 4.1 关键实现约定

- `prepare_scene_inputs` 与 `build_scenes` 共用 `_build_house`、任务发现、`BuildContext`、站位选择；不复制第二套构建逻辑。
- `select_task_stations` 的 `max_final_stations` 固定 20，不修改硬上限。
- `evaluate-stations --recipe` 使用的 execution 配置为 `configs/execution/panda_omron_no_edit_curobo.json`：`trials_per_station=3`、`operations=[pick, open]`、无 `mode_config`、`export_figures=false`；热图由独立离线脚本导出。
- 不启用 task-mode 诊断；资格在 `failure_analysis.py` 里按 `mode_calibration` 规则重算（3 次、`no_success` / `no_high_rate_s1` / `eligible`、S0 0/3→1/3、S1 3/3→2/3）。
- `_trial_record` 新增字段为纯增量，不改变现有 trial 判定。

## 5. 配方与批次

### 5.1 v2 配方模板

```json
{
  "schema_version": "scene-dataset-recipe-v2",
  "data_root": "/data1/wenyifan/lmdf/molmospaces_data",
  "dataset_dir": "assets/scenes/procthor-10k-val",
  "assets_dir": "assets",
  "asset_index": "/data1/wenyifan/lmdf/LastMileDataFlow/outputs/asset_index/task-modes-thor-20261025-r02/assets.jsonl",
  "robot_config": "configs/robots/panda_omron_robocasa.json",
  "mode_config": "configs/task_modes/mode_defaults.json",
  "execution_config": "configs/execution/panda_omron_no_edit_curobo.json",
  "houses": [0, 1, 2, 3, 4],
  "operations": ["pick", "open"],
  "modes": [],
  "exclude_categories": [],
  "include_categories": ["Cup", "Mug", "Bowl", "SoapBottle", "SprayBottle", "Plate", "PepperShaker", "SaltShaker", "Vase", "Apple", "Tomato", "Potato", "TeddyBear", "Statue", "Book", "Candle", "Pan", "Pot", "Dresser", "Cabinets", "Fridge", "Microwave", "Doorway", "SideTable", "Safe", "ShelvingUnit", "Desk", "Stove", "Dishwasher", "Toaster"],
  "include_assets": {"Vase": ["Vase_Flat", "Vase_Medium", "Vase_Tall"], "Statue": ["Dog_Statue", "Stone_Statue", "Fertility_Statue", "Hand_Statue", "Waving_Statue"]},
  "exclude_tasks": [],
  "variants_per_intent": 1,
  "max_final_stations": 20,
  "max_pick_targets_per_house": -1,
  "max_open_targets_per_house": -1,
  "station_config": {
    "radius_m": 1.0,
    "spacing_m": 0.25,
    "width": 256,
    "height": 256,
    "gripper_width_m": 0.08,
    "max_pick_object_size_m": 0.2,
    "navigation_whole_body": true
  },
  "split_seed": 42,
  "train_fraction": 0.0,
  "seed": 42,
  "local_task_models": false,
  "settle": {"require_source_stability": false}
}
```

`modes=[]` 表示无编辑；`train_fraction=0.0` 表示全部来源 `dataset_split='val'`；`max_*_targets_per_house=-1` 表示该操作类型全部合格目标。

### 5.2 批次文件

用 `scripts/make_no_edit_recipes.py` 从模板生成：

| 文件 | houses |
|---|---|
| `configs/scene_datasets/no_edit_val/pilot_000_004.json` | 0–4 |
| `configs/scene_datasets/no_edit_val/batch_000_099.json` | 0–99 |
| `configs/scene_datasets/no_edit_val/batch_100_199.json` | 100–199 |
| … | … |
| `configs/scene_datasets/no_edit_val/batch_900_999.json` | 900–999 |

批次只按 house 范围切分，不按结果挑选来源。运行时再把每批切成 chunk（首 chunk 5 房，之后 25 房）；每个 chunk 完成后分析并更新 `exclude_categories`：累计 ≥1000 trial 且 success rate < 0.1%；Cup、Mug、Bowl、SoapBottle、SprayBottle、Vase 为永久白名单，不参与剔除。由于流水线会提前准备下一个 chunk，排除通常滞后一个 chunk 生效。首批 0–99 同时承担 10% 规模验证。

## 6. 运行编排（8 卡并行）

### 6.1 阶段

| 阶段 | 范围 | 目的 |
|---|---|---|
| P0 试点 | `val_0`–`val_4` | 验证直接标定链路、失败证据字段、单房耗时 |
| P1 10% | `val_0`–`val_99` | 验证批次规模、8 卡吞吐、存储 |
| P2–P10 | 100–199 … 900–999 | 剩余 9 批全量 |
| 汇总 | 全部标定 run | 汇总失败表、S0/S1、无用场景 |
| 视频 | 代表集 | 全部标定完成后统一回放 |

每批使用新 run-id，不与前一批复用或覆盖。P0/P1 不通过不启动后续批次。

P0 试点完成后，全量从 `val_5` 继续，并复用试点 state：

```bash
.venv/bin/python scripts/run_no_edit_pipeline.py \
  --recipe configs/scene_datasets/no_edit_val/remaining_005_999.json \
  --run-prefix no-edit-val-all \
  --state outputs/v1-no-edit/inputs/no-edit-pilot-state.json \
  ...
```

这样 `val_0`–`val_4` 的试点结果和排除类别计入最终统计，不重复构建。

### 6.2 8 卡资源分配

- 构建/准备：`--build-workers 32 --gpu 0 1 2 3 4 5 6 7`，worker 轮流使用 8 卡。
- 标定：`--gpu 6 7 --cpu-workers 160 --preparation-workers 32 --station-workers 8 --planning-workers-per-gpu 2 2 --recording-mode numerical`。
- `PlanningPool` 使用全部 8 卡；每卡可配置 1–4 个规划 worker；CPU trial 默认 160；准备并发 32。
- EGL 设备默认与 GPU 编号一致；如机器映射不同，显式传 `--egl-devices 0 1 2 3 4 5 6 7`。
- 启动前用 `nvidia-smi` 确认 8 卡空闲；不占用其他进程，不固定卡号到代码。

### 6.2.1 CPU 容量

`cpu_workers` 是独立 trial 执行容量，每个 trial worker 单线程（`OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`）。按以下方式调整：

- 试点 60 起步；实测 c004 标定阶段 256 核中只有约 27 核 runnable，整机 CPU 约 10.5%，因此后续提高到 160；
- 每个 run 的 `cpu_workers` 固定并写入 `config.json`，不在 run 中途修改；下一批用新 run-id 和新值；
- `preparation_workers` 默认 12，实测可提高到 32；只有准备阶段成为瓶颈时才继续提高；
- `RunResources` 已按机器 CPU 数校验，不再硬限制 60。

### 6.2.2 GPU 容量

实测每卡显存约 2 GB / 73 GB，活跃标定阶段利用率约 59–80%，chunk 尾部/CPU 执行阶段会掉到 0%，说明 GPU 仍有并发余量。

- 默认 `--planning-workers-per-gpu 1`；c004 作为基线。
- 当前按实时负载分配：仅使用 GPU 6–7，各 2 个 planning worker；避免 0–5 显存接近打满。
- 2 worker 已把 GPU 计算基本打满；CPU 仍有约 86% idle，继续加 CPU 不会明显加速。3 worker/GPU 显存允许，但 GPU 已饱和，不作为默认；除非后续实测显示利用率仍长期低于 80%。
- 每卡 worker 数写入 run 配置；后续批次保持 2，按实测吞吐决定是否回退到 1。

### 6.3 命令

```bash
export LD_LIBRARY_PATH=/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64:${LD_LIBRARY_PATH:-}
export CUDA_HOME=/data1/wenyifan/opt/cuda-12.8
export MUJOCO_GL=egl

# 主入口：流水线。准备下一 chunk 与当前 chunk 的 8 卡标定重叠；
# 标定完成后分析并更新排除类别，热图离线导出与下一 chunk 并行。
.venv/bin/python scripts/run_no_edit_pipeline.py \
  --recipe configs/scene_datasets/no_edit_val/batch_000_099.json \
  --run-prefix no-edit-val-b000-099 \
  --output-dir outputs \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets \
  --gpu 0 1 2 3 4 5 6 7 \
  --first-chunk-houses 5 --chunk-houses 25 \
  --build-workers 32 --cpu-workers 160 --preparation-workers 32 --station-workers 8 \
  --gpu 6 7 --planning-workers-per-gpu 2 2 --heatmap-workers 16 --min-streak 1000 --min-success-rate 0.001

# 单批直接标定（不经过流水线；仍可用于调试）
.venv/bin/lastmile-dataflow evaluate-stations \
  --recipe configs/scene_datasets/no_edit_val/pilot_000_004.json \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets \
  --config configs/execution/panda_omron_all_cases_curobo.json \
  --output-dir outputs --run-id no-edit-val-pilot-000-004-$(date +%Y%m%d-%H%M%S) \
  --gpu 0 1 2 3 4 5 6 7 --egl-devices 0 1 2 3 4 5 6 7 \
  --build-workers 12 --cpu-workers 60 --preparation-workers 12 \
  --station-workers 8 --recording-mode numerical

# P1 / P2–P10：替换 --recipe 和 --run-id
# --cpu-workers 试点 60；后续按实测 CPU/内存余量提高到 96、128 等
.venv/bin/lastmile-dataflow evaluate-stations \
  --recipe configs/scene_datasets/no_edit_val/batch_000_099.json \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets \
  --config configs/execution/panda_omron_all_cases_curobo.json \
  --output-dir outputs --run-id no-edit-val-batch-000-099-$(date +%Y%m%d-%H%M%S) \
  --gpu 0 1 2 3 4 5 6 7 --egl-devices 0 1 2 3 4 5 6 7 \
  --build-workers 12 --cpu-workers 60 --preparation-workers 12 \
  --station-workers 8 --recording-mode numerical
```

标定 run 位于 `outputs/<run-id>/`；内部输入位于 `outputs/_calibration_inputs/<run-id>/`。

### 6.4 每批完成后的分析

```bash
.venv/bin/python scripts/analyze_no_edit_calibration.py \
  --calibration outputs/<run-id> \
  --output outputs/<run-id>/analysis
```

分析只读，不改写 `trials.jsonl`。每批标定结束后，流水线还会启动离线热图导出：

```bash
.venv/bin/python scripts/export_no_edit_heatmaps.py \
  --calibration outputs/<run-id> --workers 16 --skip-existing
```

热图导出与下一批构建/标定并行，不新增物理步，也不阻塞 GPU。全部批次完成后：

```bash
.venv/bin/python scripts/analyze_no_edit_calibration.py \
  --calibration outputs/<run-id-1> outputs/<run-id-2> ... \
  --output reports/no-edit-calibration/delivery/analysis
```

### 6.5 统一视频

全部标定完成后，从各标定 run 选代表 rollout。`evaluate-stations --replay-from` 一次只接收一个标定 run，因此按 run 分组回放，最后合并清单：

1. 分析脚本为每批生成 `replay_manifest.jsonl`（scene / task / station / trial / rollout / reason）；
2. `scripts/render_no_edit_replays.py` 按 run 分组调用回放；
3. 合并各批 `manifest.jsonl`，形成一份总清单。

```bash
.venv/bin/python scripts/render_no_edit_replays.py \
  --manifests outputs/<run-id-1>/analysis/replay_manifest.jsonl \
              outputs/<run-id-2>/analysis/replay_manifest.jsonl \
  --output-dir outputs --run-id-prefix no-edit-val-replay-$(date +%Y%m%d-%H%M%S) \
  --gpu 0

# 单批回放的实际命令形态
.venv/bin/lastmile-dataflow evaluate-stations \
  --replay-from outputs/<run-id> \
  --rollouts r000001 r000123 ... \
  --replay-layout grid \
  --output-dir outputs --run-id no-edit-val-replay-<batch>-$(date +%Y%m%d-%H%M%S) \
  --gpu 0
```

代表集按需求 6 节：每个失败行 1 条、每 operation 1 条成功、每有效场景最多 1 组 S0/S1。回放完成前不删除 `outputs/_calibration_inputs/`。回放不新增物理步，不重新判定任务结果。

## 7. 失败分析产物

`failure_analysis.py` 输出：

| 文件 | 内容 |
|---|---|
| `planning_failures.jsonl` | 每条规划失败 trial；阶段、IK 不可达/约束不可行、TrajOpt、arm、seed、grasp_index、误差、约束项 |
| `execution_failures.jsonl` | 每条执行失败 trial；phase、原始 reason、attribution、grasp_row/source、retry |
| `failure_summary.json` | 资格、S0/S1、task/house 分类、主表与交叉表计数 |
| `failure_table.md` | 5.3 主表、分组表、类别交叉表、执行细分表、约束细分表 |

分析读取：

- `trials.jsonl`；
- `planning_ref` 指向的 `planning.json` 与同目录 `solver_trace.json`；
- rollout 的 `task_evidence_initial.json`；
- 内部输入的 `scenes.jsonl`（从标定 run 的 `config.json.dataset` 解析）。

## 8. 验收标准

### 8.1 每批

- 标定 run 正常结束，`summary.json` 存在；
- 计划 trial 数与实际记录数差异全部有解释（准备失败、初始化失败、共享故障）；
- 无共享 GPU/模型/记录故障；
- 失败分析能生成全部四个文件；
- 规划失败明细至少覆盖 IK 不可达、IK 约束不可行、TrajOpt 三类中的实际出现项；
- 执行失败明细能读到 `attribution` 和 `retry_history`；
- 不完整任务、`no_success`、`no_high_rate_s1` 分开统计。

### 8.2 全部完成

- 1000 个来源的 house 级覆盖表；
- task 级全失败、全成功、有效、未完成数量与占比；
- 无用场景 task 口径与 house 口径；
- S0/S1 数量与配对；
- trial 级失败原因主表（pick / open / 合计 / 占比）；
- 规划失败按 goalset / approach / grasp × IK 不可达 / IK 约束不可行 / TrajOpt；
- 执行失败按 phase × reason × attribution；
- 视频清单与代表视频；
- 报告明确 unknown/未执行不是任务失败。

## 9. 资源与耗时

按旧 val0–99（100 来源、1 Pick + 1 Open、8,463 trial、2.88 h、2 卡）线性外推，并考虑本次全部合格目标与 8 卡：

| 项目 | 全量 1000 来源 |
|---|---|
| 构建/准备 | 9–30 h（25 房并行 chunk） |
| 标定 | 100–250 h（8 卡压缩规划段，物理/CPU 段仍是瓶颈） |
| trial 数 | 约 50–150 万（单房可能 50–90 个合格任务，以 P0 实测为准） |
| 内部输入 + 标定存储 | 约 0.5–1.2 TB，准备 ≥1.5 TB 空闲 |
| 每批 100 来源 | 标定约 10–30 h，构建约 1–3 h |

以上是保守范围，P0/P1 后按实测重新估算；不把旧 run 的耗时当作本次承诺。CPU worker 从 60 起步，试点后按实际 CPU/内存余量提高，能压缩 trial 排队时间，但物理步和内存仍是上限。目标类别排除会在后续 chunk 减少任务量。

## 10. 停止与异常处理

- **共享故障**（CUDA、模型、GPU、渲染、记录服务）：立即停止当前批次，保留已完成记录；新 run-id 重试，不继续消费失效服务。
- **单 house 加载/准备失败**：记录 rejected 和真实原因；若现有 `build_scenes` 的父进程会因 house 错误停止整批，则在 `prepare_scene_inputs` 中按 house 隔离，单房错误不终止批次。共享故障仍终止。
- **站位准备失败**：该站位不生成 3 次 trial，任务记 `incomplete`，不补造。
- **规划无解**：状态 `planning_no_solution`，`task_outcome=unknown`，不写成任务失败。
- **写入失败**：不覆盖已有 run；换新 run-id。
- **GPU OOM / 不稳定**：降低 `--cpu-workers` 或 `--preparation-workers` 后新 run-id 重跑该批；不修改已有结果。

## 11. 测试与真实验证

1. 局部单元测试：
   - 配方 `modes=[]`、`train_fraction=0.0` 通过；
   - natural 构建只产生无编辑 accepted/rejected，站位 ≤20；
   - `_trial_record` 含 `attribution`、`retry_history`；
   - 失败分析用合成 `trials.jsonl` + `planning.json` 覆盖 IK 不可达、IK 约束不可行、TrajOpt、执行失败。
2. 相关领域测试后跑全套离线回归：
   ```bash
   MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
     .venv/bin/python -m unittest discover -s tests -t . -v
   ```
3. P0 真实 GPU 试点：8 卡、val_0–4，确认真实 cuRobo rollout、失败证据、耗时；单元测试和 mock 不计为真实任务结果。
4. P1 10% 验证通过后再启动 P2–P10。

## 12. 风险与应对

| 风险 | 应对 |
|---|---|
| 1000 来源单批太长，失败代价高 | 10 批 × 100，每批独立 run-id |
| 全部合格目标导致 trial 数远超旧 run | P0/P1 实测后重估；必要时按 house 分批而非降采样 |
| 8 卡并行下 CPU/内存瓶颈 | 监控 CPU 队列和准备并发；`cpu_workers` 60 起步按实测提高，`preparation_workers` 默认 12 |
| 内部快照占用大 | 每批完成后清理已回放的 `_calibration_inputs`；删除前确认无待回放 rollout |
| 失败证据字段缺失 | P0 即检查 `solver_trace`、`attribution`、`retry_history`，不等到全量 |
| 单房异常终止批次 | `prepare_scene_inputs` 按 house 隔离；共享故障才停批 |
| 无编辑场景大量 no_success | 按需求丢弃并统计，不补造 rollout；无效负结果保留 |

## 13. 本方案不做的事

- 不单独运行 `build-scenes`，不产出 `outputs/scene_datasets` 交付 run；
- 不生成场景预览、构建报告或 task-mode 诊断标签；
- 不编辑场景、不移动目标、不改初始姿态；
- 不运行 seed 级 IK probe（默认关闭）；
- 不覆盖已有 run，不补造失败/成功记录；
- 不把规划无解、未执行或 unknown 写成任务失败。
