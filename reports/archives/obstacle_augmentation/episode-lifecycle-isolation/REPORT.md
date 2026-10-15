> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../../README.md)。

# 单 episode 异常分类与完整生命周期隔离

## 结论

已在 `procedural-obstacle-augmentation` worktree 修改 cuRobo/no_edit 与 MolmoBot VLA 采集链路。局部初始化、执行、收尾和记录异常会记录证据、释放本次资源、继续下一 episode；共享资源故障仍安全停止。此次没有重启历史采集，也没有进行真实 GPU/VLA 验收或提交 Git。

## 执行过程与改动

1. 新增 `runtime/episode_guard.py`：统一分阶段分类，覆盖 runtime 原先 try 外的初始化及 finally 内的收尾；通过 ContextVar 跟踪本次仿真、recorder 和文件流，出错后关闭本次资源，不关闭 baseline。
2. 两个 runtime 与 collector 接入统一边界。构造失败、记录失败、视频收尾失败均有独立处理；用户中断不吞掉。
3. MolmoBot 区分完整消费的模型错误响应与连接/响应同步失效。前者可继续；RPC 等待超时、断连及 GPU 失效不能盲目复用服务。
4. trials、progress、statistics、地图、task result 与父进程逐段数据导出增加记录隔离。局部记录异常保存旁证，标记记录不完整、禁止数据集晋级；共享存储故障或最小旁证无法保存时停止。
5. 更新统计及输出契约，补充故障注入回归。新增旁证，不改写已封存 attempt、hash 或旧 run。

| 故障 | 分类 | 行为 |
|---|---|---|
| 放置/初始化异常 | initialization_error | 保留原因，继续下一独立 episode |
| 局部运行异常、已消费的模型错误响应 | episode_error | 保留 traceback，继续 |
| 局部轨迹、编码、收尾异常 | recording_error | 保留已有产物，继续，不宣称记录完整 |
| episode 墙钟预算 | timeout | 独立统计，继续 |
| 模型断连/等待响应超时、OOM/GPU上下文失效、磁盘满/不可写/I/O失效 | infrastructure_error/shared | 停止当前 scene，不伪装局部错误 |

`completed_episodes` 统计已消费的 episode；`terminal_trials` 与成功率只纳入可评价结果及预算超时。局部错误单列 `episode_errors`，不当作物理任务失败；全部不可评价时成功率为 null。续跑按 station/trial 去重并跳过已结束的局部错误。

旁证位于 scene 的 `episode_errors/`，包含阶段、异常类型、原因、traceback 及实际结果。初始化尚未建立 recorder 时步数为 null，不生成虚假轨迹或视频。

## 验证结果

- 14 项专项故障测试：全部通过。覆盖两条 collector 的继续与续跑、初始化及部分 recorder 构造失败、执行记录异常、收尾、资源清理、结果序列化、模型通信、共享故障、父进程导出隔离和用户中断。
- 包含真实 MuJoCo MjModel/MjData 的**简化模型**初始化故障测试：clone 关闭、baseline 状态不变。不是实际 RBY-1/场景或 GPU/VLA 验收。
- 全量 unittest：336 项，326 通过、9 跳过、1 失败。失败项为 `test_model_defaults_are_from_selected_sim_configs`：当前配置半径 `0.7928056080178096`，测试仍预期 `2.0`；没有为使测试通过而回改当前配置。
- compileall 与 `git diff --check` 通过。
- 专项日志、全量日志。

## 剩余边界

仅新加载代码的进程生效，已经运行或冻结旧代码的 run 不会自动更新。本次未启动新的实际采集。共享故障不自动重启模型或 GPU；原生进程崩溃及无法保存最小证据仍不能保证继续。共享故障识别使用明确类型、errno 和 GPU 错误模式，不承诺识别所有底层故障。

本报告是代码交付与故障回归报告，使用分类表和可复查测试日志比 RGB/统计图更准确，未生成新场景图。

详细契约见 episode 隔离说明。
