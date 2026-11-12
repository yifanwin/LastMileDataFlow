# 配置、脚本与报告整理

- 日期：2026-11-12
- 分支：`refactor/simplify`，基线 `780e3f2`，提交 `02b101c`、`ae80fcf`、`59a2801`、`2225e44`、`c5358a1` 加本文所在提交（未推送）
- 范围：起初只动路径、命名和命令行组装；11-12 用户取消了“不改源码逻辑”的限制，此后改动行为的地方逐条写在第 2、5 节

## 1. 配置

原则：`configs/` 的子目录名就是加载这份配置的源码包名。执行配置的 `--config` 定义在 `station_map/config.py`，由 `station_map/planner_trials/build.py` 用 `operation.config.load_execution_config` 解析，标定和连续采集共用；benchmark 评测不读执行配置，只读策略配置。

| 原路径 | 新路径 |
|---|---|
| `configs/execution/{molmobot,panda_omron_curobo,pi05_panda_omron,pi05_rby1,rby1_curobo}.yaml` | `configs/station_map/` 同名文件 |
| `configs/rl/val103_cup30.yaml` | `configs/station_map/rl/val103_cup30.yaml` |
| `configs/benchmark/panda_omron.yaml` | `configs/lastmile_benchmark/panda_omron.yaml` |
| `configs/scenes/`、`configs/robots/`、`configs/policies/`、`local.example.yaml` | 不变 |

全部 14 份配置都用各自的加载器实际读了一次。

## 2. 脚本

| 原路径 | 新路径 |
|---|---|
| `scripts/install.sh`、`install_pi05.sh`、`install_rl.sh` | `scripts/install/main.sh`、`pi05.sh`、`rl.sh` |
| `scripts/build_scenes.py`、`build_asset_index.py` | `scripts/scenes/build.py`、`build_asset_index.py` |
| `scripts/sample_stations.py` | `scripts/station_map/sample.py` |
| `scripts/station_map.py` | `scripts/station_map/planner_trials.py` |
| `scripts/experiments/train_sac.py`、`score_stations.py`、`evaluate_sac.py` | `scripts/station_map/rl/train.py`、`score.py`、`evaluate.py` |
| `scripts/collect.py`、`export_pi05.py` | `scripts/lastmile_dataset/collect.py`、`export_pi05.py`（转换代码在 `lastmile_dataset/pi05_export.py`） |
| `scripts/bench_build.py`、`bench_evaluate.py` | `scripts/lastmile_benchmark/build.py`、`evaluate.py` |
| `scripts/replay.py` | `scripts/operation/replay.py` |

写法统一（已写进 AGENTS.md「脚本写法」）：

- argparse 对象一律叫 `parser`；`add_station_run_arguments(cli)` 的参数、`tests/test_scripts.py` 的变量和测试名、两个冒烟脚本里的 `p` 都改了。
- import 放顶部。不绑卡的 `lastmile_benchmark/build.py`、`lastmile_dataset/export_pi05.py`、`scenes/build_asset_index.py` 全部提到顶部；`scenes/build.py`、`station_map/sample.py`、`station_map/planner_trials.py`、`lastmile_dataset/collect.py`、`lastmile_benchmark/evaluate.py`、`operation/replay.py`、RL 三个脚本和 `tests/smoke/rl.py` 先绑卡，绑卡后的那一处导入上方有注释。顶部的轻量模块逐个核实过不会间接导入 mujoco/torch。
- 主流程调用先赋给 `result` 再打印；`json.dumps` 照旧写在 `print` 里。src 里用 AST 扫了一遍 print/日志语句，没有嵌着有副作用调用的写法，没有改动。
- RL 入口：删掉 `training.run_cli(mode)`、`devices.isolate_from_argv`、`isolate_training` 和对应的 3 项测试；三个脚本各自声明参数，先 `parse_args()` 再 `select_devices`，不改写 `sys.argv`。原来的 `--gpus` 并不是只解析不用：`isolate_training` 从 argv 读它，经环境变量 `LASTMILE_SAMPLER_GPUS` 交给 `train()` 给 Sampler 分卡。所以保留了多卡功能，改成 `train.py --gpu 0 5`（1–8 张互不相同）。`--gpu` 现在一律是物理卡号，也不再有默认值（原来训练默认 0）。
- **RL 接口变化**：`train(config, *, gpus, output, seed, ...)` 取代原来的 `gpu=<进程内编号>` 加环境变量；`score(..., gpu=...)`、`evaluate(..., gpu=...)` 收物理卡号。绑定后进程内编号的换算只在 `station_map/rl/devices.VISIBLE_DEVICE = 0` 这一处，Learner、score、evaluate 和 Sampler 子进程都用它。`learner_gpu`、`sampler_gpus` 字段名和值不变；`runtime_metadata` 里的 `gpu_argument` 从进程内编号 0 变成物理卡号（与 `physical_gpu` 相同）；score 的 `station-q.json` 和 evaluate 的 `summary.json` 多了一个 `physical_gpu` 字段。
- `tests/smoke/rl.py` 拆成入口（参数、绑卡）和 `rl_checks.py`（检查本体），后者在绑卡之后导入。

## 3. 文档

- `docs/运行手册.md` 改名 `docs/runbook.md`，全仓库的引用都改了；`grep 运行手册` 只剩正文里的中文称呼。约定（docs 文件名英文小写加连字符）写进 AGENTS.md。
- README、AGENTS.md、configs/README.md、docs/*、tests 里的配置和脚本路径都已同步；保留下来的 RL、π0.5 运行指南里的命令路径也改了。π0.5 指南的其余命令（`--output-dir --run-id` 等）原本就是 10-18 的旧命令，开头加了一句以 runbook 为准。

## 4. 报告

删除（任务 4）：原 `reports/archives/`（9 个主题，63 份）和 `docs/archives/`（23 份），共 86 份 Markdown，没有被代码、测试或当前文档当作输入；两处文字引用改成“已删除，见提交 780e3f2 的 Git 历史”。

归档到新的 `reports/archives/`：

| 原位置 | 新位置 | 理由 |
|---|---|---|
| `task-modes/` | `archives/task-modes/` | 八类任务模式已删除 |
| `info/2026-10-25-seed-inventory-build-to-calibration.md` | `archives/info/` | 针对旧 `build-scenes`/`evaluate-stations` 链路的一次性盘点 |
| `benchmark/docs/运行指南.md` | `archives/benchmark/docs/` | 旧命令和 JSON 配置，已由 runbook 取代 |
| `panda-omron/docs/panda-omron.md` | `archives/panda-omron/docs/` | 指向已不存在的 phase01 worktree 和旧配置 |
| `collision-handing/archive/`（03、04） | `archives/collision-handling/` | 主题内原有的归档，已被局部碰撞方案接续 |
| `curobo-time-optimization/archive/2026-10-24/` | `archives/curobo-time-optimization/2026-10-24/` | 主题内原有的归档，已被当前方案接续 |

主题内原有的两个 `archive/` 没有直接删：当前报告（no-edit-calibration 交付、局部碰撞方案、加速现状）还把它们当证据引用，所以统一搬进 `reports/archives/`，链接已重算。

改名与分类：`collision-handing` → `collision-handling`，`raw_asserts` → `raw-assets/analyze/`；各主题的 `info/` → `analyze/`，`docs/` 下的运行指南 → `delivery/running.md`（RL 的算法说明 → `delivery/algorithm-training-pipeline.md`），`no-edit-calibration/decisions/` → `plan/`。相对链接按新旧位置脚本重算；全仓库 Markdown 的链接检查没有新增断链（剩下的断链原来就有，指向已删的源码、外部仓库或其他 worktree）。`reports/README.md` 重写为目录约定、当前状态、主题和归档四段。

保留但拿不准、请用户定：

- `no-edit-calibration/`：10-27 的旧标定实验与归因，Oracle 边界分析里的调整还没实施。
- `build-lastmile-datasets/`：首轮数据集交付描述的“独立物化”入口现已不提供。
- `curobo-time-optimization/`、`panda-omron/phase_01` 的 10 月实验记录：当前代码延续了它们的方案，但实验数字是旧版本的。

## 验证

- 每个提交前跑全量单元测试：最终 324 项（删掉 3 项 isolate 测试，新增 2 项命名空间测试），只有 `test_rl_rewards` 因主环境没装 mjlab 导入失败，属已知。
- actor-critic 环境（`.worktrees/actor-critic/.venv`，PYTHONPATH 指向本 worktree 的 src）：RL 相关 10 个测试模块加 `test_gpu`、`test_layers` 共 32 项通过；RL 三个脚本和 `tests/smoke/rl.py` 的 `--help` 通过。
- 主环境 12 个脚本的 `--help` 通过（`test_scripts.py` 覆盖其中 9 个）；`ruff format --check`、`ruff check` 通过；安装脚本 `bash -n` 通过。
- 真实 RL（actor-critic 环境，先查 nvidia-smi，只用空闲的 GPU 2、3）：
  - `tests/smoke/rl.py --gpu 2 --num-envs 1 --steps 2`：修第 5 节的命名空间之前在构建环境时失败，修后通过（909 个合法站位，2 步采样）。
  - `scripts/station_map/rl/train.py --gpu 2 3 --num-envs 4 --max-transitions 512`（`MUJOCO_GL=disable`）：两张卡各起一个 Sampler、Learner 正常，checkpoint 元数据为 `sampler_gpus ['2','3']`、`learner_gpu '2'`、`physical_gpu 2`；跑到第一次验证录像时因为关了渲染而停下，属预期。临时产物已删。
  - `MUJOCO_GL=egl` 跑不起来：pro5000 上现在没有 `libEGL.so.1`（只有 `libEGL_nvidia.so.0`，没装 libglvnd），`select_devices` 查询 EGL 设备的子进程直接失败，主环境和 RL 环境都一样。这是机器环境问题，与本次改动无关；docs/setup.md 已把 `ldconfig -p | grep libEGL` 列为前提。需要渲染的验证（场景预览、录像、标定的 visual 模式）在补装之前都跑不了。

## 5. 修掉的遗留问题

- **RL 机器人命名空间**（第二轮遗留，16e5831 起 RL 训练、打分、评测都在构建环境时报 `TypeError: unexpected keyword argument 'namespace'`）：16e5831 把命名空间改成固定返回 `robot_0/` 的属性，但 RL 场景编进 mjlab 后元素名多一层实体前缀，是 `robot/robot_0/`，`station_map/rl/env/stations.py` 仍按旧字段传参。现在 `station_map/rl/env/scene.py` 是唯一来源：`ROBOT_ENTITY = "robot"`、`ROBOT_PREFIX = "robot/" + ROBOT_NAMESPACE`，`EntityPandaConfig`（`PandaOmronConfig` 的子类，只把 `namespace` 换成带前缀的值）；两处构造合成 `RLSceneConfig.robot_config(entity=False|True)`。`environment.py`、`terms.py`、`observation.py`、`video.py` 里硬编码的 `"robot/robot_0/"`、`"robot/"`、`"robot_0/"` 和实体名 `"robot"` 都改成引用这几个常量。配置字段不变（`asdict` 与原来相同），输出不变。新增 `tests/station_map/test_rl_scene.py`（2 项，主环境也能跑）。

## 未处理


- `policies/serving.py` 里还有一个 argparse（`--config --socket`）：这是 PolicyProcess 拉起策略服务子进程时的进程间协议（还要在 `.venv-pi05` 里跑），不是用户命令入口；挪到 scripts 会让 src 依赖 scripts 的路径，所以没有动，是否在 AGENTS.md 里列为第二个例外请用户定。
- pro5000 缺 `libEGL.so.1`，见“验证”。
