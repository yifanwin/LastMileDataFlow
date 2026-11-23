# SAC 直接启动采样

- 状态：superseded
- 日期：2026-11-12
- 替代：[12-final-only-validation.md](12-final-only-validation.md)
- 关联：[运行指南](running.md)
- 记录时间：2026-11-12 13:34 +08:00

按用户最终决定，从头训练与恢复训练均不做启动验证或视频，不保存 initial.pt。环境和 Replay 初始化后直接启动采样与随机预热。周期及末次验证、视频、latest/best checkpoint 和最终 best 全站位评分保持不变。若恢复时缺少 best.pt，则清除无法对应模型的旧 best 评分，由后续首次验证重新选择 best。未增加配置项。

实现先在独立 worktree 完成，随后按用户“当前训练暂停，并入新流程后重新开始”的要求终止旧初始验证任务，将代码并入 actor-critic 后使用新 run、GPU 0–3、100 万健康经验重新开始。旧 run 与已有 initial.pt 保留。

相关软件验证：评估、采样、视频和 SAC 共 14 项测试；新增从头训练、完整恢复、缺少 best 模型/评分的恢复路径，确认首次验证发生在采样之后、不会写 initial.pt、末次验证及正常关闭保持。未将软件测试作为学习效果证明。新 run 的启动状态与命令写入其 launch.json 和实验 README。
