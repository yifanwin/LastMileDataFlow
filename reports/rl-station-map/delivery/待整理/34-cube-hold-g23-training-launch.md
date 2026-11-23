# CUDA2–3 方块连续保持0.25秒训练启动

- 状态：active
- 日期：2026-11-17
- 时间戳：2026-11-17T17:51:36.345416+08:00
- 替代：无；旧run保留
- 关联：[当前协议与测试](33-cube-hold-quarter-second-reward.md)、[运行指南](running.md)

## 配置与启动

用户指定CUDA2–3运行训练。当前工作区已改为0.25 s连续保持与新奖励，旧checkpoint有效配置不同；按当前配置从头训练，无resume，不加载旧模型或Replay。

成功要求双指法向力均>0.5 N、cube最低点离原桌面≥1 cm、无桌面/场景支撑接触，连续满足0.25秒。位置/姿态/抓握/抬升奖励权重分别为20/5/3/8，抬升按整回合有效接触历史峰值增量，其他参数沿用当前配置。

物理CUDA2–3各128环境，总256环境，CUDA2兼Learner；全局300万健康transition，batch512，seed42，Replay100,000，预热20,000，每128条新增经验更新一次，预计23,281次更新。TensorBoard30秒刷新，无启动/周期/终点策略评估。3小时上限，超时30秒强制清理本任务进程组。未干预其他GPU任务。

运行源码固定提交 `bcbaaac19a6858856e6d0be3067a5fd1de71a837`；已有报告修改保留，运行期间冻结源码、脚本与配置。启动前奖励/成功局部14项测试通过（0.725秒），git diff --check通过。

Run：`20261117-175136-s0246-cube-hold025-3m-b512-g23`；完整路径 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-175136-s0246-cube-hold025-3m-b512-g23`。进程组1756647，命令、环境、配置、Git状态与GPU快照见run内launch.json。

## 启动核查

待核查真实采样、学习更新、两卡绑定、checkpoint与TensorBoard。

### 2026-11-17T17:53:32.106298+08:00 启动核查通过

已记录40064条健康transition、156次实际SAC更新，预热已结束，累计吞吐约467条/s。已核对CUDA2 Learner、CUDA2/3两个Sampler共3个GPU进程绑定。latest.pt包含65维输入、0.25 s保持、新奖励20/5/3/8、batch512、300万预算及固定提交。TensorBoard实际loss、熵、alpha与runtime事件均有限；确认从零预热/零更新开始，无resume。运行源码、脚本、配置仍clean。训练继续运行，尚无冻结策略成功率验收；详情见run内startup-verification.json。
