# LastMile Datasets v0：场景包 → cuRobo → 连续恢复数据

`collect-lastmile-datasets` 是独立薄编排入口。它复用现有 cuRobo collector、A*、raw/canonical recorder 和 MolmoBot adapter；不恢复原五阶段路线，不重新构造障碍物。

## 1. 运行入口

从 lastmile-datasets worktree 根执行，解释器显式设置为具备 MuJoCo 3.5 / pinned cuRobo 0.8.0 的环境。输入模型的 MuJoCo 版本必须兼容。

```bash
export DATAFLOW_PYTHON=<prepared-python>
export MUJOCO_GL=egl PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1

bin/lastmile-dataflow collect-lastmile-datasets \
  --dataset <sealed-scene-dataset-root> --assets-dir <readonly-assets-root> \
  --config configs/lastmile_datasets/v0.json --run-id <unique-run-id> \
  --gpu-ids 0 1 2 3 4 5 6 7 --workers 1
```

- 默认处理请求范围内的全部封存包；`--samples <sample-id> ...` 显式限制样本，仍处理这些样本的全部配置包。
- 初版按 sample/config 串行执行，`--workers` 必须为 1。GPU 从候选集合实时选择：利用率 ≤50%、空闲显存 ≥10000 MB，先低利用率，再高空闲显存。CUDA/EGL 绑定同一物理 GPU，不干预其它进程。
- `--preflight-only` 仅校验真实输入和配置，不初始化策略、不分配 GPU、不执行物理动作；输出单独预检 run。
- `--calibration-only` 完成逐点标定与选择证据，不采连续段；同一 run 使用 `--resume` 并去掉此开关可进入连续采集。
- `--resume` 只接受相同输入、配置、代码、诊断预算、导出设置；已有有效 trial 和登记的成功 S0→S1 连续段不重复执行；某个 S1 成功不跳过同一 S0 的其他 S1。正常终结的失败配对不自动重跑，基础设施/记录等异常配对可恢复重试。
- `--max-trials N` 是每个包的新标定尝试数诊断上限，不截取源 selected 集合。使用该开关的 run 不得冒充完整正式标定；不能去掉上限后强行 resume 同一诊断 run。
- 默认对审计合格的成功 Pick episode 导出 MolmoBot state/images；`--no-molmobot-export` 可将派生工作留到后续。

日志全部落 `outputs/logs/`。后台运行需记录 PID 和运行命令；不得将进程启动或退出码 0 描述为机器人任务成功。

## 2. 输入与执行协议

读取 scene-datasets 的 `packages.jsonl`。父 dataset 为 partial 时，只处理其中封存且检查通过的包，保留缺额。每个 sample/config 派生独立子 run，避免同 house 不同变体覆盖。

机器人配置来自包内 `scene/version.json`，任务来自包内 `task.json`。所有 `stations/selected.json` 的完整姿态照用，不生成 lattice。传感器分辨率、sampling geometry 与源包绑定一致；不以 320×240 数据冒充 native 1024×576。

首版仅接受 position-v1 cuRobo 配置，不暗中改为 motor。现有原 XML/metadata、机器人和抓取资产仍是只读依赖，输入缺失/摘要变化拒绝。采集强制 raw-first/depth，第三人称按 cuRobo 配置启用；源场景、旧输出不改写。

逐点默认 5 次独立试验，有限预算 success/failure/planning_no_solution/timeout 为有效结果。仿真、记录、初始化、基础设施异常及未执行不伪装成正常任务失败。正式选择要求全部 selected 点都达到计划有效 trial 数；旧错误 attempts 保留，同一 trial 不重复计数。封存结果在索引落盘前被中断时，可从既有 outcome/config 重新登记，不重写原 attempt。

默认 S0 实测率 ≤0.2，S1 ≥0.8，XY 距离 ≤1.0 m。每个 sample/config/task 尽量选择 3 个 S0，至少需要 1 个合格 S0；只有 1–2 个时保留并警告，0 个时为 `no_eligible_pair`。每个 S0 最多选择 5 个 S1（`max_s1_candidates=5`；LastMile 模式下 0 也按 5 处理），按成功率降序、距离升序、站位 ID 排序截取。首条成功后继续处理其余选定 S1，不用第 6 个替补无路径或失败的候选。复用 benchmark selector，只用实测计数。新模式不应用整体平均率 `discard_below`；未启用 LastMile 模式的旧 collector 规则不变。

高率邻点不证明通路。连续采集另用 A*，真实导航到站并复核 XY/yaw，再从实际状态规划操作。`begin()` 后不恢复快照、传送或重启。S0 标定失败是独立证据，不拼接成当前连续 episode。

## 3. 输出与状态

```text
outputs/no_edit/<parent-id>-unit-<identity>/    # 现有完整采集输出
  scenes/val_<house>/tasks/<task>/
    trials.json / station_statistics.json / calibration.json / selection.json
  scenes/val_<house>/attempts/<operation>/<station>/<attempt>/raw/、canonical/
outputs/lastmile_datasets/<parent-id>/
  run.json / progress.json
  calibration/<sample>/<config>/rates.json
  selections/<sample>/<config>.json
  audits/<episode>.json
  all_attempts_index.jsonl / lastmile_index.jsonl / train_index.jsonl
  unit_results.jsonl / exports.jsonl / summary.json
  manifest.json / checksums.json              # 完整执行后封存
outputs/derived_packages/<parent>-<episode>/   # 独立 MolmoBot 派生
```

公共率表沿用 `lastmilebench-station-rates-v1`，绑定 dataset/sample/config/model/task/源包摘要，附完整有效次数、配置摘要、selected 集合摘要与原 attempt 引用。原始记录遵循 [raw-first 协议](canonical-data-protocol.md)，不另创状态/动作格式。

索引路径相对父 publication 解析，外部子 run 的 canonical 摘要独立绑定。`all_attempts_index` 保留标定、连续及中断后尚未登记的真实 attempt；`lastmile_index` 保存连续结果和阶段引用；`train_index` 只纳入任务和导航成功、连续执行、仿真有效、记录完整且审计通过的 episode。

父 summary 的 completed 表示请求范围执行并汇总完成；`no_eligible_pair`、`no_successful_rollout` 可以是完整的负结果。实际成功数据看 `train_episodes/lastmile_success_data`，不能看退出码或 completed。adapter 失败单列，不抹掉成功 raw 数据。

完整执行的父 publication 封存后不再变更；重新 resume 已封存 run 仅校验并返回。calibration-only/incomplete 仍为可恢复检查点，不封存为完整连续数据交付。

## 4. MolmoBot 与 benchmark 的边界

position 数据默认只自动导出 state22 + 图像；没有 motor 标定时，不提供 native action 监督。后续沿用已有命令：

```bash
bin/lastmile-dataflow export-molmobot --attempt <audited-attempt> \
  --output outputs/derived_packages/<new-export> --gripper-map <calibrated-map.json>
```

映射须含显式标定来源，不能猜测 position→motor 等价。Open 另需显式首帧 head 的 `--conditioning-point X Y`；缺提示不阻止原始采集。详见 [adapter 使用说明](raw-first-implementation.md)。

LastMileBench 共用率表和低/高选择；native benchmark 仍强校验其模型/robot_sensor_config 绑定。position 率表不能换标签后冒充 native 同配置标定，匹配 native 标定不是本采集入口的前置条件。

## 5. 验证与证据

软件回归：`PYTHONPATH=src:tests <python> -m unittest test_lastmile_datasets test_lastmilebench test_canonical_data test_no_edit test_native_station_input -v`。合成/mocked 测试不代表真实 CUDA 或操作成功。

方案见 [REPORT.md](../../reports/lastmile_datasets/REPORT.md)。真实执行状态与剩余限制见 [实施报告](../../reports/lastmile_datasets/IMPLEMENTATION.md)；具体数字必须以新 run 的 rates/selection/summary/审计为准。

## 6. evidence-v2：物理结果不再被墙钟状态遮蔽

新 attempt 的 `execution_semantics_version=no-edit-evidence-v2`。严格成功阈值不变：双指承力、抬升至少 5cm、无外部支撑、滑移受限并稳定至少 2s，还须通过完整 trace 的证据和约束检查。

- lift/hold 的每个已执行控制区间边界检查成功；满足完整判据即结束。固定 50 个 hold 区间仅为最大保持预算，不再是成功后必须额外等待的长度。
- timeout 收尾用同一完整判定器核对已有 Pick trace；分别保存 `execution_termination` 和 `task_outcome`。若证据已经成功，兼容旧 collector 的顶层 `status=success`，但保留 `execution_termination.status=timeout`；缺失、异常或不合格证据不升级为成功。
- 新增 `task_evidence_initial.json` 保存真实 monitor 基线/指尖 IDs/control，避免后续复核猜初值。Open 超时暂保持 unknown，不套用 Pick 标准。
- `execution_diagnostics.json` 记录墙钟预算/耗时、仿真时长，以及规划、渲染、记录、物理仿真与证据检查的计时。`simulation_and_physics_evidence` 包含逐物理 tick 的 monitor/trace 写入；这些计时不是无遗漏的性能 profiler。recorder 封存耗时在父 trial 返回的 `execution_diagnostics.recorder_finalize_wall_s` 中记录，不改写已封存 attempt。
- 预算仍为原配置值；本修复不扩大 timeout。代码摘要和执行语义变化必须使用新 run，不能 resume 原封存 run。

独立 `review-lastmile-timeouts` 复核入口已删除。执行中的超时收尾与真实成功判定保留，不依赖二次复核 publication。

修复与验收见 [TIMEOUT-FIX.md](../../reports/lastmile_datasets/TIMEOUT-FIX.md)。

## 7. 同起点多终点采集

同一 S0 的各条轨迹从相同冻结场景和完整机器人初态初始化，每条使用独立 Simulation/attempt；执行开始后禁止重置、传送或拼接标定失败轨迹。非 LastMile 模式保留旧的首条成功即停止行为。

`selection.json` / task result 新增 `start_groups`，记录每个 S0 的选定 S1、不同成功终点数、最低要求和警告。父 `unit_results.jsonl` / summary 的组统计额外提供 `train_s1` / `train_s1_count`，按实际进入 train_index 的不同 S1 计数，而不是将未通过审计的物理成功计入可发布成功解。

- 每个 S0 有至少 1 个成功且审计通过的连续解即可入集；只有 1 个时警告 `single_successful_solution`，不阻止发布。
- 0 个成功解的组标记 `no_audited_successful_solution`，不贡献训练轨迹；其他 S0 的合格轨迹不因此丢弃。
- 不足首选 S0 数时警告 `fewer_than_preferred_starts`；无合格 S0 或无成功解仍保留真实负结果。
- 新代码/配置必须使用新 run ID，不修改既有封存数据，也不能用新配置 resume 旧 run。
