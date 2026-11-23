# 方块归一化与新奖励：300万条四卡训练

- 状态：superseded
- 日期：2026-11-17
- 替代：无；新run，从头训练
- 关联：[归一化与奖励实现](25-cube-normalization-rewards.md)、[历史训练](21-cube-lift-v2-training-launch.md)、[运行指南](running.md)
- 启动时间：2026-11-17T10:56:46.328961+08:00

## 配置与启动

按用户指令使用物理CUDA 0–3。启动前四卡均0%利用率、显存占用0 MiB，其他GPU上的任务不干预。每卡一个128环境Sampler，共512个训练环境；Learner与Sampler0共用GPU0。

固定运行源码提交为 `4a493f8a8d11b4306a71b4888b3185d7ede8353d`，工作区为 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact`。本次仅提交已验证的配置、源码、测试与相关说明，保留其他已有文档修改；运行期间不修改源码、脚本和配置。复用actor-critic的RL解释器，`PYTHONPATH`显式绑定本工作区src，`MUJOCO_GL=disable`，不安装依赖。

- 从头初始化，不传resume，不加载历史checkpoint；seed42。
- 总预算3,000,000条健康transition，预热20,000，Replay100,000，batch256，每128条新增健康经验更新一次，预计23,281次更新。
- 65维观测，位置/原始旋转误差按2 cm/20°归一化；位置和旋转奖励仍使用原始米、弧度。
- 位置10、旋转0.5、碰撞−1、首次成功+30；3 cm/20°内闭爪0.05与双指接触0.05；双指接触时归一化抬升进度差权重1.0；关闭张开奖励。
- v2成功条件仍为同一原生物理子步双指各>1e-6 N且相对初态抬升至少0.0086 m；策略步0.05 s，回合50 s。
- 无启动、周期或终点Q图/策略评估；TensorBoard每30秒刷新，按近期非预热健康训练回报选择best.pt。
- 按历史500万run的实际吞吐估计约45分钟，非耗时保证；设置3小时超时，超时后30秒强制清理本次进程组。

run为 `outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-105646-s0246-cube-normalized-3m-g0123`，启动进程组2929470。run内保存config.yaml、launch.json、console.log、metrics.jsonl、tensorboard/和checkpoint；命令、环境、PID、GPU快照和工作区状态见launch.json。历史运行保留，不搬迁、不改写。

## 验证与边界

复用本会话已通过的31项定向测试及GPU0真实2环境11步验证，不重复测试。启动后检查本run实际采样、学习更新、四卡Sampler进程、checkpoint输入尺度和TensorBoard事件，结果保存到run内startup-verification.json。训练完成以metrics.jsonl的sampling_complete核查实际预算和更新数；启动成功不表示策略已学会抓取。

### 2026-11-17T10:58:41.229756+08:00 启动核查通过

已记录100,096条健康transition与625次实际SAC更新，预热已结束；含启动开销的累计吞吐约1,021条/s。物理GPU0上的Learner PID2929473与Sampler PID2941296正常运行，GPU1/2/3上的Sampler分别为2941297/2941298/2941301；实际GPU计算进程确认四卡绑定正确。

读取本run的latest.pt，确认65维flat输入、2 cm/20°尺度、新七项奖励、3,000,000预算及固定源码提交一致。TensorBoard实际损失、熵、alpha及runtime曲线均可读取且数值有限。当前尚无完整回合，因此暂未出现train/warmup回合奖励曲线；不把它解释为未记录分项，也不据此判断策略成败。源码、脚本、配置和测试与固定提交一致，训练继续运行。详细快照见run内startup-verification.json。

## 2026-11-17T11:39:41.072643+08:00 按用户指令停止

用户最新要求先修改代码、启动下一轮前停止本轮。已终止本run进程组2929470并确认无存活进程，最后记录2,930,045条健康transition、22,734次更新；未写sampling_complete，未完成300万预算。旧配置、日志、checkpoint及TensorBoard全部保留，详情见本run的stop-record.json。新奖励与batch512的独立从头训练见[27号交付记录](27-cube-stage-contact-b512-training.md)。
