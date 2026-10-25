# val100 标定运行失败原因分布（分 Case，trial 级）

- 状态：done
- 日期：2026-10-25
- 替代：无
- 关联：[val100 正式实验交付](../delivery/2026-10-24-val100-experiment.md)、[运行目录](../../../outputs/panda-time-val100-calibration-20261024-r01/)、[执行配置](../../../configs/execution/panda_omron_all_cases_curobo.json)

数据来源：`trials.jsonl`（8463 条 trial）、`planning/*-batch/batch_grasp_result.json` 与 `solver_trace.json`（2821 个 station 批次）、`scenes.jsonl`（Case / operation / category 映射）。每个 station 批次含 3 条 trial（3 个 seed），本文全部按单条 trial 统计，不按批次。

规划阶段按 `solver_trace.json` 切分为 3 个 `plan_pose` 调用（goalset → pregrasp → grasp），每条 trial 按 lane 与 stage 单独归因；归因结果与 `batch_grasp_result.json` 的 lane success 完全一致（0 条不一致）。

## 1. 全部失败原因（trial 级）

| 阶段 | 具体原因 | Case1 | Case2 | Case3 | 合计 | 占全部 |
|---|---|---|---|---|---|---|
| IK (goalset) | 目标位姿不可达（位置 ≥5 mm 或姿态 ≥0.05 rad） | 19 | 46 | 2918 | 2983 | 35.25% |
| IK (goalset) | 位姿收敛但约束不可行（碰撞/关节限位/工作圆） | 480 | 743 | 1453 | 2676 | 31.62% |
| TrajOpt (goalset) | IK 有解、轨迹不可行 | 580 | 904 | 0 | 1484 | 17.54% |
| IK (approach) | approach 阶段 IK 失败 | 9 | 18 | 0 | 27 | 0.32% |
| TrajOpt (approach) | approach 阶段轨迹不可行 | 9 | 15 | 0 | 24 | 0.28% |
| IK (grasp) | grasp 段（8 cm 线性接近）IK 失败 | 1 | 1 | 0 | 2 | 0.02% |
| TrajOpt (grasp) | grasp 段（8 cm 线性接近）轨迹不可行 | 66 | 97 | 0 | 163 | 1.93% |
| 执行 | 抓住物体后 lift 规划无解 | 18 | 47 | 0 | 65 | 0.77% |
| 执行 | `lift_contact_hold_or_slip`（抓取后滑落/夹持失败） | 78 | 84 | 0 | 162 | 1.91% |
| 执行 | `base_outside_workspace` | 15 | 22 | 0 | 37 | 0.44% |
| 执行 | `mobile_base_arm_tracking_timeout` | 2 | 11 | 0 | 13 | 0.15% |
| 执行 | `tcp_tracking_timeout` | 1 | 3 | 0 | 4 | 0.05% |
| 执行 | `arm_tracking_timeout` | 2 | 1 | 0 | 3 | 0.04% |
| — | success | 250 | 570 | 0 | 820 | 9.69% |
| 合计 | | 1530 | 2562 | 4371 | 8463 | 100% |

| Case | 场景数 | 操作 | trials | 规划失败 | 执行失败 | success |
|---|---|---|---|---|---|---|
| Case1 | 43 | pick | 1530 | 1164 | 116 | 250 |
| Case2 | 52 | pick | 2562 | 1824 | 168 | 570 |
| Case3 | 88 | open | 4371 | 4371 | 0 | 0 |

## 2. Case1（pick，1530 trials）

### 失败原因

| 阶段 | 具体原因 | trials | 占 Case1 |
|---|---|---|---|
| IK (goalset) | 目标位姿不可达 | 19 | 1.24% |
| IK (goalset) | 位姿收敛但约束不可行 | 480 | 31.37% |
| TrajOpt (goalset) | goalset 阶段轨迹不可行 | 580 | 37.91% |
| IK (approach) | approach 阶段 IK 失败 | 9 | 0.59% |
| TrajOpt (approach) | approach 阶段轨迹不可行 | 9 | 0.59% |
| IK (grasp) | grasp 段 IK 失败 | 1 | 0.07% |
| TrajOpt (grasp) | grasp 段轨迹不可行 | 66 | 4.31% |
| 执行 | 抓住后 lift 规划无解 | 18 | 1.18% |
| 执行 | 抓取后滑落/夹持失败 | 78 | 5.10% |
| 执行 | 底盘超出工作圆 | 15 | 0.98% |
| 执行 | mobile_base+arm 跟踪超时 | 2 | 0.13% |
| 执行 | tcp 跟踪超时 | 1 | 0.07% |
| 执行 | arm 跟踪超时 | 2 | 0.13% |
| — | success | 250 | 16.34% |

### 物体类别 × 原因

| 类别 | IK 不可达 | IK 约束不可行 | goalset TrajOpt | approach 规划 | grasp 段规划 | lift 规划 | 其他执行 | success | 合计 |
|---|---|---|---|---|---|---|---|---|---|
| SoapBottle | 1 | 207 | 256 | 11 | 39 | 15 | 42 | 134 | 705 |
| PepperShaker | 8 | 80 | 104 | 3 | 11 | 0 | 4 | 27 | 237 |
| Cup | 8 | 60 | 110 | 3 | 9 | 1 | 3 | 16 | 210 |
| SprayBottle | 1 | 56 | 41 | 1 | 6 | 0 | 5 | 43 | 153 |
| WineBottle | 0 | 10 | 50 | 0 | 2 | 2 | 44 | 30 | 138 |
| Mug | 1 | 67 | 19 | 0 | 0 | 0 | 0 | 0 | 87 |
| 合计 | 19 | 480 | 580 | 18 | 67 | 18 | 98 | 250 | 1530 |

### 证据

| 指标 | 数值 |
|---|---|
| goalset TrajOpt 失败站中目标位姿已到达（误差 <5 mm）的比例 | 89.2% |
| goalset TrajOpt 失败站最佳位置误差 p50 / p90 / max | 0 / 0.059 m / 0.555 m |
| 含 IK 约束不可行 trial 的 station 中，top-12 解有 ≥10 个底盘撞环境 | 156 / 224（69.6%） |

## 3. Case2（pick，2562 trials）

### 失败原因

| 阶段 | 具体原因 | trials | 占 Case2 |
|---|---|---|---|
| IK (goalset) | 目标位姿不可达 | 46 | 1.80% |
| IK (goalset) | 位姿收敛但约束不可行 | 743 | 29.00% |
| TrajOpt (goalset) | goalset 阶段轨迹不可行 | 904 | 35.28% |
| IK (approach) | approach 阶段 IK 失败 | 18 | 0.70% |
| TrajOpt (approach) | approach 阶段轨迹不可行 | 15 | 0.59% |
| IK (grasp) | grasp 段 IK 失败 | 1 | 0.04% |
| TrajOpt (grasp) | grasp 段轨迹不可行 | 97 | 3.79% |
| 执行 | 抓住后 lift 规划无解 | 47 | 1.83% |
| 执行 | 抓取后滑落/夹持失败 | 84 | 3.28% |
| 执行 | 底盘超出工作圆 | 22 | 0.86% |
| 执行 | mobile_base+arm 跟踪超时 | 11 | 0.43% |
| 执行 | tcp 跟踪超时 | 3 | 0.12% |
| 执行 | arm 跟踪超时 | 1 | 0.04% |
| — | success | 570 | 22.25% |

### 物体类别 × 原因

| 类别 | IK 不可达 | IK 约束不可行 | goalset TrajOpt | approach 规划 | grasp 段规划 | lift 规划 | 其他执行 | success | 合计 |
|---|---|---|---|---|---|---|---|---|---|
| SoapBottle | 11 | 261 | 356 | 16 | 41 | 25 | 32 | 263 | 1005 |
| Mug | 7 | 98 | 172 | 4 | 24 | 3 | 6 | 97 | 411 |
| Cup | 8 | 89 | 173 | 6 | 17 | 6 | 10 | 93 | 402 |
| PepperShaker | 16 | 178 | 75 | 4 | 5 | 5 | 16 | 58 | 357 |
| SprayBottle | 0 | 101 | 59 | 3 | 6 | 4 | 9 | 28 | 210 |
| WineBottle | 4 | 16 | 69 | 0 | 5 | 4 | 48 | 31 | 177 |
| 合计 | 46 | 743 | 904 | 33 | 98 | 47 | 121 | 570 | 2562 |

### 证据

| 指标 | 数值 |
|---|---|
| goalset TrajOpt 失败站中目标位姿已到达（误差 <5 mm）的比例 | 90.1% |
| goalset TrajOpt 失败站最佳位置误差 p50 / p90 / max | 0 / 0.0005 m / 0.575 m |
| 含 IK 约束不可行 trial 的 station 中，top-12 解有 ≥10 个底盘撞环境 | 164 / 317（51.7%） |

## 4. Case3（open，4371 trials）

### 失败原因

| 阶段 | 具体原因 | trials | 占 Case3 |
|---|---|---|---|
| IK (goalset) | 目标位姿不可达 | 2918 | 66.76% |
| IK (goalset) | 位姿收敛但约束不可行 | 1453 | 33.24% |
| TrajOpt / 执行 | 无 | 0 | 0% |
| — | success | 0 | 0% |

### 家具类别 × 原因

| 类别 | IK 不可达 | IK 约束不可行 | 合计 | 不可达站把手高度 z | 不可行站把手高度 z |
|---|---|---|---|---|---|
| Dresser | 2388 | 0 | 2388 | 0.19–0.60 m | — |
| Fridge | 87 | 1143 | 1230 | 0.21 m | 0.84–1.17 m |
| Safe | 187 | 140 | 327 | 0.15–0.25 m | 0.85–0.88 m |
| SideTable | 185 | 115 | 300 | 0.34 m | 0.73–0.79 m |
| Desk | 66 | 0 | 66 | 0.18 m | — |
| Microwave | 5 | 55 | 60 | — | 0.95 m |
| 合计 | 2918 | 1453 | 4371 | | |

### IK 不可达：把手高度 vs 最小位姿误差

| 把手高度 z 档 | trials | 最小位置误差 p10 / 中位 / p90 | 中位（z + 误差） |
|---|---|---|---|
| ≈0.2 m | 1266 | 0.303 / 0.347 / 0.388 m | 0.547 m |
| ≈0.3 m | 1200 | 0.193 / 0.241 / 0.288 m | 0.541 m |
| ≈0.6 m | 420 | 0.014 / 0.016 / 0.023 m | 0.616 m |

即该抓取姿态下 TCP 最低可达高度约 0.54–0.62 m。

### IK 收敛但约束不可行：碰撞链

| 类别 | 不可行 trial | 目标碰撞（target_collision）主要链节（占 station 比例） |
|---|---|---|
| Fridge | 1143 | gripper 100%、link7 98%、link6 97%、link5 91%、base 87% |
| Safe | 140 | link7 100%、gripper 100%、link6 96%、link5 65% |
| SideTable | 115 | link0 100%、gripper 95%、base 92%、link6 90% |
| Microwave | 55 | link6 100%、link7 100%、gripper 100%、link5 85% |

位姿误差中位数为 0（精确到达目标），失败来自腕部/夹爪与家具本体的碰撞约束。

## 5. 汇总

| 结论 | Case |
|---|---|
| goalset/approach/grasp 轨迹不可行是首要原因，且绝大多数目标位姿已到达 | Case1、Case2 |
| IK 约束不可行的次因：底盘为够目标平移/旋转进台面或墙体 | Case1、Case2 |
| 少量 trial 在 approach/grasp 阶段 IK 就失败 | Case1、Case2 |
| 执行阶段主要是抓紧后滑落，其次是带物体 lift 规划无解 | Case1、Case2 |
| 低位把手（z≤0.6 m）低于 TCP 最低可达高度 | Case3 |
| 高位把手（z≥0.73 m）腕部/夹爪与家具本体碰撞 | Case3 |

## 6. 口径与限制

| 项 | 说明 |
|---|---|
| IK 收敛判定 | 位置 <5 mm 且姿态 <0.05 rad，与 cuRobo IK 默认容差一致 |
| IK 可行 | IK `success` 为收敛且 `feasible`（关节限位、自碰撞、环境/目标碰撞、工作圆全部满足） |
| TrajOpt 失败 | IK 有可行解，但该阶段 5 次 IK→TrajOpt 尝试后该 lane 仍无成功轨迹 |
| 阶段切分 | 依据每批 3 个 `plan_pose` 调用与每阶段 `max_attempts=5` 的提前退出规则；切分后消耗 trace 条目数与 IK/lane success 均与原始结果逐条核对一致 |
| 未记录项 | `solver_trace.json` 只为 IK 保存 `constraint_terms`，TrajOpt 失败无法继续拆分为环境碰撞/自碰撞/关节限位/速度约束 |
| 约束归因精度 | IK 约束项是 32 seeds × goalset 的聚合值，`selected_link_costs` 只覆盖 top-12 解，不能逐 seed 归因 |
