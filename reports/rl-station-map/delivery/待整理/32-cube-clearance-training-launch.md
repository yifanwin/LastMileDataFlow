# Cube 最低点离桌面1 cm四卡训练启动

- 状态：done
- 日期：2026-11-17
- 时间戳：2026-11-17T16:41:37.540599+08:00
- 替代：无
- 关联：[成功与奖励调整](31-cube-clearance-success-reward.md)、[运行指南](running.md)

按用户指令使用物理CUDA0–3启动。启动前四卡均0%利用率、0 MiB占用，CUDA4–7已有任务，未干预。

运行源码固定提交 `d618bb709b77657a72c2a449cf079079c26eca73`；包括本次成功/奖励改动和已有的满100回合成功率选模实现，工作区已有文档修改保留。运行期间冻结源码、脚本与配置。

Run：`20261117-164137-s0246-cube-clearance-1cm-force05-3m-b512-g0123`。完整路径：`/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-164137-s0246-cube-clearance-1cm-force05-3m-b512-g0123`。

从头训练，无resume、不加载旧模型/Replay，seed42。CUDA0–3各128环境，共512环境；GPU0兼Learner。全局预算3,000,000条健康transition，预热20,000，Replay100,000，batch512，每128条新增经验更新一次，预计23,281次更新。沿用3小时进程组时间上限，超时30秒后强制清理本次任务。

成功为同一物理子步双指法向力均>0.5 N、cube最低点离原桌面≥1 cm；抬升奖励也按最低点离桌面进度，姿态权重1。其余超参数沿用当前配置。best仅在收满100个有效非预热健康完整训练回合后按近期成功率严格提升选择。TensorBoard每30秒刷新，无启动/周期/终点策略评估或Q图。

命令、PID、环境、配置、Git状态和GPU快照见run内launch.json；本次进程组为1380813。真实采样和学习更新待核查，启动不代表Actor已成功学会。

### 2026-11-17T16:43:47.916840+08:00 启动核查通过

已记录120064条健康transition、781次实际SAC更新，已结束预热，累计吞吐约1032条/s。GPU0 Learner与CUDA0–3四个Sampler共5个计算进程的卡号绑定已核对。latest.pt包含65维输入、新0.5 N/1 cm判定、姿态权重1、batch512、300万预算及固定提交；源码/脚本/配置仍clean。TensorBoard实际loss、熵、alpha与runtime事件均有限。确认从零预热、零更新开始，无resume；训练继续运行，尚无冻结策略成功率验收。完整核查见run内startup-verification.json。

## 2026-11-17T17:15:02.816959+08:00 用户要求停止

按“结束当前所有训练和验证”停止当前训练进程组，训练未完成300万预算。最后已刷盘learner记录为2100096条健康经验、16250次更新；这不是额外保存的终止checkpoint。四个验证进程组在检查时已退出，原始trial和视频等工件保留；不继续验证或汇总。停止记录在两个run的stop-record.json。
