# 三类配置

- `robots/rby1.json`：显式模型路径；其他控制参数和初态默认值由 `RobotConfig` 集中定义，运行时完整冻结。
- `tasks/foundation.json`：第一阶段仅执行 short_action，不声明抓取成功或 case 实证。
- `collection/smoke.json`：最多 6 步、80 次初始化候选、三相机 320×240、轻量协议与零 Agent 调用。

路径以 JSON 所在目录为基准。默认资产路径只是本机示例，部署到其他机器必须显式配置。
配置、动作布局、限制和数据格式见 [data-format.md](../docs/data-format.md)。
