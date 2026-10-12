# `no-edit-val-full-20261009` 统计与结果报告

> 只读统计。未修改、未续跑、未删除该 run 任何文件。机读数据见 [val-full-20261009-stats.json](data/val-full-20261009-stats.json)。

## 一句话结论

**这是一次未完成、且被后续协议取代的全量运行：实际只派发并跑完 4 / 1000 个场景，9270 次尝试里只有 2 次抓取成功（0.022%），没有任何任务达到 5% 保留阈值，最终数据集为空（0 段），run 以 `infrastructure_error` 终止。**

## 1. Run 身份与状态

| 项 | 值 |
|---|---|
| Run ID | `no-edit-val-full-20261009` |
| 目录 | `outputs/no_edit/no-edit-val-full-20261009/` |
| schema | `no-edit-v1`；协议 `lightweight-v1`（**锁底盘版本**，非当前 mobile-base 协议） |
| code_sha256 | `6400bde4312c1f6fce4bda78b1e5b195c5155234e4c2e035f644736a756b1cc4` |
| seed | 20261009 |
| 场景清单 | 1000（`scene_index.json` 全部 `ready`） |
| 实际派发场景 | **4**（GPU 1→val_0、5→val_1、2→val_2、7→val_3） |
| 运行区间 | 2026-10-09 16:00:35 UTC → 20:10:31 UTC（约 **4 h 10 min**） |
| `collection.record_video` | `true`（**但实际产出 0 个 MP4，见 §6**） |
| 顶层状态 | `infrastructure_error`，`retained_segments=0` |
| 磁盘（统计时） | `/data0` 29 T / 32 T，95% 已用，余 1.7 T |
| 进程 | 主进程 PID 1748272 **已不存在**；无该 run 的存活 worker |

`run.lock` 残留、`dataset_index.jsonl` 为 0 字节，均与「未完成且数据集为空」一致。

## 2. 场景级结果

| 场景 | 状态 | 枚举任务 | 已建任务目录 | 出结果 | 尝试数 | 有执行步数 | 成功 |
|---|---|---:|---:|---:|---:|---:|---:|
| val_0 | `completed` | 80 | 80 | 80 | 4710 | 32 | **2** |
| val_1 | `infrastructure_error` | 23 | 23 | 22 | 1860 | 29 | 0 |
| val_2 | `infrastructure_error` | 72 | **12** | 11 | 575 | 5 | 0 |
| val_3 | `completed` | 24 | 24 | 24 | 2125 | **0** | 0 |
| **合计** | — | **199** | 139 | 137 | **9270** | **66** | **2** |

要点：

- val_1、val_2 的中断原因是同一处代码缺陷：`src/lastmile_dataflow/tasks/raw_scene.py:146` 对某个资产的抓取数据抛出 `ValueError: invalid grasp shape`（grasp 数组形状非法），未被捕获而终止该场景 worker。
- val_2 只建了 12/72 个任务目录就停了，是四个场景里覆盖度最低的。
- **val_3 完成 24 个任务、2125 次尝试，`executed_steps>0` 的数量是 0**——即整个场景从未真正下发过一个控制步。其失败全为规划期（`outside_conservative_reach_bound`）。

## 3. 尝试（attempt）级结果

共 9270 次尝试，每次都写全量产物（`attempt.json` / `outcome.json` / `result.json` / `physics.jsonl.gz` / `videos.json`）。

**状态分布**

| status | 次数 | 占比 |
|---|---:|---:|
| `planning_no_solution` | 9204 | 99.29% |
| `failure` | 64 | 0.69% |
| `success` | **2** | 0.02% |

**终止原因分布**

| termination_reason | 次数 | 占比 |
|---|---:|---:|
| `outside_conservative_reach_bound` | 9036 | 97.48% |
| `planning_no_solution` | 177 | 1.91% |
| `robot_collision` | 37 | 0.40% |
| `torso_protocol` | 16 | 0.17% |
| `strict_pick`（即为成功） | 2 | 0.02% |
| `nonfinger_target` | 1 | 0.01% |
| `lift_contact_hold_or_slip` | 1 | 0.01% |

**归因类别**：`Reachability` 9158、`PlanningFailure` 55、`Collision` 38、`TrackingFailure` 16、`GraspSlip` 1。即 **98.8% 的尝试被归为可达性（Reachability），98.8% + 0.6% = 99.4% 归为规划类问题**；执行/物理类（碰撞、跟踪、滑移）合计仅约 0.6%。

**执行深度**：仅 **66 / 9270（0.71%）** 的尝试走到了实际下发控制步（`executed_steps>0`），最长 673 步。按首个记录到的阶段看：`initialize` 9025、`dry_pregrasp` 179、`approach` 26、`pregrasp` 18、`lift_attached` 11、`close` 5、`hold` 3、`lift` 2、`open` 1。

**按操作拆分**

| 操作 | 尝试 | 有执行步数 | 成功 | 主要终止原因 |
|---|---:|---:|---:|---|
| pick | 6115 | 47 | 2 | reach_bound 5968 |
| open | 3155 | 19 | **0** | reach_bound 3068 |

`open`（开门/开抽屉）**零成功**，且没有任何一次进入有效的铰链动作验收。

**尝试耗时**：中位 0.09 s、均值 2.26 s、p99 102.5 s、最长 197.8 s——绝大多数尝试在进入规划前就退出，耗时集中在少数做完整规划的样本上（也印证了「规划器构建/预热成本高、但吞吐不是瓶颈」的判断）。

## 4. 任务级结果

137 个已产出结果的任务中：

| status | 次数 |
|---|---:|
| `discarded_low_success` | 123（成功率 < 5%） |
| `no_valid_stations` | 14（无合法站位，`success_rate=null`） |

- `terminal_trials` 合计 **9270**，`successes` 合计 **2**。
- **`dataset_eligible` 全部为 `false`；`successful_rollouts>0` 的任务数为 0；`retained_segments=0`。**
- 只有 2 个任务出现过非零成功率，且都远低于 5% 阈值：

| task_id | 场景 | 指令 | 成功/试验 | 成功率 |
|---|---|---|---|---:|
| `pick-ed4fde20087f` | val_0 | 拿起房间5中编号`SprayBottle\|surface\|5|52`的SprayBottle | 1 / 45 | 2.22% |
| `pick-5a86ecebea93` | val_0 | 拿起房间2中编号`SprayBottle\|surface\|2|7`的SprayBottle | 1 / 105 | 0.95% |

两次成功都是 val_0 的 **SprayBottle**，`executed_steps=243`，`phase=hold`，`control={arm:"right", torso_h:0.0}`，来源为 `Spray_Bottle_1_grasps_filtered.npz` 的第 673 / 280 行。按运行规则，单段成功也需占位但本 run 未进入 S0→S1 连续采集阶段，故未产生任何保留段。

**未评测目标（`skipped_targets`，共 77 条）**：`pick|not_free_moving_instance` 34、`open|no_eligible_articulation` 23、`already_open_or_no_travel` 20。这些是合理的正常跳过（非自由移动体、无可开启关节、门已开），不是失败。

**目标物体分布**（按指令解析出的类别）：Dresser 36、Pen 11、Book 10、CellPhone 8、Pencil 7、SprayBottle 6、BaseballBat 6、RemoteControl 6、AlarmClock 5、Bowl 4、Plate 4…… 值得注意的是**类别高度偏向小物体与需精细抓握的目标**，而 `Dresser`（36 条）全部是 `open` 类（抽屉/柜门）。

## 5. 产物完整性与「应有 vs 实有」差距

| 产物 | 实有 | 说明 |
|---|---:|---|
| `attempt.json` / `outcome.json` / `physics.jsonl.gz` | 9270 | 全覆盖 |
| `videos.json` | 9270 | **全部 `{"cameras":{},"enabled":false}`** |
| MP4 文件 | **0** | collection 声明 `record_video=true`，实际一路都没录 |
| `events.jsonl` 非空 | **0** | 事件流为空 |
| `trajectory.jsonl` 非空 | 66 | 仅等于「有执行步数」的尝试数 |
| `planning.json` 非空 | 245 | 规划证据 |
| `success_heatmap.png` | 137 | 每任务一张，与任务数一致 |
| `dataset_index.jsonl` | 0 字节 | 无保留段 |
| `timing.json` | **不存在** | 该运行未生成整体计时 |

**这三点需要明确区分**：① `summary.json` 的 `completed_scenes=4` **不等于全量完成**（1000 场景只碰了 4 个）；② 缺视频**不等于视频编码故障**——是这批 attempt 根本没有执行动作（`not_executed` 语义成立），且该 run 的录像开关在实例层面就是 `false`；③ 缺 `events`/`trajectory` 是**规划期退出**的自然结果，不是记录器故障。

## 6. 判读与边界

**可以直接下的结论**

1. 该 run 是一次**过早终止的探索性全量**，有效规模是「4 个场景、199 个任务枚举、137 个任务出结果」，不能代表 1000 场景的 val 全量。
2. 在锁底盘 `lightweight-v1` 协议下，**`Reachability`（`outside_conservative_reach_bound`）是压倒性瓶颈**：97.5% 的尝试被执行前的保守可达界挡下，只有 0.71% 真正进入物理执行。
3. **`open` 类任务 0 成功**，与记忆中 ProcTHOR 场景可动关节实际占比低、且代码会移除部分关节的事实一致；热图/日志里 `no_eligible_articulation` 也印证了这一点。
4. 数据集产出为 **0 段**，两例成功仅作为可行性证据，不构成任何可发布示教。

**不能下的结论**

- 不能把 `outside_conservative_reach_bound` 说成「目标不可达」——它只是**保守界内的诊断无解**，运行文档本身声明这不是全空间不可达的证明。
- 不能把 `planning_no_solution` 计入「操作失败率」，其物理结果按协议为 `unknown / not_executed`。
- 不能把本次 0/9270 当作该评测协议的整体成功率；至少还有 996 个场景从未派发，且协议本身随后已被 mobile-base（放开底盘）取代。
- 缺视频不代表录像管线有问题：该 run 的 `videos.json.enabled` 全为 false，属实例级未启用。

## 7. 与后续 run 的关系

该 run 属于**已被取代的锁底盘协议**。worktree 内后续已有多个新 run（`no-edit-val-mobile-*`、`no-edit-val-four-view-*`、`no-edit-val103-*`），其中 `no-edit-val-four-view-full-20261010` 与 `no-edit-val103-full-20261010` **当前仍在运行**（各自独立后台进程，与本 run 无关）。本报告只统计 `no-edit-val-full-20261009`，不与上述任何 run 的产出混算。

## 8. 复现本统计

```bash
# 只读聚合，不启动仿真
python3 reports/no-edit/data/collect_val_full_stats.py \
  outputs/no_edit/no-edit-val-full-20261009
```

原始明细：`scenes/val_<i>/{summary,task_index,task_results}.json`、`scenes/val_<i>/attempts/<id>/{attempt,outcome,result}.json`、`scenes/val_<i>/worker.log`。
