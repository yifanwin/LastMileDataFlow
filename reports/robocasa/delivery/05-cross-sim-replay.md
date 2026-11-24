# RoboCasa 原生与 mjlab 跨仿真回放

- 状态：active
- 日期：2026-11-24 17:00 +08:00（诊断更新 2026-11-24 22:58 +08:00）
- 关联：[plan 06](../plan/06-protocol-unification.md)、[04 逐抓法扫描](04-cup3-grasp-sweep.md)

## 定位

两套仿真（robosuite 原生 PandaOmron + JOINT_POSITION kp 200，MuJoCo 3.3.1；mjlab 的 LMDF PandaOmron + 位置伺服，MuJoCo 3.16 / Warp）允许存在差异。本回放只描述差异有多大、有无系统性偏差，不设硬门槛；「成功时刻相差 ≤1 个控制步」仅作描述性指标（用户 2026-11-24 16:55 决定）。

## 方法

- 原生：`scripts/operation/robocasa_replay_native.py` 按评测器 B 的阶段（approach、grasp、close 20 步、lift、hold 30 步，hold 总是跑满）执行已存 cuRobo 规划，逐控制步记录臂目标、夹爪执行器 ctrl、关节、TCP、目标位姿和 PickJudge 三个事实。
- mjlab：`scripts/station_map/rl/robocasa_replay_mjlab.py` 在导出的 RL 场景同站位（mocap），以相同的逐步臂关节目标和夹爪 ctrl 驱动（绕过 DLS/终止），torso 目标 0，PickJudge 用原生口径（双指垫接触、无环境支撑、最低点 ≥2 cm、控制步保持 0.5 s）。
- 代码在独立 worktree `.worktrees/robocasa-physics-align`（分支 `codex/robocasa-physics-align`，基于 `507ff6d`，未提交）。

## 结果一：3 站 × 2 场景（cuRobo 名义站位规划，均为杯沿捏抓）

运行 `outputs/experiments/robocasa-station-eval/runs/20261124-1700-physics-replay/`。

| 指标 | 结果 |
|---|---|
| 成败一致 | 6/6（两边均成功） |
| 成功步差（mjlab − 原生） | −1 ×5，−4 ×1（far S0022） |
| 双指抓握起始步 | 6/6 完全相同 |
| 脱离支撑步差 | 0–2 步 |
| 最大抬升差 | ≤2.3 mm |
| 初始 link0 / TCP / 目标位置差 | ≤1e-6 m / ≤1e-6 m / 9e-6 m |
| 运动中 TCP 最大偏差 | 0.85–1.2 cm（臂控制器差异：原生最大跟踪误差 0.061–0.086 rad，mjlab 0.039–0.056 rad） |
| 结束时目标位置差 | ≤0.7 mm（far S0022 为 7.9 mm） |
| 夹紧后指宽差 | ≤0.25 mm |

far S0022 差 4 步的原因（逐步核对）：

1. 原生侧在抬升中指垫接触闪断：第 163 步右指垫、第 165 步左指垫各丢失接触 1 个控制步，保持窗口两次清零，成功从约第 173 步推迟到第 176 步。原生 `_check_grasp` 只看接触是否存在，盒形指垫贴在杯沿凸分解片上时接触会单步消失。
2. mjlab 侧在抬升起始（第 154–155 步）双指接触短暂丢失，杯子回落 3.6 mm 后重新夹住，抬升曲线滞后约 5 mm，但仍在第 172 步成功；这次滑移也是该组结束时目标位置差 7.9 mm 的来源。
3. 两者都是离散接触事件，不是标定偏差：其余 5 组 mjlab 一致早 1 步（抬升略快），这一组的 4 步来自原生单步闪断。

结论：同一指令下两边成败一致，时间轴上有约 1 步的系统性偏移和偶发的接触闪断；在当前判据下这类差异只影响成功时刻，不影响成败。

## 结果二：逐抓法扫描回放

把 04 号扫描中在原生执行过的 114 条规划（原生成功 87、闭爪未抓住 18、跟踪超差 6、保持中断 3；30°/45° 侧抓与杯沿捏抓，kettle-far 三站）在两边回放，统计成败交叉表，检验临界抓法上是否有系统性偏差。

- 原生回放：`outputs/experiments/robocasa-station-eval/runs/20261124-1715-physics-replay-sweep/native/`，17:00 时已完成 20/114。
- mjlab 回放待原生完成后运行（命令见下），结果写 `mjlab/summary.json`（`outcome_agree`、四格计数、成功步差分布）。

```bash
cd .worktrees/robocasa-physics-align
R=<station-eval>/outputs/experiments/robocasa-station-eval
PYTHONPATH=src LD_LIBRARY_PATH=$PWD/.venv/lib/native .venv/bin/python scripts/station_map/rl/robocasa_replay_mjlab.py \
  --native $R/runs/20261124-1715-physics-replay-sweep/native \
  --config configs/station_map/rl/robocasa_kettle_far_station_quality_ppo.yaml \
  --rl-scenes $R/rl-scenes --table $R/station-table/kettle-far__kettle-left-125mm/stations.jsonl \
  --grasps $R/runs/20261124-0024-cup3-grasp-pool-pitch/rl_transforms.npz --gpu 1 \
  --output $R/runs/20261124-1715-physics-replay-sweep/mjlab
```

## 限制

- mjlab 侧用的是 `507ff6d` 的环境（PickJudge 事实在脚本内按原生口径计算，不依赖 terms.py 的成功逻辑）；分身1 的 06 改动（torso 删关节、DLS 7 关节）不影响本回放的关节目标驱动方式。
- 回放绕过 DLS，比较的是被控对象加底层伺服；RL 策略经 DLS 产生的关节目标未在原生中回放。


## 结果三：差异来源诊断（2026-11-24 19:20–22:58 +08:00）

同一批 114 条 kettle-far 规划，三种仿真两两比较成败：

| 对比 | 成败一致 | 前者成功、后者失败 | 前者失败、后者成功 |
|---|---|---|---|
| 原生 3.3.1 vs 原生 robosuite 换 MuJoCo 3.16 | 93（82%） | 15 | 6 |
| 原生 3.3.1 vs mjlab（3.16 + Warp） | 84（74%） | 24 | 6 |
| 原生 3.16 vs mjlab | 85（75%） | 19 | 10 |

| 站位 | n | 原生 3.3.1 | 原生 3.16 | mjlab |
|---|---|---|---|---|
| S0019 | 44 | 0.80 | 0.66 | 0.64 |
| S0022 | 44 | 0.70 | 0.70 | 0.61 |
| S0025 | 26 | 0.81 | 0.69 | 0.54 |

排查过程与结论：

1. 静态参数一致：夹爪（执行器 kp 1000、力限 ±20 N、指垫 box、friction 2、solref 0.01 0.5）、杯子（friction 0.95 0.3 0.1、solref 0.001 2）、柜台台面（默认值）、timestep 0.002、Newton 100 次、椭圆锥。
2. 运行时差异两处：mjlab 关了 multiccd（原生开），impratio 写死 10（原生 20）。对齐后（`mjlab-forcearm-ccd-imp20/`）在同一 36 条子集上一致数 22/26/23，在噪声内，不是主因。`environment.py` 的 impratio 改为可配置（默认 10，训练不变）；回放脚本对齐两者。
3. 强制臂回放（每子步写原生臂 qpos/qvel，`--force-arm`）不可用：纯 MuJoCo 3.3.1 用同一模型也只复现 72/114，38 条抬起后掉落，是写关节破坏夹持；据此得出的「臂跟踪不是主因」作废。零速度版本（`mjlab-forcearm-zerovel/`）同样作废。纯 MuJoCo 回放脚本已删除。
4. 只换 MuJoCo 版本（`scripts/operation/robocasa_native_mj316.py` 包装：绕过 robocasa 版本断言，兼容 3.16 的 `jnt_type` 整数比较与 `mj_fullM(m, d, dst)` 新签名；控制器、判据、模型不变）：成败翻转 18%，偏保守，TCP 最大偏差中位 0.28 cm、p90 2.2 cm。闭爪时 TCP 偏差与 mjlab 失败的相关性（偏差 >=0.5 cm 时 mjlab 成功率 0.40，<0.5 cm 时 0.89）因此不是独立原因，版本本身就让同一控制器的轨迹偏开。
5. 版本差异只解释一部分：原生 3.16 与 mjlab 之间仍有 25% 不一致（19 条原生 3.16 成功、mjlab 失败），来自 Warp 和/或 mjlab 控制与回放方式（DLS 绕过、位置伺服实现）。未进一步隔离。
6. 翻转集中在临界抓法（如 g058–g061、g121–g125），各设置下翻转的具体条目不同。

最终结论：跨仿真成败差异大致一半来自 MuJoCo 3.3.1→3.16 版本，另一半来自 Warp/mjlab 实现；两者都偏保守，且集中在临界抓法。调 mjlab 参数无法消除版本带来的约 2 成翻转，站位排序（尤其 S0025，0.81→0.54）受影响。

建议：

- 统一版本：原生基准改在 MuJoCo 3.16 上跑（包装已可用），先消除版本这一半。
- 降低临界敏感：每个抓法加小扰动重复多次取平均成功率，检验 Q 与 S 的秩相关是否稳定。
- 若需定位剩余一半：原生 3.16 与 mjlab CPU（非 Warp）后端对比。

运行目录：`outputs/experiments/robocasa-station-eval/runs/20261124-1715-physics-replay-sweep/`（`native/`、`native-mj316/`、`mjlab/`、`mjlab-forcearm*/`）。
