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

- `case_edits/distance-train2.json`：只给抽象现象、真实场景来源和预算，不给实例/编辑坐标。
- `case_edits/settling.json`：max_settle_s=10，延长 train_0 静置时间；未放宽稳定/穿透阈值。
- `case_edits/views.json`：640×480，fovy_rad=π/4，有限成对补拍。
- `agent_api.json` 或三键 `.env`：使用已有服务配置；不要把密钥提交或复制到报告。

可选资产目录结构：`{"asset_catalog_version":"0.1","assets":[{"asset_id":"clock","xml_path":"clock.xml","root_body":"clock","category":"AlarmClock","type":"object"}]}`。
路径相对资产目录 JSON；普通单顶层固定/自由根 MJCF，不要求人工 verified 标签。
资产需实际可编译、有有限碰撞几何；关节化根、世界几何/灯光/控制/约束尚不支持。
详见 [case-edit 运行说明](../docs/case-edit-pipeline.md)。

`agent_api.json` 现为 `provider` + `providers`：两个 provider 分别名为 `dmx`、`xera`，各自保存独立三项 LLM 配置。
`provider: auto` 每次逻辑调用先第一个，服务失败再第二个；指定 `dmx` / `xera` 时不切换。
CLI `--provider` 覆盖文件选择；所有实际 HTTP 请求仍计入共享预算。空密钥默认禁用，`enabled:false` 可显式关闭。
本次用户指定第二个服务 `https://newapi.x-era.com/v1`，模型沿用 `gpt-5.6-sol`。完整脱敏示例见运行说明。
