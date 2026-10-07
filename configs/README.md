# 配置职责

- `robots/rby1.json`：显式模型路径；其他控制参数和初态默认值由 `RobotConfig` 集中定义，运行时完整冻结。
- `tasks/foundation.json`：第一阶段仅执行 short_action，不声明抓取成功或 case 实证。
- `collection/smoke.json`：最多 6 步、80 次初始化候选、三相机 320×240、轻量协议与零 Agent 调用。

路径以 JSON 所在目录为基准。默认资产路径只是本机示例，部署到其他机器必须显式配置。
配置、动作布局、限制和数据格式见 [data-format.md](../docs/data-format.md)。

- `builds/phase3-case1-cup.json`：本次原始 Cup 构建，目标/支撑与起点明确；不导入旧成功状态。
- `stations/case1-cup-probe.json` / `case1-cup-contact.json`：同站位 0 / 10 mm 接近深度对照。
- `stations/case1-cup-coarse.json`：26 个粗位姿、最多 2 个局部细化位姿；两臂/三高度是候选配置，
  32 次规划/4 次真实执行/1200 秒限制意味着配置可能未测，不能当成双臂实证。

站位配置 v3 与物理协议见 [phase3.md](../docs/phase3.md)。配置冻结后不可覆盖既有 run；使用新的唯一 ID。

## 自动 case-edit

- `case_edits/case1-val103.json`、`case1-5-val103.json`、`case3-val103.json`：当前单场景请求，不提供人工目标/支撑/机器人 base/编辑位姿。
- `case_edits/settling.json`：宽松构造阈值；取消源场景稳定窗口门槛，编辑范围仍检查稳定、严重穿透和支撑。
- `case_edits/views.json`：head 与动态辅助配对视图；辅助相机数量和搜索次数有界，覆盖不足明确记录。
- `agent_api.json`：用户本地独立 provider 配置；不要把密钥提交或复制到报告。

请求版本 0.2；数据集检索和真实移动任务验收当前明确 unsupported。
`asset_library` 为 THOR 根目录，按计划需求检索类别并实际编译，不需要人工资产资格表。

当前仅启用 `xera`，默认选择 `xera`，DMX 已禁用；`auto` 也只会访问 Xera，显式选择 DMX 被拒绝。
使用 `gpt-5.6-sol`、`/v1/chat/completions` 和 `structured_output=json_schema`，不自动退回文本模式。
`json_schema_strict=false` 兼容动态 roles/parameters 等字典及可选字段；服务端接收同一份 JSON Schema，程序仍逐项严格校验，不把非 strict 输出直接当作通过。
CLI `--provider`/`--model` 仅覆盖本次选择，所有实际 HTTP 请求均计入预算。

详见 [运行说明](../docs/case-edit-pipeline.md) 和 [当前实施计划](../docs/case-to-edit-pipeline-plan.md)。
