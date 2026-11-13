# RL TensorBoard 监控交付

- 状态：done
- 日期：2026-11-13
- 替代：无
- 关联：[运行指南](running.md#tensorboard-实时监控)

## 目标与执行

2026-11-13 17:34 +08:00：从 actor-critic 提交 `830a81c` 创建独立工作区 `.worktrees/rl-tensorboard`，分支 `codex/rl-tensorboard`。应用的工作区创建工具不可用，改用 Git 创建独立工作区；未复制原工作区的未提交配置或报告，未修改原训练目录和进程。

由 Learner 在 `<run>/tensorboard/` 写事件，默认每 30 秒汇总刷新；可通过 `training.tensorboard_every_seconds` 调整。涵盖全部更新的 loss / entropy 均值、最新 Alpha、健康完整回合与全部 13 项奖励、接触次数、运行状态和带实际 scope 的评估标量。预热回合与策略回合分组，无样本和未知相关性不补零。区间累加张量 detach 后留在设备上，刷新时才读取标量。

删除四种训练曲线 PNG 的生成逻辑，保留空间 Q 图、Value-Success 图和视频；保留原有 JSON / checkpoint 节奏、best 选择和末尾评估流程。训练结束及评估前补写，退出时关闭 writer；同目录恢复隐藏恢复点及其之后的旧事件。同步 RL 依赖、smoke 入口、配置说明与运行指南，纠正指南中已与源码不符的 best 模型说明。

## 验证

借用 actor-critic 的独立 RL 解释器，通过 `PYTHONPATH=src` 加载新工作区源码，不修改共享环境。TensorBoard 版本为 2.21.0。

- 39 项 RL 离线测试通过：`MUJOCO_GL=disable PYTHONPATH=src <actor-critic>/.venv/bin/python -m unittest discover -s tests/station_map -p 'test_rl_*.py' -t . -v`。
- 真实事件文件在 writer 关闭之前即可读取；可控时钟验证 29 秒不刷新、30 秒刷新、所有梯度更新均值、13 项奖励、健康回合筛选、预热分组、空区间不补零和末尾补写。
- 训练集成测试覆盖新训练、不同目录恢复和同目录恢复；在首次 checkpoint 之前读取到事件，确认 Sampler 关闭后才运行最终 best 评估。实际事件读取器确认恢复点之后的旧事件被隐藏，累计 transitions 不重置。
- 首次以 `MUJOCO_GL=egl` 执行时因环境缺少 EGL 库导致 3 项导入失败；切换不渲染模式后相关测试通过。未改动系统图形库。
- 主 checkout 环境运行仓库离线回归：343 项，320 通过、20 跳过、3 项导入失败。失败模块为 `test_rl_best`（缺少 torch-geometric）、`test_rl_grasp_reset` 和 `test_rl_rewards`（缺少 mjlab）；这三个模块已在上面的 RL 专用环境中通过。没有把全套回归描述为全绿。命令为 `MUJOCO_GL=disable PYTHONPATH=src <main>/.venv/bin/python -m unittest discover -s tests -t .`；日志位于 `/tmp/rl-tensorboard-regression.log`。
- 训练入口 `--help` 及 `git diff --check` 通过；全套检查包含分层约束和脚本入口测试。

2026-11-13 17:36:25 +0800：完成实现和软件验证；当前正在运行的训练、原工作区的已有修改均保留。

## 限制

此次验证为 CPU 软件测试及真实 TensorBoard 文件读写，没有启动长期训练、GPU rollout 或视频渲染，不证明 Actor 学习效果。更新检查发生在采样批次及单次梯度更新之间，长时间阻塞会推迟刷新；现有正在运行的训练没有新增 TensorBoard，不做历史日志转换，也未测量训练吞吐变化。
