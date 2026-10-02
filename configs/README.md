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
