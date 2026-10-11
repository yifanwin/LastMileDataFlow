# 配置职责

## 子目录速查（各目录内有 config-readme 详列每个字段）

| 目录 | 内容 | 加载入口 | 详情 |
|---|---|---|---|
| `robots/` | 机器人本体（模型路径/时序/相机） | `RobotConfig` | [robots/README.md](robots/README.md) |
| `tasks/` | 阶段一 foundation 任务 | `TaskConfig` | [tasks/README.md](tasks/README.md) |
| `collection/` | 采集/执行层公共配置 | `CollectionConfig` | [collection/README.md](collection/README.md) |
| `builds/` | 阶段二 v2 场景构建契约 | `BuildConfig` | [builds/README.md](builds/README.md) |
| `stations/` | 阶段三 v3 固定底盘站位图 | `StationConfig` | [stations/README.md](stations/README.md) |
| `no_edit/` | 无编辑原始场景采集（cuRobo/MolmoBot） | `NoEditConfig` | [no_edit/README.md](no_edit/README.md) |
| `policies/` | VLA 策略（MolmoBot）模型配置 | `MolmoBotConfig` | [policies/README.md](policies/README.md) |
| `case_edits/` | 自动 case→场景编辑请求与阈值 | `SceneConstructionRequest` / `SettleConfig` / `ViewConfig` | [case_edits/README.md](case_edits/README.md) |

## 顶层文件

- `curobo-v080-pin.json`：cuRobo v0.8.0 的**源码钉扎**。记录 `tag`、`git_sha` 与 8 个关键源码文件的
  SHA-256（`motion_planner.py`、`motion_planner_cfg.py`、`parser_urdf.py`、`link_params.py`、
  `data_mesh.py`、`solver_trajopt.py`、`cost_cspace_position.py`、`attachment_manager.py`）。
  `planning/curobo_v2.py: verify_version()` 在构造 V2 规划器时校验 `nvidia-curobo==0.8.0` 且逐文件摘要匹配，
  不匹配直接抛错、无 legacy fallback。改动此文件即改变 V2 规划的行为基线。
- `agent_api.json`：case-edit 四 Agent 的**本地独立 provider 配置**（`providers` 数组 + 默认 `provider`）。
  每项 `name`/`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`/`enabled`/`structured_output`/`json_schema_strict`。
  仅 `xera` 启用、DMX 禁用；`auto` 只访问 Xera。**含密钥，不要提交或复制到报告。**

## 各配置职责（原有简要条目）

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

## cuRobo v0.8.0：臂展半径 / 半底盘半径间距

独立配置 `no_edit/curobo_v080_arm_reach_half_base_radius.json` 不修改原
`curobo_v080.json`，仅改变两个字段：

- `radius_m = 0.7928056080178096`：当前 RBY1M v1.2 肩枢轴到 TCP 的最大自由空间臂展。
  连杆长度上界由合法关节位形的 FK 达到；不是随机采样 p99，也不加 5 mm 保守余量。
- `spacing_m = 0.2217592080002628`：实际底盘和轮子外接圆半径
  `0.4435184160005256 m` 的一半，**不是底盘直径的一半**。

保留目标锚点为圆心、每有效站位 5 次、原 cuRobo v0.8.0 / FOV / 物理验收规则。
这是当前模型的数值冻结配置，不随更换模型自动更新；模型变化时先运行
`scripts/verify_reach_sampling.py`，配置与模型不匹配会拒绝通过。
测量口径不代表任意方向、任意目标高度都可抓取，也不包含自碰撞约束。
运行命令见[无编辑采集说明](../docs/no-edit-collection.md)。

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
