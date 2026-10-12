# 躯干边界容差修复

## 执行与结果

针对 `no-edit-v080-val103-cup30-20261010-163014` 的 S0024/t4 关闭夹爪时 `InvalidAction:torso height out of range`：已实施增量修复，不修改旧 attempt、原始资产或关节/执行器限位。

- 新版 no-edit 命令输入距 `[0,0.738]` 边界≤0.003rad时裁剪，非有限值或更大越界仍拒绝。
- `torso_command_adjustments.jsonl` 记录原输入、提交值、阶段、时间和容差；trajectory 原始动作与实际提交动作分别保留。
- 实测 h 越界≤0.003rad不终止；越界量与阈值逐物理子步记录。超出容差是 `torso_feedback_limit` 操作失败，归因 TrackingFailure。
- close、hold及open保持最后已提交合法目标，不将实测超调变成命令。没有修改qpos、重置或传送。
- 原碰撞检查、旧后端动作校验、搜索预算和严格抓取判定保持不变。

源代码：`robots/torso_command.py`、`runtime/no_edit_execution.py`、`runtime/no_edit_v2.py`、`validation/no_edit.py`。

## 验证

全套回归206项、跳过8项，其余通过（23.417秒）。日志：`outputs/logs/torso-boundary-regression-20261010.log`。

新增测试使用原反馈0.739327605117437：容差内允许、命令裁剪、last target保持、记录原始动作；并验证更大越界反馈停止、NaN/Inf和大幅命令越界拒绝。测试替身不是物理抓取成功证据。

CUDA1真实重测采用原S0024和种子1578639238，新输出：`outputs/diagnostics/v080-torso-fix-S0024-seed1578639238/`。不覆盖原结果；未启动全量采集。真实重测 **success / mobile_pick，259步，114.306秒**。夹爪关闭20步均保持h=0.737261950969696；close实测最高0.7395097101688121，超上限0.00150971rad，处于0.003rad容差内，不再异常停止。四视角视频已保存。

源码摘要已变化，后续采集须新建run-id，不能混合续跑旧冻结版本。
