# SAC 连续训练与末次独立验证

- 状态：done
- 日期：2026-11-12
- 替代：11-startup-validation.md 中的周期验证安排
- 关联：[运行指南](running.md)、[上次启动流程](11-startup-validation.md)
- 记录时间：2026-11-12T16:09:02.561297+08:00

## 用户决定与实际停止

按用户最新要求停止当前训练，取消训练中的周期验证与对应采样暂停，只在训练预算结束、采样器全部关闭后，对最终策略独立做一次 Validation。本次不重启训练，也不对中断的旧 run 补做验证。

旧 run：outputs/experiments/distributed-sampling/runs/train-20261112-133445-g0123-direct-1m。2026-11-12 16:05:54 +08:00 终止进程组 2647529；四个 Sampler 和主进程均已退出，GPU 0–3 显存释放。latest.pt 已实际加载核对：800000 transitions、6093 次更新。预算 1000000 未完成，原始日志、旧验证及权重均保留，不宣称训练完成。

## 实现

- 删除训练循环里的周期 Validation、验证调度、恢复旧 best 排名及对应在途经验 pending 列表；默认配置删除 training.eval_every，旧输入中留有此字段也不再触发验证。
- 预算完成后仅在关闭采样器时暂停并排空有限队列，统计未使用经验，随后关闭所有 Sampler；不是运行中暂停采样去验证。
- 保存最终 latest.pt，再做一次独立 Validation；验证不会进入 Replay、不更新网络。原有验证站位、成功判定和视频口径保留。
- best.pt 保留为本轮最终已验证模型，不再按旧周期分数选择历史模型；末次全合法站位 Q 图也用同一最终策略。
- 保留无启动验证、无 initial.pt；未修改奖励、控制、SAC 算法与全局更新比例。

## 验证与边界

相关软件测试 14 项通过（test_rl_evaluation、test_rl_sampling、test_rl_video、test_rl_sac）；随后简化末次保存函数后，重跑评估相关 2 项通过。新增流程检查在模拟采样中跨越多个旧评估间隔，确认仅在 5 批经验采集完成且 workers.close 后调用一次验证，验证前已保存 latest；覆盖从头训练及带历史 best 的恢复路径，确保最终模型写入 best，不再加载旧 best。git diff --check 通过。

没有启动新的 GPU 训练或真实末次 Validation，未将软件回归视为学习效果或加速实测。修改位于 actor-critic 工作区，等待用户下一次运行指令。
