# 第二轮精简总结

- 日期：2026-11-12
- 分支：`refactor/simplify`，基线 `bfd707d`，最终 `c515ca1` 加本文所在提交（未推送）
- 方案：`../plan/02-further-simplify-plan.md`；逐条过程、验证和默认决定见 `11-second-pass-progress.md`

## 提交

| 批 | 提交 | 内容 |
|---|---|---|
| A（runtime） | 8269355、0110d07、9df3d22、73bd644、d110939、d4b8130 | Simulation 来源字段和 `derive()`；cuRobo/VLA 共用试验外壳；机器人适配器共用 `JointMap` 与一张 `ROBOTS` 表；判定合成 `outcomes/task.py`；`GraspPlan`；锁死字段改常量；`SettleReport` |
| B（planning/scenes） | 31bf406、a7a11bb、2111a9f、ee2e35f | 删死代码；planner 的 grasp 共用一套流程、一个 `planner.json`；astar 共用 Dijkstra；`BuildContext` dataclass；拆场景图和任务发现 |
| C（data） | d71a126、bab797e | 相机与分割掩码合一；标定统计合成一个模块；删数据导出里的死路径；新增 `scripts/replay.py` |
| D（policies） | cfb992c、b614068 | π0.5 导出拆函数并有 `scripts/export_pi05.py`；两个策略服务共用 `serve()`；`PolicyProcess`、`PolicyAction`；移植代码删 11 个无引用函数 |
| E（workflows、RBY-1） | c8acd48 | 删旧的逐场景标定引擎；标定与采集分成两个入口；调度器队列改 dataclass；拆长函数 |
| 配置 | 16e5831 | 输入配置改 YAML、只写非默认字段；删 `configs/collection/`；Panda 四份执行配置合一；`data_root` 统一 |
| F（格式） | f307943 | `ruff format`，行宽 120，开 E702/E703 |
| G（目录分层） | 7f40d58、5632764、a986427、c515ca1 | 迁移前的代码改动；纯 `git mv` 加 import 改写；分层测试与测试重排；文档 |
| H | 本文 | 总结 |

## 实际改动

前六批的改动逐条记在进度文档第 1–18 条，这里只列 G 批。

**目录结构**按方案第二节（含 11-12 用户的两次调整）改成按 pipeline 分层：

```
utils → sim/model → robots → sim → planning → operation → policies → scenes → station_map → lastmile_dataset、lastmile_benchmark
```

`runtime/`、`data/`、`workflows/` 消失。robots 分 `rby1/`、`panda_omron/` 两个子目录，公共件在根下；站位采样放在 `station_map/sampling/`；标定调度是 `station_map/planner_trials/build.py`。新增 `tests/test_layers.py`，遍历全部 import（含函数内）检查只向下引用、并列部分互不引用、`__init__.py` 为空。跨顶层包写绝对导入，包内写相对导入。

**迁移前做的代码改动**（7f40d58，方案第二节第 0–3 条）：

- 机器人落位、初态检查、接触过滤、底盘轮廓、停放下沉到 `sim/placement.py`，地面三角形与地面射线并入 `sim/model/geometry.py`，执行层不再引用 scenes。为消掉倒挂，`OperationFailure`、`NATIVE_MAX_ATTEMPTS`、`clone_preparation`、`task_support_obstacles`、`disk_samples` 也各自挪到下层或用它的那一层。
- `config.py` 拆成 `utils/io.py`（通用读写与路径）、`robots/registry.py` 和两份机器人配置、`operation/config.py`（并入 `CollectionConfig`）、`station_map/config.py`。
- **站位采样独立成一步**：场景构建只看物理检查就接受，不再采站位；新增 `scripts/sample_stations.py`，逐场景重开快照、按原规则选站位，结果写回场景库的 `stations.jsonl`，没有合法站位的场景在这一步淘汰；场景预览改在这一步导出；下游通过 `scenes/library.load_scenes` 按 scene_id 合并站位，读法不变。
- **`run_curobo_attempt` 拆两层**：`operation/curobo.py` 的 `CuroboAttempt` 只做站位上的一次操作；`lastmile_dataset/continuous.py` 的 `ContinuousAttempt` 在它前面加导航、操作重试和导航记录。结果字段不变。
- RL：`sampler_entry`、`isolate_*` 挪到 `station_map/rl/devices.py`；消掉 rl_training 与 rl_evaluation 的循环引用；函数内 import 提到顶部（只留 mjlab 的 `StationEnv` 和绑卡入口）。
- 删除只有测试在用的 `PreparedScene/prepare_scene` 和 `SettleConfig.graph_config()`；`panda_factory.py` 挪到 `tests/smoke/`。

**迁移本身**（5632764）：113 个文件 `git mv`，Git 都识别为改名；除 import 外只改了 Panda 资产路径、`pyproject.toml` 的 package-data 和 ruff 排除路径，删掉 6 份子目录 README（内容并入 `docs/architecture.md`）。

**输出格式的变化**（都已写进 `docs/data-format.md`）：`scenes.jsonl` 不再有 `stations`、`station_sampling`、`station_hints` 和构建记录里的站位计数；场景库多了 `stations.jsonl`、`stations_summary.json`；预览由采样这一步生成。

## 验证

- 单元测试：最终 325 项（基线 342 项，删了只测已删功能的用例，新增站位合并、站位淘汰报告和两项分层测试），只有 `test_rl_rewards` 因主环境没装 mjlab 导入失败，属已知。actor-critic 的环境跑 RL 相关 9 个测试模块通过。
- 全部 6 个阶段脚本、`replay.py`、`export_pi05.py`、`build_asset_index.py`、3 个 RL 实验脚本、8 个冒烟脚本 `--help` 通过；`ruff check` 通过；另用脚本核对全部 import（含函数内）都能解析。
- GPU 回归：只用 GPU 0，实验单元 `outputs/experiments/simplify-g-regression/`。场景构建 → 站位采样 → 标定 → 采集 → benchmark 构建都跑通（结果见下）。按用户 11-12 的要求只验证能跑，策略评测没有跑。
- E 批的 RBY-1 新旧调度器对照：9 个场景 81 个 trial，逐 trial 的 status/reason/executed_steps/failure_scope 全部一致，9 张热图逐数组相同，旧引擎约 5 分钟、新调度器 69 秒；这批场景 RBY-1 一次都没成功（重构前 bfd707d 同样），规划成功之后的执行段没有比到。

## 结果

| 步骤 | 结果 |
|---|---|
| 场景构建 | 2/2 接受，编辑摆放和任务字段与重构前的 scene-check 相同 |
| 站位采样 | 2/2 场景各 5 个站位；站位 id、底座位姿、净空、可见性结论与重构前构建时采的逐个相同 |
| 标定 | 30 trial：28 成功、2 失败，完整，213 秒 |
| 采集 | 4 个 S0→S1 配对全部执行，3 成功、1 失败，导出 3 条连续示教（S0 阈值在单元内的配置副本里改成 0.7，否则这批没有 S0） |
| benchmark 构建 | 2 个 episode |

## 源码行数

第 18 条的格式化把密排行拆开，会让行数变多，所以基线另用同一份 ruff 配置格式化后再数（同口径）。移植代码（`multitask/`、`pi05/core/`）不格式化，单列。

| | 基线 bfd707d 原样 | 基线格式化后 | 最终 | 变化（同口径） |
|---|---|---|---|---|
| `src/`（不含移植代码） | 19085 | 21745 | 20222 | −1523（−7.0%） |
| `src/` 非空非注释行 | — | 19424 | 17664 | −1760（−9.1%） |
| `tests/`（不含 smoke） | 7936 | 8721 | 7951 | −770（−8.8%） |
| `tests/` 非空非注释行 | — | 7790 | 7028 | −762 |
| 移植代码 | 5972 | 不格式化 | 5844 | −128 |
| `configs/` | | | | 44 个文件，+257/−765 |

方案预计源码减少 2600–3200 行，实际同口径约 1500–1760 行：新增了站位采样一步、分层测试、显式 dataclass 和拆分后的函数签名，部分抵消了删减；方案里有几项按默认决定没做（见进度文档“剩余”）。

## 限制

- 迁移后的代码只在最小规模上跑通，没有做完整回归；策略评测（bench_evaluate）和 RL 训练没有在迁移后的代码上跑过真实 GPU。
- 站位采样单 GPU 顺序执行；编辑搜索失去站位反馈后，编辑场景采不到站位会在采样这一步淘汰，编辑预算没有调（没有数据依据）。
- 旧标定 run 不再保证可复用（第 5、16 条）；重构前生成的场景库没有 `stations.jsonl`，要先跑一次 `sample_stations.py`。
- **actor-critic 分支上的 RL 改动以后合并进来时，冲突会比原来多，需要手工解**：RL 文件都搬了位置（`runtime/rl/`、`workflows/rl_*`、`policies/sac/`、`scenes/rl_observation.py`、`scenes/stations/rl.py`、`scenes/loading/rl_scene.py` 等都进了 `station_map/rl/`），import 改成新路径，`graph_observation`、`seed_all`、`isolate_*`、`sampler_entry` 换了模块，`local.py` 并进了 `rl_scene`，配置改成了 YAML。

## 默认决定（摘要）

完整清单在进度文档“默认决定与用户补充决定”一节，这里只列影响较大的：

- 11-12 用户：按默认决定一直做完；RL 文件可以修改；robots 独立成顶层、按机器人分子目录；gpu 进 utils；站位采样放 `station_map/sampling/`；`planner_trials/scheduler.py` 改名 `build.py`；回归只验证能跑。
- 按分层原则调整：`station_map` 内部 config/sampling/aggregate/heatmap 在下、planner_trials 与 rl 在上；π0.5 训练数据转换放 `lastmile_dataset/pi05_export.py`（放 policies 会倒挂）。
- 站位采样结果写回场景库目录，下游仍只收一个路径；预览挪到采样这一步。
- 锁死字段改常量、三个非有限标签合一、planner 小 JSON 合成 `planner.json`、heatmap.json 删说明字段、`discover_tasks` 的枚举比较 bug 直接修（新建场景库会多出 open 任务）。
- 可选重依赖（cuRobo、mjlab 等）在用到处 import；`PICK_THRESHOLDS` 仍是 dict；冒烟脚本自带的配置放 `tests/smoke/`。
