- 状态：P0/P1 完成；P2/P3 见第 5 节
- 日期：2026-10-28
- 关联：[方案](../plan/01-mobile-policy-integration.md)、[运行手册](../../../docs/运行手册.md)、[配置说明](../../../configs/policies/README.md)

# PyRUA 原生策略与原语接入：实现与实测

## 1. 结论

- `evaluate-bench` / `evaluate-stations` 新增 `pyrua` 后端（`configs/policies/pyrua_panda_omron.json`）。PyRUA-Lean 的 `play_episode`、Python cells、`RobocasaRobot` API，以及 RPent 的 `RoboCasaToolkit` / `RoboCasaPrimitives` / `RLDXSkill` 都原样运行；替换的只有环境客户端：`ProjectEnvClient` 在进程内调用项目 Simulation，不启动 robosuite env server，也不生成 RoboCasa 任务。
- 一个 episode 只有一个 Simulation、一个 AttemptRecorder 和一条实际轨迹。任务成败只取项目的 pick/open 物理判定；Agent 的 `finish`、执行终止和任务结果分开记录。
- P1 实测动作、相机、深度和状态接口已经对齐（第 4 节）。中途发现并修正了两处真实协议差异：夹爪/模式的取值语义，以及底盘速度所在坐标系。
- RLDX-1（RLDX-1-FT-RC365）在本项目场景上可以加载和推理，原语链里的调用也正常；P1 的短调用没有完成抓取。它是在 RoboCasa 厨房上微调的，这里不对它在项目场景的能力下结论。

## 2. 实际接入链路

```text
evaluate-bench (pyrua) ─ run_agent_attempt ─ clone_sim + place_frozen_station + AttemptRecorder + OperationEvidence
                                        └─ PyruaPolicy.run ─ worker 线程: pyrualean.play.play_episode
                                                               ├─ host.boot → RoboCasaToolkit(ProjectEnvClient, RLDX client)
                                                               ├─ prompt/card/MCP python cell/codex exec
                                                               └─ robo.* → Toolkit → RoboCasaPrimitives → env.step(12D)
                                           episode 线程: OwnerThread.serve ← 所有仿真/渲染调用
```

| 位置 | 内容 |
|---|---|
| `runtime/execution/agent.py` | `run_agent_attempt`；`AgentEnvironment`：原生观测键、12D 动作转换、三相机 + navview 渲染、深度、world map、grasp contact、项目判定的 `success` / `task_progress` / `success_criteria`、预算；`OwnerThread` |
| `runtime/execution/vla.py` | `OperationEvidence`：从 `run_vla_attempt` 抽出的逐 tick 证据，VLA 与 Agent 共用 |
| `runtime/outcomes/{pick,evaluation,vla}.py` | `PickEvidence` / `OpenEvidence` / `TaskEvidence` 流式判定，`evaluate_pick` 等改为调用它们 |
| `policies/pyrua/` | `config.py` 配置；`client.py` RLDX-1 服务（RPent 自带 `vla_server.py`）与 `PyruaPolicy`；`host.py` PyRUA host、`ProjectEnvClient`、文字替换、`unmap_action` 原文 |
| `policies/config.py`、`runtime/execution_config.py`、`workflows/{lastmilebench,collection}.py` | 新增 `pyrua` 分派，五个 CLI 不变 |
| `scripts/install_pyrua.sh`、`tests/policies/test_pyrua.py`、`tests/smoke/pyrua.py` | 环境准备、离线边界测试、P1/P2 真实入口 |

上游版本按 PyRUA-Lean RoboCasa 文档固定：RPent `886b3b2`、RLDX-1 `ebcfd13`、navview 定义取自 robosuite `97cfbde`；PyRUA-Lean 用主仓库 `reference_repos/PyRUA-Lean`（`fdf6e87`），只读。最初拉到的 RPent HEAD（`a744bd5`）已经把 `ToolResult.result` 改名为 `.data`，与 PyRUA 不兼容，因此改用固定的版本。

## 3. 适配点与依据

| 项 | 原生（robosuite/RPent） | 项目 | 处理 |
|---|---|---|---|
| 夹爪 a[6] | `sign(a)`：+ 闭合，− 张开，0 保持 | `≥0.5` 闭合，否则张开 | 按符号转换，0 沿用上一条命令；RPent 的 hold 伺服会输出 (0, 0.5) 区间的值，不转换就会把“闭合”执行成“张开” |
| 底盘模式 a[11] | `>0` | `≥0.5` | 按 `>0` 转换 |
| 底盘 x/y | 当前朝向系；关节系 = episode 初始朝向，每轴 0.25 m/s 库仑死区 | 关节系与世界系对齐，按 yaw−初始 yaw 旋转 | 按 robosuite 的稳态速度换算后，补偿项目世界轴上的死区。两个坐标系重合时就是恒等映射 |
| 观测 | `robot0_eef_pos/quat`、`base_pos/quat`、`base_to_eef_*`、`gripper_qpos` | `record_state` 同源字段（站点/手 body 与 robosuite 定义一致） | 直接映射 |
| 相机 | agentview_left/right、eye_in_hand、navview（base 上的前下视相机） | 前三个在 MJCF 中；冻结的 bench 场景没有 navview | navview 用同一位姿的仅渲染自由相机（fovy 75，只在渲染时临时设置），不改 MJCF；`card` 和 `camera_profile.json` 里注明 |
| 深度 / world map | robosuite 变换矩阵 | 实际 GL frustum 标定（`rendered_camera`） | 由 K 和 T_world_camera 反投影；远平面和无效深度记 NaN |
| RLDX 动作 | `PandaOmronKeyConverter.unmap_action` | — | 原文照抄（robocasa 包和厨房资产都没装）；拼装顺序按 composite split |
| 成功 / 进度 | `_check_success` 及其源码 | strict-pick-v3 / open 判定 | `success_criteria()` 用文字写出项目判定的阈值；`task_progress` 给保持时长、抬升（pick）或开合进度（open），`task_feedback: success_only` 时为空 |
| reset | 评测中 RPent 拒绝 reset | `begin()` 后禁止重置 | 客户端的 `reset()` 只返回当前观测，不动物理状态 |
| RPent 录像 | 每步再渲染一次 agentview 作为 `episode.mp4` | 项目 recorder 录视频 | 关闭 RPent 录像，只保留一条轨迹；ledger 的 env_steps 按项目控制步计数 |

prompt 中描述 RoboCasa 厨房和 `_check_success` 的句子按 `(原文, 新文)` 逐条替换，每条必须恰好匹配一次，上游文字一变就会报错。经检查，实际 prompt 不含目标坐标、实例 ID、站位编号或 oracle；`oracle.json` 和恢复区域只在评测端使用。

另外两处与上游文字不一致，没有改原文：RPent/PyRUA 写的是 `move_base` 的 `+lateral` 向右，实测（以及 robosuite 侧向关节轴的正方向）都是向左；RLDX 实际 chunk 数由 `RLDX_MAX_CHUNKS=40` 固定，调用时传入的 `max_chunks` 会被覆盖（这是 RPent 本身的行为）。

## 4. P1：原语直连实测

`tests/smoke/pyrua.py`，走与 `evaluate-bench` 相同的 `run_agent_attempt` 链路，用固定代码代替 Agent，GPU 6。真值位姿只用于测量。

| 项 | cup bench e000000（pick） | dresser bench e000000（open） |
|---|---|---|
| world map 与 MuJoCo 射线求交对比（64 像素） | 中位 2.1 mm，p90 3.9 mm，最大 5.2 mm | 中位 1.5 mm，p90 3.2 mm，最大 5.5 mm |
| `move_base` forward / backward / lateral 的方向（相对车头） | 0.000° / 180.000° / +90.0° | 同左 |
| 修正前的 forward 方向 | −9.94°（= 站位 yaw，沿世界 +x） | — |
| `move_delta` ±5 cm | 全部 reached，主轴实际 4.6–5.6 cm | 同左 |
| 夹爪闭合 / 张开开度 | 0.0022 / 0.0773 | 同左 |
| `rldx_arm` | 40 chunk、320 步，未接触 | 40 chunk、320 步，未接触 |
| 一次运行 | 529 控制步，记录完整，墙钟 78.6 s | 529 控制步，记录完整 |

耗时（cup，同一工作负载每步平均，单独 GPU）：

| 每控制步（25 个物理 tick） | 修改前 | 修改后 |
|---|---|---|
| 仅物理 + Python OSC 控制器 | 0.057 s | 0.057 s |
| + 逐 tick 证据 | 0.178 s | 0.111 s |
| RLDX 三相机取帧 / agentview world map | 0.0065 s / 0.030 s | 同左 |
| RLDX 单次推理 | 首次 1.06 s，之后约 0.16 s | 同左 |

证据部分的提速来自两点：同一 tick 共用一份接触帧（以前每个 tick 要扫两遍接触）；未开启 `--record-physics` 时不再做每 tick 的 JSON 序列化（以前直接写进 `NullTrace` 丢掉）。逐字段比对 9000/9000 一致，对 VLA 路径同样有效。流式判定与原批量判定在 2696 组随机变体上逐一比对，结论全部一致。

## 5. P2 / P3

（待填）

## 6. 环境与偏差

- 按用户此前的要求，复用主仓库 `.venv`（Python 3.11、torch 2.7.1+cu128），没有在本 worktree 建 `.venv`，这和 AGENTS.md 的“本仓库 `.venv`”不同。RPent 运行依赖装在 `data/pyrua_site`，追加在 site-packages 之后；RLDX 专用包（transformers 4.57.1 等）装在 `data/rldx_site`，只在 RLDX 服务进程里前置。RLDX 上游声明 Python 3.10，这里在 3.11 上加载和推理已实测可用。
- `data/codex_home` 是干净的 Codex home（不开 memories，不带用户全局 AGENTS.md）；`auth.json` 是指向用户 `~/.codex/auth.json` 的链接。
- MuJoCo EGL 需要 `LD_LIBRARY_PATH=/tmp/lmdf-egl-libs/...`（同 pro5000 既有做法）。
- 离线回归：`unittest discover` 403 项通过（2 项跳过，HEAD 上原本也是这样）。
