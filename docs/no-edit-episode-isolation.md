# no_edit：episode完整生命周期隔离

## 分类与继续策略

| 类型 | 状态 | 调度 |
|---|---|---|
| 单次机器人放置/初始化异常 | initialization_error | 记录，关闭本次资源，继续 |
| 单次执行/数据异常、已消费的模型错误响应 | episode_error | 记录，继续新独立仿真 |
| 单次编码、轨迹写入、收尾异常 | recording_error | 保存已有证据与旁证，继续；不得宣称录制完整 |
| 已分类仿真失效 | simulation_failure | 结束当前episode，继续；结果不可评价 |
| 单次墙钟预算 | timeout | 保持原预算，单列统计，继续 |
| 模型RPC等待超时、连接/进程失效 | infrastructure_error/shared | 停止当前scene，不复用潜在错序动作 |
| CUDA/GPU上下文错误、OOM、磁盘满/只读/不可写/I/O失效 | infrastructure_error/shared | 停止，不吞掉共享资源故障 |
| 用户中断、全局预算 | 原中断/预算状态 | 不吞掉，不当作普通episode错误 |

普通失败/规划无解继续，不能与上述不可评价的错误混淆。
未知普通Exception默认归为局部episode错误，并保存类型、阶段、reason和traceback；已识别的共享错误例外。
异常边界是`execute_isolated_episode`；两个runtime自身也有装饰器保护，覆盖try外初始化和finally内收尾。
ContextVar追踪当前episode的Simulation、recorders、gzip/JSONL文件；异常后释放，不恢复/传送已begin的仿真，不关闭源baseline。

## 模型响应

只有完整接收并解析的服务端错误响应可归为局部错误：RPC流仍同步，下一attempt由服务端原有逻辑reset policy。
CUDA/OOM等共享错误仍停止。poll/recv/send断连、响应等待超时保持共享故障，不能简单继续发送下一请求。
此版本不自动重启失效模型服务或重置GPU。

## 记录

- 单次失败旁证：`scenes/val_N/episode_errors/<attempt>-<stage>-<unique>.json`。包括原因、阶段、类型、traceback、实际返回结果/记录步数。
- 初始化失败不凭空生成轨迹/视频；若没有建立recorder，记录步数保持null。`artifact_complete=false`、物理任务结果unknown。
- 部分初始化/收尾失败关闭已创建的流，保留部分attempt。已封存attempt及artifacts hash不改写；编码失败的局部解释写旁证，不修改原封存结论。
- collector的trials/progress/statistics/map写入隔离；保存最小错误记录。非共享错误继续，trials在任务尾部再尝试持久化。
- 父进程数据集索引跳过记录不完整的task；逐段读取/导出局部异常独立记录并跳过，不因单段坏数据终止其余结果汇总。共享存储异常仍停止。
- task输出包含`recording_errors`与`recording_complete`；存在记录缺口时禁止dataset_eligible。如果连最小错误证据也无法保存，停止而不是无证据继续。

## 统计及续跑

`completed_episodes`包含成功、普通失败、超时及局部错误，代表调度已消费的episode。
`terminal_trials`/success_rate仅计可评价结果和预算超时，不包含初始化、记录或仿真异常。
局部错误单列`episode_errors`；全部不可评价时success_rate为null，而不是0。
有局部错误时construction_rate为completed_with_episode_errors，不做cuRobo数据集自动晋级；MolmoBot仍可完成整组站位评估。
已结束的局部错误续跑时跳过，按station/trial去重，不无限重试。

仅影响加载新代码的进程。历史run冻结了代码摘要，不能绕过校验直接续跑；此次不改写、不重启旧run。
