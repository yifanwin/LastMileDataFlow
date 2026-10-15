> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# no_edit 的单 episode 超时隔离

适用于 cuRobo station trials 与 MolmoBot VLA station episodes。

- 单次 `TimeoutError`：记录 `status=timeout`、原始 `reason`、`episode_timeout`事件，保留实际步数、轨迹和已有媒体。结束该独立仿真，继续同站位的下一次trial及其它站位。
- 不再用 episode 墙钟超时决定整个采集任务 `incomplete`。原300秒预算不变，不为通过采集放宽预算。
- 超时单列 `timeout_trials` / `EpisodeTimeout`，不能写为普通抓取失败或成功。任务物理完成结果仍为unknown。
- `terminal_trials` 是已结束的预算episode数，包含timeout。成功率明确为“episode预算内成功次数/已结束episode数”，不是仅成功/普通失败样本的条件成功率。
- 续跑调度跳过已结束的timeout；兼容旧只读记录 `incomplete + reason=attempt_wall_clock_budget`，不重写旧attempt/artifact，也不把 `interrupted` 或其它incomplete当成超时。
- 同一station/trial重试的终结记录统计去重（保留最近一个），避免次数/分母膨胀。
- 真正中断、显式全局 `max_trials`、scene全局时间预算和基础设施错误仍可终止整体。模型响应超时/连接丢失是RPC基础设施故障，不能在未恢复通信同步时强行继续用潜在错序动作。

代码更新不会改变已结束或正在运行的旧进程。旧run冻结了代码摘要，不能绕过resume校验直接用新代码续跑；需要显式、可追溯的迁移续跑或新run。此次仅修复逻辑和回归验证，不自动覆盖/重启历史run。

## 生命周期隔离补充

初始化/执行/记录/收尾局部异常也已隔离；`completed_episodes`包含这些已结束episode，
但不可评价错误不进入`terminal_trials`或成功率分母。详见 [完整隔离策略](no-edit-episode-isolation.md)。
