# 双本体配置修正与 20 点 cuRobo 标定启动

状态：active  
日期：2026-10-18  
替代：无；原 3 点结果保留为历史局部结果  
关联：[标定需求](../requirement/02-embodiment-full-calibration.md)、[首轮 benchmark](04-panda-benchmark.md)

已修改配置、完成候选物理检查，并按用户最后确认的“均匀分布、最终上限 20 点”启动两本体标定。当前是启动记录，尚无完整成功率结论。

## 配置与采样结果

范围半径取各自固定肩枢轴到 TCP 臂展 L，网格间距取各自底盘含车轮半径 B 的四分之一。候选均执行物理合法性检查；可见性只记录，保守导航网格不作为物理资格门槛。合法点按现有方位/距离分层与最远点规则覆盖压缩，最终每场景最多 20 点，完整执行全部重复数。

| 本体 | L / B（m） | 候选 | 物理合法 | 最终点 | 每点重复 | 计划 trials | GPU |
|---|---|---:|---:|---:|---:|---:|---:|
| Panda-Omron | 0.9410662915 / 0.4382921400 | 233 | 86 | 20 | 2 | 40 | 0 |
| RBY-1 | 0.7928056080 / 0.4435184160 | 161 | 31 | 20 | 5 | 100 | 1 |

两场景均为 val103 / Cup_30。Panda 保持源快照的 RoboCasa 控制与初始姿态；RBY 保持 position 控制。Panda 源数据为 `outputs/scene_datasets/panda-robocasa-bench-01`；RBY 源数据为 `../refactor-light/outputs/scene_datasets/small3` 中已接受的 Case1，原拒绝的 Case2 未加入。这里不是整个场景库的全量标定。

两本体的模型 FK 数值复核记录在 `outputs/diagnostics/full-calibration/measurements.json`，脚本为同目录 `measure.py`。L 是关节限位内多起点数值搜索得到的固定自由空间臂展，不是全局最优证明，也不保证每个站位均可达。

物理候选结果保存于 `outputs/scene_datasets/{panda,rby1}-full-calibration-02`；最终 20 点数据集保存于同前缀 `-03`。后者复用已经检查过的候选及冻结快照，`sampling.json` 保留全部候选与 coverage 选择/压缩记录。86/31 个合法点中，当前低可见性点数均为 0；不可见合法点保留行为另有测试验证。

## 启动命令与查看进度

在 `LastMileDataFlow/.worktrees/model-train` 运行，使用项目 `.venv`。以下为已执行命令；再次运行请改用新的 `--run-id`。

```bash
MUJOCO_GL=egl .venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/panda-full-calibration-03 \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_robocasa_curobo.json \
  --output-dir outputs --run-id panda-full-calibration-02 --gpu 0

MUJOCO_GL=egl .venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/rby1-full-calibration-03 \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/curobo_v2.json \
  --output-dir outputs --run-id rby1-full-calibration-02 --gpu 1
```

两任务已于 2026-10-18 16:25 启动并进入规划，后台监督进程 PID 分别为 2353829、2353830。未设置 `--max-trials`。

```bash
tail -f outputs/logs/full-calibration-panda-20.log
tail -f outputs/logs/full-calibration-rby1-20.log
```

实际结果目录分别为 `outputs/panda-full-calibration-02`、`outputs/rby1-full-calibration-02`，逐 trial 结果写入 `trials.jsonl`。运行命令和 PID 保存在 `outputs/diagnostics/full-calibration/runs-20.json`；进程退出后对应日志旁的 `.exit` 文件记录退出码。

## 执行过程与验证

1. 增加本体臂展/底盘半径配置，场景几何从所属本体解析，修正 Panda 继承 RBY 采样值的问题。旧快照加载按补齐默认字段后的配置比较，继续保留物理参数一致性检查。
2. 将可见性及保守导航网格资格筛选设置为关闭；cuRobo 的 head FOV 约束关闭，实际碰撞与规划约束继续执行。
3. 完成两本体全部候选物理检查。按最初“全部合法点”理解启动过 86/31 点任务；收到最终 20 点修正后中止这些进程并确认退出，保留已有产物，没有删除或覆盖。
4. 更新场景配方为 coverage / 20，从已有候选生成最终数据集，启动新 run；两者日志均确认 station数=20，规划器已初始化并开始轨迹求解。
5. `python -m unittest discover -s tests`：运行 239 项，通过，跳过 1 项。覆盖本体采样参数、不可见点保留、20 点选择的方位/距离覆盖；`git diff --check` 通过。测试日志为 `outputs/logs/full-calibration-tests-20.log`。

标定结束后应按真实 trial 汇总成功率，再决定 benchmark 可用站位。尚未构建这轮结果的 benchmark，也未启动 π0.5 评测。
