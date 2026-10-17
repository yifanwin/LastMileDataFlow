# 困难 S0 benchmark 现状梳理

状态：active
日期：2026-10-17
替代：无（首份现状文档，旧实测记录见 [归档 benchmark 报告](../archives/benchmark/REPORT.md)）
关联：[运行手册](../../docs/运行手册.md)、[数据格式](../../docs/data-format.md)、[报告首页](../README.md)

**结论：两条命令 `build-bench` / `evaluate-bench` 的软件实现完整、判定与统计口径明确；但至今没有跑过带标定的正式困难 S0 评测。唯一真实运行是归档中的两条未标定 smoke，均以 600 s 墙钟超时结束，TSR 0/2，不能作为泛化或难度结论。**

本文只描述当前源码可支持的现状；归档记录属于旧分支与旧命令名，不作为当前能力承诺。

## 1. 命令与代码入口

| 项 | 位置 |
|---|---|
| 命令定义 | `src/lastmile_dataflow/cli.py:43-56`（`build-bench` / `evaluate-bench`） |
| 分发 | `src/lastmile_dataflow/cli.py:103-112` |
| 编排 | `src/lastmile_dataflow/workflows/lastmilebench.py`（`build` / `evaluate`，167 行） |
| 选择与汇总 | `src/lastmile_dataflow/data/evaluation/benchmark.py`（`select_starts` / `summarize`） |
| 构建期标定校验 | `src/lastmile_dataflow/data/evaluation/selection.py`（`station_calibration`）、`src/lastmile_dataflow/runtime/execution_config.py`（`execution_profile`） |
| 原生场景转换 | `src/lastmile_dataflow/runtime/construction/benchmark_scene.py`（`native_scene`） |
| 策略执行 | `src/lastmile_dataflow/runtime/execution/molmobot.py`（`run_vla_attempt`） |

固定预算（可经 CLI 覆盖）：`horizon=400`（40 s 仿真）、`wall_timeout_s=600`、`radius_m=1.5`、`starts_per_sample=1`、`seed=42`。

## 2. 构建：困难 S0 的选择规则

`build` 逐场景执行：场景库 → `native_scene()` 转原生（motor 夹爪、1024×576、10 Hz）→ 校验 `config.json` 的 `profiles[scene_id]` 与当前 native 机器人/相机/策略配置一致 → 要求完整逐点标定 → 选起点 → 校验站位初始化 → 写 `episodes.jsonl`。

`select_starts` 的选择依据是**完整逐点实测率**，不是平滑热图：

| 条件 | 取值 |
|---|---|
| 低率 S0 | `successes / trials ≤ 0.2`（含等号） |
| 附近高率点 S+ | `≥ 0.8`，仅作构造证据 |
| 邻近距离 | 两站位 XY 直线距离 ≤ 1.0 m |
| 位置合法性 | `geometry == 'valid'`，站位 ID 不重复 |
| 绑定校验 | 标定的 `backend` / scene id / task id 必须与当前场景一致，否则 `ValueError` |
| 计数校验 | `trials > 0` 且 `0 ≤ successes ≤ trials`，否则 `ValueError` |

未实现的部分保持未实现：**不验证底盘路径**、不计算难度分级、不强制发生底盘移动，`recovery_verified` 记作 `observed_station_rates_only`。同一场景无符合低率/附近高率组合时该场景跳过并告警，不退回任意起点。

站位有效性的构建期检查：无机器人接触（`robot_contacts`）、地面支撑成立（`floor_support`）、原生 head 视角可见像素 ≥ 24，任一失败抛 `ValueError` 而不是静默接受。

标定缺失时的行为：`build` 默认拒绝，必须显式 `--allow-uncalibrated-smoke`，且会打 `未标定 smoke：结果不能作为困难 S0 评测` 告警；incomplete 标定直接抛错。

## 3. 评测：独立 MolmoBot 串行运行与统计

`evaluate` 对 `episodes.jsonl` 逐条新建 `Simulation` 与 attempt，不恢复快照、不传送、不重用共享仿真；基础设施/模型故障停止当前批次。结果追加写 `results.jsonl`，收尾写 `summary.json`。

`summarize` 的统计口径：

- `coverage = 有效任务结论数 / 计划 episode 数`；
- `task_success_rate` **仅在 `unknown == 0` 时给出**，否则为 `null`；
- `timeout` 计为任务失败；`initialization_error` / `simulation_failure` / `recording_error` / `infrastructure_error` 为 unknown，不冒充失败；
- 同时给出 `observed_task_success_rate`，避免只统计完成子集造成虚高；
- 重复或外来 episode 结果直接拒绝统计。

即 benchmark 同时报告覆盖率与完整计划集合成功率，未完成试验不补入分母。

## 4. 实测现状

归档记录（`reports/archives/benchmark/REPORT.md`，旧分支 `feat/lastmilebench-design`，旧命令名 `build-lastmilebench` / `evaluate-lastmilebench`）：

| 项 | 结果 |
|---|---|
| 运行类型 | 两条**未标定 smoke**，非正式困难 S0 |
| Task Success Rate | **0/2（0%）**，coverage 100%，infra/unknown 0 |
| 结束原因 | 两条均 600 s 墙钟超时（实际 policy 动作 392 / 325，底盘位移 0.24 / 0.20 m） |
| 模型 | MolmoBot-RBY1Multitask 真实权重，CUDA 6 / RTX 5880 Ada |
| 链路证据 | 执行、物理判定、计数、canonical 审计、原生数据包导出均跑通 |

**0% 是这套统一预算下的 smoke 结果，未走到 400 步上限，不能断言更长预算下也失败，更不能作泛化结论。**

当前工作区状态：`outputs/` 为空，仓库内不存在任何 benchmark 产物；`.venv` 尚未创建，本轮未运行任何真实命令。

## 5. 缺口

1. **关键缺口**：没有与 native 派生配置（motor 夹爪、1024×576、对应站位初始化）匹配的 cuRobo 站位成功率图，因此始终无法构建正式困难 S0 benchmark。补上该标定后重新 `build-bench` 才会替换 smoke 起点。
2. 未运行带 `--calibration` 的正式构建与评测，未验证非空 benchmark 的端到端结果。
3. 未验证成功操作示教与困难 S0 恢复能力；`--allow-uncalibrated-smoke` 只做启动检查。
4. benchmark 仍绑定 RBY-1，PandaOmron 迁移（[panda-omron](../panda-omron/requirement/01-panda-omron.md)）尚未实施，迁移后需重建站点与标定。

软件边界已有测试覆盖（`tests/workflows/test_benchmark.py` 10 项、`tests/workflows/test_benchmark_progress.py` 2 项），只验证选择、计数、native 转换与汇总逻辑，**不代表真实任务成功**。

## 6. 下一步

补齐匹配的 cuRobo 逐点标定后按运行手册执行，全部使用新输出目录：

```bash
.venv/bin/lastmile-dataflow build-bench \
  --dataset <scene-dataset> --calibration <matching-native-calibration> \
  --output <new-bench-dir> --policy-config configs/policies/molmobot.json --gpu "$GPU"

.venv/bin/lastmile-dataflow evaluate-bench \
  --benchmark <new-bench-dir> --output <new-evaluation-dir> \
  --policy-config configs/policies/molmobot.json --gpu "$GPU"
```

无合格困难起点时 benchmark 为空，此时不运行评测，保留负结果。
