# LastMileDataFlow 简化重构接手文档

- 状态：active；已恢复工作，阶段 0–2 完成，阶段 7 的控制、策略配置、本体公共工具已实施
- 替代：无
- 最新进度：[08-docs-scene-validation.md](08-docs-scene-validation.md)：旧配置和主文档已清理，真实场景构建2/2接受、自动预览2张成功，原场景快照/站位与基线一致。长函数检查与其余必要验证继续。
- 上轮进度：[07-runtime-assets-recording.md](07-runtime-assets-recording.md)：资产与 runtime 合并、physics/depth 录制和无用执行字段已删除；完整文档/配置清理及验证仍待处理。
- 前轮进度：[06-station-map.md](06-station-map.md)：标定/采集已拆分，诊断、旧 recipe、StationPlannerCache 和 mode_config 已删除，58 项相关局部测试通过；其余收尾继续。
- 上轮进度：[05-scene-edits.md](05-scene-edits.md)：阶段 3 已替换旧模式构建并删除旧目录，局部测试通过；真实构建验证和阶段 4 等继续处理。
- 前轮进度：[04-collection-benchmark.md](04-collection-benchmark.md)，包含采集自包含导出、benchmark 分支删除与固定抓取后端删除；阶段 3、4、执行配置和整体收尾仍待完成。
- 上轮交付：[03-egl-device-binding.md](03-egl-device-binding.md)；控制精简见 [02-control-policy-refactor.md](02-control-policy-refactor.md)
- 日期：2026-11-11
- 关联：[../plan/01-refactor-plan.md](../plan/01-refactor-plan.md)（计划、已确认的决定、阶段 0 基线结果）

## 1 位置和规矩

- 服务器 pro5000，worktree `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify`，分支 `refactor/simplify`，从 `actor-critic` 的 `1b2f454` 拉出。
- 每个阶段单独提交到本分支，不 push，不改别的分支。`actor-critic` 分支和它的 worktree 上还有人在提交 RL 改动，不要碰。
- RL 部分的逻辑一行不动，最多只改 import 行（文件清单见计划第 3、6 节）；目前 RL 文件没有任何改动。
- 用户的代码规范见计划第 1 节；超出计划的改动、需要用户拍板的事，先停下来问。
- 删除真实采集数据、权重、资产之前要先问用户；源码、配置、脚本按计划删。

## 2 提交记录

| 提交 | 内容 |
|---|---|
| `be49952` | 计划文档 |
| `3a2366e` | 修复：RoboCasa 相机校验的期望位置乘以 Panda 的 0.9 缩放（不修的话 Panda RoboCasa 场景构建全部被拒，见计划 9.1） |
| `bb6fd32` | 计划文档写入已确认的决定和阶段 0 基线结果 |
| `38328a4` | 阶段 1：删死代码 |
| `7eb7b36` | 阶段 2：去掉 `cli.py`，每阶段一个脚本 |

行数（`.py`）：`src` 25997 → 24784，`scripts` 3990 → 165，`tests` 10855 → 10386。

## 3 各阶段做了什么

### 阶段 0 基线

结果和容差见计划第 9 节。要点：旧代码在固定种子下没有波动（两次标定逐 trial 一致，热图、采集数组和图像逐字节一致），所以后续对照按完全一致来卡。产物在实验单元 `outputs/experiments/simplify-baseline/`（Git 忽略），单元 README 里有 run 表。

### 阶段 1 删死代码

- 删了 7 个孤立模块：`data/export/molmobot_images.py`、`data/export/station_dataset.py`、`data/rendering/obstacle_views.py`、`data/rendering/rollout_video.py`、`data/visualization/benchmark_replay.py`、`data/visualization/navigation_map.py`、`data/evaluation/failure_analysis.py`（最后一个在删掉 no-edit 脚本后变成孤立）。
- 删了 11 个 no-edit 一次性脚本和 `scripts/experiments/` 里的 13 个诊断脚本；保留 `train_sac.py`、`score_stations.py`、`evaluate_sac.py`、`build_asset_index.py` 和 3 个安装脚本。
- 删了只测这些东西的测试（`test_rollout_video.py`、`test_failure_analysis.py`、`ObstacleViewTests`、一个 opt-in 渲染测试、两个测已删脚本的 no-edit 测试、CLI grid 回放测试）；`cli.py` 去掉 grid 回放。
- 静态 import 图检查：孤立模块只剩子进程方式启动的 `policies/molmobot_service.py`、`policies/pi05/service.py` 和 π0.5 数据转换 `data/export/pi05_dataset.py`、`policies/pi05/data_config.py`，都按计划保留。

### 阶段 2 入口

- 删掉 `cli.py` 和 `pyproject.toml` 的 `[project.scripts]`，新增 5 个阶段脚本：

| 脚本 | 参数 | 调用 |
|---|---|---|
| `scripts/build_scenes.py` | `--config <配方> --output <run 目录> --gpu …` | `workflows.scene_datasets.build_scenes` |
| `scripts/station_map.py` | `--dataset --config <运行配置> --output --gpu … [--samples] [--egl-devices]` | `workflows.collection.run_stations(collect=False)` |
| `scripts/collect.py` | 同上，加 `[--calibration]` | `workflows.collection.run_stations(collect=True)` |
| `scripts/bench_build.py` | `--dataset --calibration --config <benchmark 配置> --output` | `workflows.lastmilebench.build`（CPU） |
| `scripts/bench_evaluate.py` | `--benchmark --config <策略配置> --output --gpu` | `workflows.lastmilebench.evaluate` |

- 实验参数进配置：
  - 配方（`TaskModeRecipe`）新增 `build_workers`，默认 1。
  - 新增逐站位运行配置 `config.StationRun`：执行配置路径、`assets_dir`、`max_trials`、记录开关、`recording_mode` 和各类进程数，默认值与原 CLI 一致。示例 `configs/collection/curobo_v2.json`、`panda_omron_robocasa_curobo.json`，说明在 `configs/collection/README.md`（原 `smoke.json` 已删）。
  - 没有把这些字段放进 `ExecutionConfig`：采集时要拿执行配置和已有标定比对（`calibration_execution`），混进进程数会让比对失效。
- 原 CLI 的设备绑定抽成 `gpu.select_devices(gpu_ids, egl_devices)`，行为不变（第一张卡设 `CUDA_VISIBLE_DEVICES` 和 `MUJOCO_EGL_DEVICE_ID`）。
- 命令行里的路径仍按调用目录解释；配置文件里的路径按仓库根目录解释（和以前一样）。
- 不再提供的入口：`--recipe`（阶段 4 删代码）、`--replay-from` 离线回放、`--materialize-from` 物化（阶段 5 改成采集直接自包含）、`--preview-dataset`。`data/rendering/replay.py` 的 `render_run` 和 `scene_preview.export_scene_previews` 函数还在，只是没有脚本入口；要不要给它们留入口，需要用户定。

## 4 验证情况

- 单元测试：两个环境都用无渲染设置跑（见第 5 节命令），新旧代码剩下的失败和错误完全相同：
  - 主环境：基线 431 项（1 失败、1 错误），阶段 2 后 411 项（1 失败、1 错误）。
  - RL 环境：基线 433 项（1 失败、9 错误），阶段 2 后 413 项（1 失败、9 错误）。
  - 失败是已知的 `test_task_identity_survives_body_renumbering`；错误是两个环境各缺对方的依赖（主环境缺 mjlab/torch，RL 环境缺 cuRobo）。少掉的测试正好是删掉的那些，另外新增 `tests/workflows/test_scripts.py` 4 项。
- 每个剩下的模块都能 import，失败的只缺可选依赖。
- 阶段 2 对照：
  - 已做：`bench_build.py` 在基线的 `modes` + `cal-modes-a` 上重建（run `bench-p2`）。`episodes.jsonl`、`oracle.json` 逐字节一致，`summary.json` 除 `elapsed_s` 外一致；可视化 PNG 只有标题里的目录名不同。
  - **未做**：用新脚本重跑场景构建和 cuRobo 标定再和基线对照。这两步要用 GPU，场景构建还会渲染，按用户安排放到渲染设备隔离修好以后（见第 6 节）。这些对照仍未完成；用户最新要求优先代码构建，本轮没有补跑。

## 5 怎么跑

基线和对照都在实验单元里运行，`env.sh` 设好 `PYTHONPATH`（本 worktree 的 `src`）、EGL 库路径和两个解释器：主仓库 `.venv`（cuRobo 栈，MuJoCo 3.16.0）跑主链，`actor-critic` 的 `.venv` 跑 RL。本 worktree 没有自己的 `.venv`，不要在这里运行 `scripts/install.sh`。

```bash
cd /data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/simplify
source outputs/experiments/simplify-baseline/env.sh   # 定义 W、U、PY、RLPY

# 单元测试（不渲染、不碰 EGL；CUDA 只给一张空闲卡）
env CUDA_VISIBLE_DEVICES=<空闲卡> MUJOCO_GL=disable $PY -m unittest discover -s tests -t .
env CUDA_VISIBLE_DEVICES=<空闲卡> MUJOCO_GL=disable $RLPY -m unittest discover -s tests -t .

# 阶段脚本（GPU 那几步先看第 6 节）
$PY scripts/build_scenes.py --config $U/configs/scenes.json --output $U/runs/<新 run> --gpu <卡>
$PY scripts/station_map.py --dataset $U/runs/noedit --config configs/collection/panda_omron_robocasa_curobo.json \
    --output $U/runs/<新 run> --gpu <卡>
$PY scripts/bench_build.py --dataset $U/runs/modes --calibration $U/runs/cal-modes-a \
    --config $U/configs/bench.json --output $U/runs/<新 run>

# 对照
$PY $U/analysis/compare_calibration.py $U/runs/cal-a $U/runs/<新标定 run>
$PY $U/analysis/compare_dataset.py $U/runs/collect-mat <新的自包含数据集>
```

注意：基线 `scenes.json` 里没有 `build_workers`，按默认 1；新构建的 `config.json` 会多出这个字段，对照时忽略它。

## 6 GPU 和渲染：接手前必读

**2026-11-11 更新：设备绑定已修复，真实准备/规划子进程最小出图验证仅占 GPU 0，见最新交付。以下故障描述为修复前记录；完整业务对照仍未补跑。**

- GPU 0–3 平时空闲（有时是 zhanzhihao 在用），4–7 是 fengzhuoqian 长期在跑的进程（每张约 20 GB、利用率约 89%）。开跑前先看 `nvidia-smi` 和进程属主，只用空闲的卡，不干预别人的进程。
- **渲染设备隔离还没修，修好之前不要跑任何会渲染的运行**：场景构建（出预览图）、带录像的标定、采集、benchmark 评测、RL 验证视频。阶段 0 里，这类运行在指定卡之外的 GPU 4–7 上建过 EGL 上下文（每个约 1.5 GB）；为了查映射起的 4 个带录像的标定，spawn 出的子进程在 GPU 4–7 上各建了一个。用户发现后，这些进程已全部停止。
- 已知情况：CUDA 计算都落在指定的卡上，额外占用来自 EGL。`CUDA_VISIBLE_DEVICES` 管不住 EGL；这台机器上 EGL 设备号和 nvidia-smi 编号的对应关系还没查清（`eglQueryDeviceStringEXT` 查不到 DRM 节点，直接用 `mujoco.Renderer` 的探针报 `EGLError`）；规划池和站位准备 spawn 的子进程没有逐个绑定渲染设备。
- 修复方向：
  1. 在一张空闲卡上单进程实测 EGL 设备号和物理 GPU 的对应关系，一次只起一个进程，起来后马上看 `nvidia-smi`。
  2. 让入口和所有 spawn 的渲染子进程都显式绑定到同一张卡（入口在 `gpu.select_devices`；子进程设置设备的位置见 `workflows/collection.py`、`workflows/scene_datasets.py`、`workflows/lastmilebench.py`、`planning/pool.py`、`runtime/execution/workers.py` 里对 `CUDA_VISIBLE_DEVICES`/`MUJOCO_EGL_DEVICE_ID` 的设置）。
  3. 修好以后先跑一个最小的带渲染运行，确认只占指定的卡，再补阶段 2 的对照。
- 永远不要 `pkill -f` 一个在自己 ssh 命令里字面出现的模式（会杀掉自己的 shell）。要杀进程就先列出 pid，按 pid 杀，或者用 `[s]cripts/...` 这种括号写法。

## 7 接下来

1. 用户 2026-11-11 已要求继续精简、减少测试和实机测试、优先构建。本轮先完成阶段 7 中不依赖 GPU 的控制模块合并、策略配置统一和本体公共工具提取；40 项相关现有测试通过，见最新交付。阶段 7 的 execution_config 精简尚待阶段 4 删除旧功能后处理。
2. 渲染设备绑定已修复，并完成真实最小出图验证；阶段 2 的业务对照仍待完成。正常使用 --gpu 自动选择 EGL，需要覆盖时使用 --egl-devices。
3. 阶段 3 场景构建：用新的 `scenes/edits/` 替换 `modes/`、`build/`、`augmentation/`，换成 `SceneRecipe`，重写 `workflows/scene_datasets.py`，按计划 4.1、第 8 节第 5 条的默认值放障碍物。原场景这条路要和 `noedit` 的站位、快照一致；编辑后场景的结果本来就会变，检查接受率、各类拒绝原因和物理校验，并把编辑前后的对比预览图交给用户看。阶段 5、6 的逐位对照要以阶段 0 的 `modes` 快照为输入。
4. 阶段 4–8 按计划第 7 节执行。阶段 4 里 `pool.py`、`remote.py` 保留，只在内部精简（不做去留对比）。
5. 需要用户定的事：第 3 节末尾提到的、没有脚本入口的离线回放和预览补生成要不要保留入口。
