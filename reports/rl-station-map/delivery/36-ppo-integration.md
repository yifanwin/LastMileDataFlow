# SAC / PPO 可选训练实现与短验证

- 状态：done
- 日期：2026-11-17
- 验证记录时间：2026-11-17T20:05:39+08:00
- 替代：无
- 关联：[运行指南](running.md)、[checkpoint 规则](35-tensorboard-best-periodic-checkpoints.md)

## 目标与实际修改

从 `rl-cube-contact` 的 `57d25fda62dd342bb0007a0f03f3dcb823427a8b` 创建独立托管工作区 `/home/wenyifan/.codex/worktrees/rl-ppo/LastMileDataFlow`，分支 `codex/rl-ppo`。实现与验证使用该工作区的未提交修改，不能仅凭基线 commit 复现新增 PPO；原工作区保持干净，未修改或停止其中的任务。

训练脚本支持 `--algorithm {sac,ppo}`，覆盖配置 `algorithm`，缺省仍为 SAC。新增 cube flat / Cup graph 两个 PPO 示例，保留原 SAC 参数以便通过同一入口切换算法。核对示例：去掉新增的 `algorithm: ppo` 和 `ppo: {}` 后，与各自原配置逐项相同；cube 的65维观测、7维动作、奖励、0.5 N、最低点离桌面1 cm、无支撑保持0.25 s等原语义不变。

- `networks.py` 共享 GAT / flat 编码、MLP 和 tanh Gaussian Actor。PPO 使用独立 Actor / V 与两个优化器；SAC 保留原双 Q、target、alpha 和异步 Replay 流程，旧 state dict 名称保持一致。
- `ppo/` 实现固定版本的同步多卡 rollout、有限时域 GAE、clipped policy objective 和多个 mini-batch epoch。每卡一个 worker，首卡更新；每轮更新结束再广播 Actor / V。worker 的 GAE 保留时间与环境维度，仅将有效样本交给 Learner。
- 全局有效样本只做一次优势标准化，使用总体标准差和 `1e-8`，固定贯穿全部 epoch；V target 使用原始 GAE 加旧 V，不随归一化变化。
- Actor / V 分别反向传播和 `clip_grad_norm_`，默认范数限制各0.5。每个 mini-batch 更新前估计 `KL(old||new)`，超默认0.02则停止当前和后续 epoch；轮末另计算全局 KL。每 epoch 重新打乱样本并保留尾批，最后预算不足时不超采纳样本数。
- tanh 动作保存变换前的 Gaussian 样本，按同一原始样本重算 log probability，避免饱和动作的逆变换误差。
- `training_common.py` 共享 episode 记录、元信息、30秒成功率窗口选 best、周期 checkpoint 及恢复 best；`checkpoint.py` 统一算法加载与 RNG。旧 SAC checkpoint 缺少算法字段时仍按 SAC 加载；跨算法恢复拒绝。PPO 恢复参数、优化器和计数，重新建立环境与 rollout，不保存环境内存。
- 训练、评分和评估复用原入口。PPO graph 保存 `station-v.json/png`、`v_mean/v_std` 和 `v_*_spearman`，按 grasp 初态 V 等权平均，不做动作采样；SAC 仍保存原 Q 语义。PPO 监控位于 `ppo/`，包含损失、KL、裁剪比例、Actor/V 裁剪前范数、实际 epoch / mini-batch 和累计 KL 停止次数。

默认 PPO 参数与操作命令见[运行指南](running.md#ppo-训练与评估)。未引入 ManiSkill、Gymnasium wrapper 或新的训练依赖；参考用户提供的 PPO / GAE 实现，保留本项目的 mjlab、DLS、动作和记录接口。

## 离线验证

最终相关测试与边界检查：

```bash
MUJOCO_GL=disable OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests/station_map -t . -q
MUJOCO_GL=disable OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest tests.test_layers tests.test_scripts -q
```

- 站位相关117项通过，13.842秒；层级、空 `__init__.py` 与脚本边界5项通过，1.080秒。
- 新增 PPO 测试覆盖全局归一化与固定 target、常量优势、GAE 的终止/异常/分段边界、clipped loss、Actor/V 独立裁剪、epoch 重排及尾批、KL 整轮停止、饱和动作概率、flat/graph 更新、checkpoint 推理一致与继续更新、旧 SAC、双 worker 同步预算、V 聚合/排名、监控和算法分派。
- 全仓回归尝试运行391项，失败1项、错误9项、跳过3项：当前独立 RL 环境缺少 cuRobo（9项）和 rtree（1项），未修改这些规划测试或安装无关规划依赖。不能报告全仓回归通过。
- `git diff --check` 通过。日志保存在实验单元的 `runs/software-checks/`。

## 真实 GPU 短验证

原始结果：[实验单元](../../../outputs/experiments/ppo-integration/README.md)；汇总：[verification.json](../../../outputs/experiments/ppo-integration/analysis/verification.json)。启动前实时查询资源：GPU0–3利用率0%、各约72,831 MiB空闲，GPU4–7约88%–90%利用率；仅使用GPU0–3。

全部短验证每 worker 2个环境，rollout 8步、3个epoch、mini-batch16，临时回合0.4秒，验证环境1个。配置、日志、模型、指标、视频和生成输入均在对应 run。采用本工作区独立 `.venv`，借用已安装的 RL 包路径，项目源码指向本工作区，未修改原 actor-critic 环境。

| Run | 算法/设备 | 累计健康样本 | 累计 rollout 更新 | 实际成对 mini-batch 更新 |
| --- | --- | ---: | ---: | ---: |
| flat-single | PPO / GPU0 | 48 | 3 | 5 |
| flat-dual-final | PPO / GPU0、1 | 97 | 4 | 7 |
| graph-single | PPO / GPU2 | 48 | 3 | 9 |
| graph-dual-final | PPO / GPU2、3 | 96 | 3 | 17 |
| flat-resume | PPO / GPU1，由 flat-single 的 latest 恢复 | 64 | 4 | 7 |
| sac-switch | `--algorithm sac` 覆盖 PPO 配置 / GPU0 | 48 | SAC更新6次 | 不适用 |

`flat-dual-final` 每轮采纳 `[32, 32, 32, 1]` 条，策略版本 `[0, 1, 2, 3]`；`graph-dual-final` 为 `[32, 32, 32]`，版本 `[0, 1, 2]`。接收端核对版本一致，KL 停止实际触发，所有 learner 数值和 checkpoint 参数有限，采样结束写出 `sampling_complete` 并正常退出。初次双卡结果保留在 `flat-dual`、`graph-dual`；公共记录代码整理后用 `*-final` 复核，未替换旧 run。

`flat-resume` 从48条/3轮更新继续到64条/4轮更新，未随机预热。`sac-switch` 使用短验证专用 warmup0、Replay64、batch16、每8条更新一次，完成6次真实 SAC 更新。

独立重载评估：

- flat：`flat-single/evaluation/`，20个健康固定初态随机策略试验，成功0/20，均timeout，无基础设施失败，20个回放视频全部生成且帧数非零。
- graph：`score.py` 输出 `graph-single/scores/station-v.json/png`，`evaluate.py` 正确读取 V 文件，单站位10次健康试验成功0/10，V 相关性因单站位/恒定结果记为null；训练终点另生成随机与均值策略视频。

这些是训练软件与真实物理接口验证。回合被缩短为0.4秒，graph 原成功协议要求保持1秒，因此该短验证不能衡量抓取学习能力；没有启动正式预算训练，没有收敛或泛化结论。1卡与2卡经过实测，3–8卡使用同一固定worker布局，未逐卡数实测。
