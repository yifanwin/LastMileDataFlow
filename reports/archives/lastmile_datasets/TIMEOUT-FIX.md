> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# timeout 与抓取成功遮蔽：修复报告

**已修复成功判定时序及超时收尾，未放宽物理标准或 300 秒预算。** 旧数据复核输出独立派生结果，不覆盖已封存 publication。真实 GPU 单条诊断和全包复核的最终结果见下方证据更新。

## 1. 执行过程与代码结果

1. 把执行终止和任务结果拆成 `execution_termination`、`task_outcome`。执行异常/超时仍保留，完整物理证据已成功时不被顶层 timeout 遮蔽。
2. 新增 `runtime/no_edit_evidence.py`：廉价必要条件筛选后，调用原完整 `evaluate_mobile_pick`，从不以“看起来抓住”或末帧替代判定。
3. 在 lift/hold 完成每个控制区间后检查完整证据；成功即安全结束当前采集，不再等待剩余固定 hold。最大 hold 仍 50 区间，未增加预算；没有改变任何 qpos/reset 规则。
4. timeout 收尾再判已有完整 Pick trace；未知、支撑、滑移、保持不足、trace 间断、约束违例不能升级成功。Open 超时不套用 Pick 判据。
5. 把真实 monitor 初值和选定控制保存为 `task_evidence_initial.json`，后续无需靠观测力猜 finger IDs。
6. 加入墙钟/仿真耗时及 planning/setup/rendering/recording/physics/validation 分类计时。recorder sealing 的耗时仅写入返回 trial 诊断，避免为加计时而重写 sealed attempt。
7. 新增 `review-lastmile-timeouts` CPU 入口，完整读取旧 timeout trace，验证所用文件与原 seal；物理成功候选做 canonical audit。旧初值无法可靠重建时保留 unknown。

新版语义 `no-edit-evidence-v2` 明确写入配置 packet/outcome。顶层 status 为旧 collector 的任务状态兼容投影；其 `success` 可以伴随 `execution_termination.timeout`，必须查看两个字段才了解全过程。基础设施/证据异常不伪装成成功。

## 2. 软件验证

- 新增 **15 个**合成/mocked 回归：成功已成立但下一 hold tick 将触发 deadline；单区间后成功无需 50 次；lift 区间成功；timeout 保留成功；保持不足、支撑、滑移、trace gap、早期约束违例拒绝成功；未知/Open；最大预算；计时异常；CLI 和独立输出限制；实际 finalizer 的双结果保存。
- 相关回归：**78 项，77 通过、1 跳过**：日志。旧测试 fixture 补全真实候选必须有的 row/source 和 torso 反馈；没有删除原断言。
- 全套：**441 项，429 通过、10 跳过、2 项已复现的基线失败**：日志。基线失败仍为 recorder 中途错误分类和 reach sampling preset 差异，详见 [实施报告](IMPLEMENTATION.md)。
- `git diff --check` 通过，无新增依赖，未提交代码。

软件回归不是 CUDA 或机器人成功证据。

## 3. 旧全包 timeout 只读复核

源：`outputs/no_edit/lastmile-v0-case1-full-20261013-unit-1108a8dd28688912`，20 点共 100 trial，其中 **64 timeout**。

派生目录：`outputs/diagnostics/lastmile-v0-case1-timeout-review-evidence-v2-full/`：

- `reviews.jsonl`：执行终止、物理判定、基线重建来源、源码/文件摘要、审计结果。
- `station_posthoc_rates.jsonl`：原成功率和追加审计成功后的证据率，显式标注 posthoc、不作正式重标定。
- `summary.json`、`manifest.json`、`checksums.json`：最终结果与封存。

日志：全包复核。首次串行复核已停止，少量 partial 结果另在不带 `-full` 的目录，未封存，不作为最终交付。

原始 trial table 摘要复核前后比较；所用 config/initial_state/outcome/physics 与原 artifact seal 比对。成功候选还做完整 canonical audit。不会因这个复核直接增加任何连续训练 episode。

## 4. 新 GPU 单条诊断

从原 S0034 t1 的 frozen scene、站位、seed 和配置创建独立新 Simulation/attempt，预算 **300 秒保持不变**，不复用旧运行结果。CUDA 0–7 按利用率/显存选择，本次 GPU 1。

新输出：`outputs/diagnostics/no-edit-evidence-v2-S0034-t1-replay/`；日志：真实试跑。原 attempt/source 不修改；不是完整重标定、S0→S1 恢复或泛化结论。

## 5. 最终证据更新

完整复核 **64/64 条 timeout 已完成并封存**：**8 条成功且 canonical audit valid、50 条未达到当前完整成功条件、6 条旧 monitor 基线不能可靠重建（unknown）**。6 条 unknown 不是新的物理失败。

原来 11 个在线成功之外，有 8 个已被墙钟状态遮蔽的审计合格物理成功；合计 19/100 是 posthoc 证据统计，不是新一次实际标定，也不是训练数据。源表摘要前后相同，`train_episodes_added=0`。

S0034 由原 1/5 变为证据 3/5；全包最高证据率仍为 **60%**（包括 unknown 全部按潜在成功计入的上界也未到 80%，没有自动启动恢复），不自动将派生表伪装成原 benchmark 校准。所有物理标准保持不变。

新 GPU 单条诊断的实际 outcome 已为 **success**，`execution_termination=completed`；墙钟 **270.76s < 300s**，推进 **11.00s** 仿真。双指/抬升/无支撑/滑移和完整约束判据通过，尾部合格保持 **2.032s**，最大抬升 **10.09cm**。共 220 个控制区间，其中 hold **21 个**，不再等待原固定 50 个；旧 t1 在 hold 35 个时被 timeout 截断。

本条计时：planner setup 38.20s、grasp planning 22.87s、motion planning 4.03s；simulation + 逐物理 tick monitor/trace **191.52s**；recording 7.34s、rendering 0.93s、完整任务验证 0.33s。主要测得耗时在物理推进及证据检查/逐 tick 写入这一合并桶，不能将 191.52s 全部归为 MuJoCo 计算，亦不能再猜测主要是渲染导致。

最终 canonical audit **valid=true，220 executed steps、221 frame groups、issues=[]**：审计。实际运行脚本保存为 `smoke_runner.py`。执行根目录保留原 collector 的状态软链接，独立 `publication/` 封存诊断汇总并绑定子 attempt 摘要；子 attempt 未改写。含 recorder finalize 共 **272.74s**。

**四项修复均完成验证；尚未重新运行全部 100 次标定、连续恢复采集或 MolmoBot 训练。** 复核后的最高证据率仍 60%，没有达到固定 S1=80%；没有凭修复自动扩大预算或降低阈值。
