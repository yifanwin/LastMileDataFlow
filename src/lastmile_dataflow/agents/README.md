# agents

`protocol.py` 保留运行期诊断观察与动作协议。

场景构造使用现有模块：`case_normalizer.py` 生成模板，`construction_strategy.py` 选择局部上下文和编辑方向，`edit_proposer.py` 生成 DSL，`edit_reviewer.py` 复查实际配对图。中文提示词集中在 `prompts.py`，HTTP 与 provider 回退由 `case_gateway.py` 管理。

实际运行契约见 [case-edit-pipeline.md](../../../docs/case-edit-pipeline.md)。Agent 判断不能代替真实移动操作验收。

`contracts.py` 是四角色 wire Schema 的唯一来源，同时约束提示、HTTP 和本地校验。Reviewer 只检查 layout:N，收益意见单独保存。协议失败同候选修复，不重新编辑。

`CaseGateway` 每个逻辑请求最多5次尝试（含首次）；服务重试、格式修复、能力协商和 provider 切换共用上限。临时网络/限流/5xx 按2/4/8/16秒退避；永久错误不重发。每次计入整轮预算，图片和当前候选保持不变，调用日志包含尝试编号。
