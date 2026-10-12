# 增强场景 cuRobo 采集运行指南

> **最终站位接入更新**：下文手工预置快照的方法只接入场景，不限制采样点。
> 要执行经过覆盖压缩的最多20点，请使用 [正式接入指南](no-edit-augmented-stations.md)
> 和 `configs/case_edits/no_edit_curobo_case*_v*_final_stations.json`，不要复用旧 run。

在程序化增强后的 frozen 变体上运行 `collect-no-edit`（cuRobo v0.8.0 后端）。
只做运行步骤，不含分析。工作目录固定为本 worktree 根：

```bash
cd /data0/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/procedural-obstacle-augmentation
```

## 输入

| 项 | 值 |
|---|---|
| 配置 | `configs/no_edit/curobo_v080_arm_reach_half_base_radius.json` |
| Case1 变体 | `outputs/diagnostics/dense-case1-five-20261011-r5-views/variants/v000/scene`（seed 1095125285） |
| Case2 变体 | `outputs/diagnostics/dense-case2-five-20261011-r5-views/variants/v001/scene`（seed 975503932） |
| 场景 | val_103 / 目标 `Cup_30`（`cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2`，pick） |

`collect-no-edit` 没有「直接指定 frozen 场景」的参数。它按 `--dataset-dir` 的 `val_*.xml` 建场景，
但 `run_scene` 在 `scenes/val_103/scene/version.json` 已存在时改走 `Simulation.from_snapshot`。
因此做法是**预置变体快照到 run 目录后加 `--resume` 启动**，它会加载增强后的场景。

## 1. 环境（每个 shell 先执行一次）

```bash
cd /data0/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/procedural-obstacle-augmentation
export DATAFLOW_PYTHON="$PWD/../no-edit-lastmile-val/.venv-curobo-v080/bin/python"
export MUJOCO_GL=egl
export MPLCONFIGDIR="$PWD/outputs/dependencies/mpl"
export WARP_CACHE_PATH="$PWD/outputs/dependencies/warp-cache"
export PYTHONUNBUFFERED=1
mkdir -p outputs/logs
# GPU 选一张空闲卡：nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader
```

## 2. Case1 v000（seed 1095125285）

```bash
RUN="no-edit-curobo-case1-v000-$(date +%Y%m%d-%H%M%S)"
mkdir -p "outputs/no_edit/$RUN/scenes/val_103"
cp -r outputs/diagnostics/dense-case1-five-20261011-r5-views/variants/v000/scene \
      "outputs/no_edit/$RUN/scenes/val_103/scene"

set -o pipefail
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/curobo_v080_arm_reach_half_base_radius.json \
  --run-id "$RUN" --houses 103 --targets Cup_30 \
  --gpu-ids 5 --workers 1 --resume \
  2>&1 | tee "outputs/logs/$RUN.log"
```

## 3. Case2 v001（seed 975503932）

同上，只替换变体来源与 run-id：

```bash
RUN="no-edit-curobo-case2-v001-$(date +%Y%m%d-%H%M%S)"
mkdir -p "outputs/no_edit/$RUN/scenes/val_103"
cp -r outputs/diagnostics/dense-case2-five-20261011-r5-views/variants/v001/scene \
      "outputs/no_edit/$RUN/scenes/val_103/scene"

set -o pipefail
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/curobo_v080_arm_reach_half_base_radius.json \
  --run-id "$RUN" --houses 103 --targets Cup_30 \
  --gpu-ids 5 --workers 1 --resume \
  2>&1 | tee "outputs/logs/$RUN.log"
```

## 4. 状态查看与续跑

```bash
bin/lastmile-dataflow no-edit-status outputs/no_edit/<RUN>
# 中断后用完全相同的原命令加 --resume 续跑（run-id 不变，不要改时间戳）
```

## 5. 产物位置

| 路径 | 内容 |
|---|---|
| `outputs/no_edit/<RUN>/` | run 根：`frozen_config.json`、`scene_index.json`、`summary.json`、`timing.json`、`dataset_index.jsonl` |
| `outputs/no_edit/<RUN>/scenes/val_103/tasks/<task>/` | 站位表、`attempts/`、热图 `maps/`、四路视频、`result.json` |
| `outputs/logs/<RUN>.log` | 本次 stdout |

## 6. 注意点

- `--resume` 必须带：预置快照后 run 目录已存在；此时没有旧 `frozen_config.json`，会按当前代码/配置新写冻结摘要。
- 每次只用一个空卡、`--workers 1`；不要把 CUDA 压在同一张忙卡上。
- `--targets Cup_30` 只跑该场景的 Cup pick 任务，不枚举全部 pick/open。
- 变体快照只读：`cp -r` 复制，不修改 `outputs/diagnostics` 下的原产物。
- 需要换变体时改 `cp` 源目录即可（v001/v002/... 对应不同 seed）。
