# 增强场景 MolmoBot 采集运行指南

> **正式最终点集入口已实现**：请优先使用 [MolmoBot最终站位接入指南](molmobot-final-stations.md)。
> 下文的旧流程只移植场景，随后重新采样，不能保证最多20点；新流程增加native全量重验与覆盖压缩，并直接绑定点集。

在程序化增强后的 frozen 变体上运行 `collect-no-edit`（MolmoBot VLA 后端）。
只做运行步骤，不含分析。工作目录固定为本 worktree 根：

```bash
cd /data0/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/procedural-obstacle-augmentation
```

本文是在 [`增强场景cuRobo采集运行指南.md`](增强场景cuRobo采集运行指南.md) 基础上并入 MolmoBot 后端的版本。

> 另有与本指南并行的"验证后站位接入"机制：`NoEditConfig.augmented_variant` 直接引用变体目录，
> 绑定 `stations_final.json` 选出的最终站位集，见 [`no-edit-augmented-stations.md`](no-edit-augmented-stations.md)。
> 该机制同样要求变体快照的 robot profile 与运行端一致；MolmoBot 下仍需先做本文第 2 步的 native 重建
> （或让增强流程以 native profile 冻结），否则 `variant_manifest` 会因 robot profile 不符而拒绝。
> 本指南当前走的是"重建快照 + `--resume` + lattice 采样"的既有 cuRobo 口径。

## 与 cuRobo 指南的关键差别

cuRobo 指南的一条核心捷径是"**预置变体快照到 run 目录后加 `--resume`**"：`run_scene` 在
`scenes/val_103/scene/version.json` 已存在时改走 `Simulation.from_snapshot`，直接加载
`outputs/diagnostics/**/variants/vNNN/scene`。

**MolmoBot 不能复用这份快照**，原因是两条独立阻塞：

1. `cli.py` 在 molmobot 后端下**强制**把机器人配置替换为
   `control_profile='molmobot-native-v1' / control_hz=10 / physics_dt=.004`；
   而增强流程冻结变体时用的是基线快照里的 `position-v1 / 20Hz`。
   `Simulation.from_snapshot` 对 `version.json.robot` 做**整份相等**校验
   (`runtime/simulation.py:194`)，两者不等 → `ValueError('robot configuration mismatch')`。
2. 即使跳过配置校验，`model.mjb` 也是按 position-v1 编译的（夹爪被改成位置伺服、
   `ctrlrange=(-0.05,0)`）；native `apply_native` 只接受原生 motor
   `ctrlrange=[-100,100]` (`robots/molmobot.py`)，会抛 `InvalidAction('native motor limits changed')`。

因此做法是：**用 `scripts/rebuild_augmented_native.py` 把已验收变体的摆放参数
(`edits.json`) 在 native profile 的**原始基线**上回放并重新冻结**，生成 MolmoBot 可直接加载的
快照，再 `--resume` 采集。这是"已验收布局的 profile 移植"，不是新的障碍搜索，也不产生新的验收结论。

## 输入

| 项 | 值 |
|---|---|
| 配置 | `configs/no_edit/molmobot.json`（模型定义 `configs/policies/molmobot.json`） |
| Case1 变体 | `outputs/diagnostics/dense-case1-five-20261011-r5-views/variants/v000`（seed 1095125285，1 把椅子） |
| Case2 变体 | `outputs/diagnostics/dense-case2-five-20261011-r5-views/variants/v001`（seed 975503932，10 件台面物体） |
| 场景 | val_103 / 目标 `Cup_30`（`cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2`，pick） |

变体各自的 `frozen_config.json` 记录该 run 的 `input_snapshot` 基线、`asset_library`、
`house`、`settle`；重建脚本据此解析，无需手工填路径。

## 1. 环境（每个 shell 先执行一次）

```bash
cd /data0/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/procedural-obstacle-augmentation
# MolmoBot 客户端解释器需具备 matplotlib/Pillow（molmospaces venv 已满足）
export DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
export MUJOCO_GL=egl
export MPLCONFIGDIR="$PWD/outputs/dependencies/mpl"
mkdir -p outputs/logs outputs/dependencies
# 离线运行需 Qwen tokenizer 缓存；从已跑通的 val worktree 复制一次即可
cp -rn ../no-edit-lastmile-val/outputs/dependencies/molmobot-cache outputs/dependencies/ 2>/dev/null || true
```

GPU 由 MolmoBot 自己选：候选卡须连续 `gpu_sample_count`(3) 次、间隔 5s 都满足
利用率 ≤ `max_gpu_utilization`(20%) 且空闲显存 ≥ `min_free_gpu_mb`(24576MiB)；
按窗口最大利用率升序、最小空闲显存降序选择，没有满足的卡则等待。
`--gpu-ids` 给出候选池即可，不必手工挑空卡。

## 2. 重建 native 增强场景（每个变体一次）

```bash
RUN="molmobot-case1-v000-$(date +%Y%m%d-%H%M%S)"
echo "$RUN" > outputs/logs/.$RUN.id

PYTHONPATH=src "$DATAFLOW_PYTHON" scripts/rebuild_augmented_native.py \
  --variant outputs/diagnostics/dense-case1-five-20261011-r5-views/variants/v000 \
  --run-id "$RUN"
```

成功时打印写入的 `scenes/val_103/scene` 路径与障碍资产清单，并在该目录写入
`rebuild_provenance.json`（记录来源变体、摆放参数、资产目录、native robot 配置）。
脚本拒绝覆盖已存在的快照；重放下会发现非有限状态或几何冲突则直接报错、不落盘。

## 3. Case1 v000 采集

```bash
set -o pipefail
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/molmobot.json \
  --run-id "$RUN" --houses 103 --targets Cup_30 \
  --gpu-ids 0 1 2 3 4 5 6 7 --workers 1 --resume \
  2>&1 | tee "outputs/logs/$RUN.log"
```

## 4. Case2 v001（seed 975503932）

同上，替换重建源与 run-id：

```bash
RUN="molmobot-case2-v001-$(date +%Y%m%d-%H%M%S)"
PYTHONPATH=src "$DATAFLOW_PYTHON" scripts/rebuild_augmented_native.py \
  --variant outputs/diagnostics/dense-case2-five-20261011-r5-views/variants/v001 \
  --run-id "$RUN"

set -o pipefail
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/molmobot.json \
  --run-id "$RUN" --houses 103 --targets Cup_30 \
  --gpu-ids 0 1 2 3 4 5 6 7 --workers 1 --resume \
  2>&1 | tee "outputs/logs/$RUN.log"
```

## 5. 状态查看与续跑

```bash
bin/lastmile-dataflow no-edit-status outputs/no_edit/<RUN>
# 中断后用完全相同的原命令加 --resume 续跑（run-id 不变，不要改时间戳）
```

## 6. 产物位置

| 路径 | 内容 |
|---|---|
| `outputs/no_edit/<RUN>/` | run 根：`frozen_config.json`、`model_manifest.json`、`scene_index.json`、`summary.json`、`timing.json`、`dataset_index.jsonl` |
| `outputs/no_edit/<RUN>/scenes/val_103/scene/` | native 重建快照 + `rebuild_provenance.json` |
| `outputs/no_edit/<RUN>/scenes/val_103/tasks/<task>/` | 站位表、`attempts/`、热图 `maps/`、四路视频、`result.json` |
| `outputs/logs/<RUN>.log` | 主进程 stdout |
| `outputs/logs/<RUN>-val-103-worker.log` | 场景 worker stdout |
| `outputs/logs/<RUN>-val-103-model.log` | MolmoBot 模型进程日志 |

任务终值为 `station_evaluation_complete`（采样完成），**不表示任务成功**；MolmoBot 不走 A* 示教导出。

## 7. 注意点

- **不能跳过第 2 步**：直接把 cuRobo 的 position-v1 变体快照 `cp` 进去会在
  `from_snapshot` 的 robot 校验处失败。重建脚本是 MolmoBot 特有的前置步骤。
- `--resume` 必须带：run 目录由重建脚本预先建好。首次首次运行没有旧
  `frozen_config.json`，会按当前代码/配置新写冻结摘要；已存在则要求代码/配置摘要一致。
- 重建脚本不修改 `outputs/diagnostics` 下的原产物；只写目标 run 目录。
- 熔断：`attempt_timeout_s=300`、`scene_timeout_s=86400`；正常接触只记日志，
  成功/失败由任务验收器判断，仿真异常计 `simulation_failure`，不计入失败分母。
- 每个 scene worker 内常驻一个模型进程（私有本机 IPC），退出时清理；
  `--workers 1` 时同一时刻只有一套模型加载，显存需求按单模型约 10.2GiB 峰值 + 余量计，
  但选卡门槛仍按 24GiB 空闲。
- 换变体只改 `--variant` 源目录（v001/v002/... 对应不同 seed）；每个变体用新 run-id。
