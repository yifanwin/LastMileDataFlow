> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# LastMile Datasets v0 实施与真实验证报告

## 后续修复

墙钟 timeout 遮蔽物理成功的问题已按用户要求修复；完整 64 条 timeout 只读复核及新版单条真实验证已完成，详见 [TIMEOUT-FIX.md](TIMEOUT-FIX.md)。旧封存 run/率表未改写。

## 最新状态（2026-10-13 08:27 CST）

完整 Case1 run 已于 **2026-10-13 06:50 CST** 完成并封存，耗时约 **6 小时 51 分钟**。100/100 次标定：11 success、5 failure、20 planning_no_solution、64 timeout，无 episode error。没有满足固定阈值的 S0/S1 点对，结果 `no_eligible_pair`；连续恢复 0 次、成功训练 episode 0。原始子 run 约 59GB，汇总 publication 约 176KB。此更新取代下文历史“正在运行”状态；下文保留实施时记录。

结果见 summary.json，率表与 selection 保存在同一 publication 下。正式 run 当前没有自动 MolmoBot 导出；pilot 的 46 条 state/images 只是格式 smoke。

**首版编排已实现，真实 raw-first 记录与 MolmoBot state/images 转换已验证。完整 Case1 标定正在 GPU 1 运行，尚未验收成功 S0→S1→操作闭环。**

报告时间：2026-10-13 00:01 CST。初始需求和方案保留在 [REPORT.md](REPORT.md)，没有把方案中的预期改写为已完成结果。

## 1. 执行过程

1. 对照 scene-datasets 的四份数据协议，检查本 worktree 的 recorder、cuRobo 标定、benchmark selector、连续执行与 adapter。
2. 增加独立薄编排入口，复用现有领域模块，不恢复旧五阶段管线，不重建源障碍物。
3. 增加错误重试、完成但未登记 attempt 的恢复、标定绑定、导航到站复核和严格成功索引；补充故障回归。
4. 校验实际 sealed/partial scene dataset：2 个 position 包、40 个 selected 点。
5. 实际执行一个点的独立 cuRobo trial，检查记录完整性，并调用现有 MolmoBot adapter。
6. 按预先记录的 [验收计划](01-validation-plan.md)，启动一个完整包的 100 次标定及后续恢复。未修改源场景、旧输出，未提交 Git。

管线说明图

*说明图只解释数据流，不是闭环成功证据。*

## 2. 实现范围

| 层 | 实现 |
|---|---|
| 输入 | sealed package 校验，sample/config/task/model/selected 绑定，沿用源站位，不重新采样；partial 缺额保留 |
| 标定 | 每点默认 5 次有效独立试验；timeout 为有限预算失败，基础设施/记录错误不伪装为任务失败 |
| 选择 | 共用 `lastmilebench-station-rates-v1` 与 benchmark 低/高点选择；全部 selected 标定齐全后才选择 |
| 连续执行 | 现有 A* 导航和 cuRobo 操作；实际到站 XY/yaw 复核，begin 后不 teleport/reset；没有通路明确记录 |
| Raw-first | 保留实际状态、控制、RGB/depth、相机信息、contacts、配置及模型摘要；不捏造动作或成功标签 |
| 发布 | all-attempts、连续结果和成功训练索引分开；成功索引要求导航/任务成功、连续仿真、记录完整与 audit valid |
| 派生 | 默认对合格成功 Pick 调用既有 state/images adapter，原始数据不改；转换失败单列 |
| 运维 | 单 worker，CUDA 0–7 按实时利用率/显存选卡；唯一 run/attempt，配置和代码绑定 resume，完成发布封存 |

主要源码：`workflows/lastmile_datasets.py`、`stations/lastmile_selection.py`；增量修改 `workflows/no_edit.py`、`runtime/no_edit_execution.py`、`stations/no_edit_config.py`、`cli.py`。真实验收还发现既有 `audit-canonical` CLI 缺局部导入，已补齐并增加分发回归。

操作说明：[lastmile-datasets-v0.md](../../../docs/archives/lastmile-datasets-v0.md)。默认配置：`configs/lastmile_datasets/v0.json`。

## 3. 软件验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 新增回归 | **21 项通过**，合成/mocked，不代表 CUDA 成功 | 日志 |
| 相关模块回归 | **94 项：93 通过，1 跳过** | 日志 |
| 全套回归 | **426 项：414 通过，10 跳过，2 失败** | 日志 |
| 代码卫生 | `git diff --check` 通过；无新增依赖 | 当前 diff |

两项全套失败在未修改基线 `950fc407` 的独立解包中同样复现，没有修复无关问题：

- recorder 中途异常分类期望 `recording_error`，基线返回 `episode_error`：基线日志。
- reach sampling preset 期望仅 radius/spacing 与 v080 不同，但基线已相同：基线日志。

## 4. 真实 pilot 结果

环境：MuJoCo 3.5.0、Torch 2.7.1、现有 pinned cuRobo 0.8.0 环境，EGL；没有安装新依赖。

输入：`scene-val103-cup30-minimal-v3-20261012-r2`。scope 为 `val_103-case1-f2871d23d0f128da`，任务 `pick-bf2b057e9916`，站点 S0048。pilot 为独立诊断 run `lastmile-v0-real-pilot-20261012-2352`，新尝试上限 1；完整 selected 集合不截断。

- 实际结果：**timeout**，原因 `attempt_wall_clock_budget`（300s），不是成功；contact 与实际运动正常记录。
- canonical：**audit valid=true，92 executed steps，93 frame groups，issues=[]**：审计。frame count 指边界帧组，不是 93 个物理 tick，也不是所有相机图片总数。
- 现有 adapter：**46 条 state/images 样本**，sealed，source audit valid；`state_images_only=true`，`no_actions`。没有 position→native motor 动作映射。
- **train_episodes=0、recovery_attempts=0**：pilot summary。超时 trial 不进入成功恢复训练索引。
- 派生输出：pilot state/images manifest。这证明格式可转换，不代表可直接提供成功策略监督或 MolmoBot 训练已完成。

实际 attempt：

```text
outputs/no_edit/lastmile-v0-real-pilot-20261012-2352-unit-1108a8dd28688912/
  scenes/val_103/attempts/pick/S0048/pick-cup2-S0048-t0-94768a06/
```

pilot 代码摘要为 `8a7dd550756b7ce471b6358d70f66868156c64d7731e8776c38c0dec9410e7ec`；CLI 审计导入修复后，adapter 与正式运行摘要为 `a6b0cf50ccc7791554914f8d709e3cf7c50ec54bfa1aec1f0a044575dcfb94a7`。既有 pilot attempt 未改写，不用新代码强行 resume 旧诊断 run。

## 5. 完整包正在运行，不能当作已完成

正式 run：**`lastmile-v0-case1-full-20261013`**，20 点 × 5 次有效试验，无诊断 cap。仅覆盖上述一个 Case1 包，不代表源 dataset 全部包。

- 启动：2026-10-12 15:59:41 UTC（23:59:41 CST）；父 PID **3894416**。命令和资源环境见 launch.json。
- 自动选择 **GPU 1**：选择时利用率 0%、空闲显存 46685MB；候选 0–7，不干预其它任务。
- 报告时父 progress 为 **running**，尚无完整标定率表或成功恢复数据。进程启动不等于任务成功。
- 标定完成才选择 S0 ≤0.2、S1 ≥0.8、距离 ≤1m 的点对，尝试实际连续恢复，再审计和自动派生。

监控入口：

```bash
cat outputs/lastmile_datasets/lastmile-v0-case1-full-20261013/progress.json
tail -f outputs/logs/lastmile-v0-case1-full-20261013-unit-1108a8dd28688912-val-103-worker.log
cat outputs/no_edit/lastmile-v0-case1-full-20261013-unit-1108a8dd28688912/scenes/val_103/tasks/cup2/progress.json
```

主日志仅含进程调度；站点执行细节在 worker log。最终以父 `summary.json`、率表、selection、audit、`train_index.jsonl` 判断结果。若运行被中断，只在相同代码和配置下用同命令加 `--resume`；不要同时启动两个恢复进程。

## 6. 剩余限制与交付边界

1. **真实成功 S0→S1→操作尚未验收**。如果完整标定没有合格高低邻点或恢复全部失败，应发布负结果，不修改阈值冒充成功。
2. position 数据已经可经现有 adapter 转为 state/images；native action 仍需显式、有来源的 gripper 标定。Open 还需显式首帧 conditioning point。
3. 与 LastMileBench 共用率表/选择代码，但 native benchmark 必须严格匹配自己的 model/robot_sensor_config，不能把 position 率表改标签使用。
4. 当前未验证 MolmoBot 真训练/推理、native action 监督、Open 真实闭环和其余场景包。原始采集不等待这些派生条件。
5. 状态和图像按实际执行区间边界记录，控制逐次提交记录、contact 逐物理 tick；不宣称所有相机/完整状态逐 physics tick 保存。

**当前交付是可运行首版编排、软件证据、真实记录/格式验证以及持续运行中的完整包验收，不是已完成的成功 LastMile 数据集。**
