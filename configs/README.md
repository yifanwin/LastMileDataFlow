# 配置入口

历史 `builds/`、`stations/`、`tasks/foundation.json`、四 Agent 请求与 API 配置已删除。
本轮保留核心命令当前使用的配置，不改数据协议或服务器路径默认值。

| 位置 | 用途 |
|---|---|
| `scene_datasets/` | 场景库配方、机器人/传感器配置 |
| `case_edits/procedural_obstacles_*.json` | 程序化障碍物配方；目录名后续整理 |
| `case_edits/no_edit_*_final_stations.json` | 现有增强站位输入配置，待统一输入协议 |
| `no_edit/` | cuRobo / MolmoBot 逐点评测和连续采集参数 |
| `lastmile_datasets/v0.json` | 场景库驱动的 LastMile 采集 |
| `policies/`、`robots/`、`scene_eval/` | 策略、机器人与 native 评测设置 |
| `collection/` | 必要记录参数；旧协议字段待步骤三清理 |
| `curobo-v080-pin.json` | 现有规划后端版本检查输入；本轮暂不改变 |

配置相对路径按 JSON 所在目录解析；当前示例包含本机路径，需要按实际部署调整。
动作和现有输出说明见 [data-format.md](../docs/datas/data-format.md)，统一路径与独立环境将在后续步骤处理。
