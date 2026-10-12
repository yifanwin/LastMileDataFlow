# 数据格式 v1

## Raw-first 统一采集协议（新增实时记录，旧格式保留）

[cuRobo 数据采集统一协议](canonical-data-protocol.md) 定义 `raw / canonical / derived` 三层、
时序与标定、完整机器人状态、原始/实际下发指令及结构化 `executed_action`。
cuRobo 只作为 expert；MolmoBot/π0 格式由版本化 Adapter 生成，不替代 raw。
新采集默认开启实时 raw/canonical sidecar，MolmoBot Adapter 单独导出，详见 [使用说明](raw-first-implementation.md)。
历史 attempt 不自动合规；下面的 v1 动作、attempt 和快照契约保持不变。

## 可选 MolmoBot 协议（不改变旧 v1）

`robot.control_profile=molmobot-native-v1` 时采用
`rby1-multitask-molmobot-native-v1`：索引布局仍为20D，夹爪为原生motor命令
（范围[-100,100]），**不是米值**。状态输入的夹爪仍为实测位置。
不得按旧位置协议重放native动作；重放须使用冻结的控制profile/model。
原始预测、策略后处理动作、执行器限位后的命令和ctrl分别保留。
三层时序100/20/4ms，详见 [VLA评测说明](molmobot-station-evaluation.md)。

## 配置与坐标

配置使用 UTF-8 JSON。三类配置分别在 `configs/robots`、`configs/tasks`、`configs/collection`。
路径相对于**配置文件**解析；CLI 显式路径相对于当前目录解析。配置未知字段、非法预算和非有限数值拒绝。
运行使用 `config.json` 冻结全部默认值及显式值，用 SHA-256 标识配置。

世界坐标完全沿用编译后的 MJCF：右手系、Z 向上，位置单位 m、关节角 rad。
姿态统一 `xyz_wxyz`，不混用 xyzw 四元数。ProcTHOR 原始 JSON 的轴不直接进入执行协议；
初始化从实际 room mesh 提取世界 XY 范围。

实例 ID 以 metadata 的稳定实例键为准，不以 MuJoCo 数字 body ID 作跨版本标识。
`instances.json` 同时保留源 object ID、asset ID、MJCF 名称映射与本模型 body ID。
缺少资产身份时记录 `null`，不猜测；同一 asset ID 对应多个实例时目标选择必须消歧。

## 动作协议

| 索引 | 意义 |
|---|---|
| 0:3 | 底盘世界 XY/yaw 相对增量 |
| 3:10 | 左臂 7 个关节相对增量 |
| 10 | 左夹爪绝对位置，[-0.05, 0] m |
| 11:18 | 右臂 7 个关节相对增量 |
| 18 | 右夹爪绝对位置，[-0.05, 0] m |
| 19 | 躯干绝对参数 h，默认 [0, 0.738]；下发 [0,h,-2h,h,0,0] |

头部没有动作维度，使用初始化时的固定目标。躯干 h 是联动关节参数，不应理解为世界绝对高度 m。
相对量以**下发时的实测关节**为参考；每条动作一次转换为实际位置控制目标，并在一个控制周期保持。
20 Hz / 0.004 s 默认用余数累积，在 12/13 个物理步间切换，长期平均 20 Hz；时间以真实仿真时间为准。

所有动作的维度、非有限值、相对步长、关节与执行器限位在任何 ctrl 写入之前完成验证。
不静默裁剪、不丢弃底盘动作，不开放额外自由度。

## 每个 attempt 的已实现目录

```text
outputs/attempts/<attempt-id>/
  config.json                   三类冻结配置及 schema_version
  source.json                   请求来源，加载失败也保留
  attempt.json                  ID、策略、任务、配置摘要、版本、起点、终止状态
  initialization.json           初始化方式、候选预算、位置和检查结果
  scene/
    model.mjb                   独立恢复用编译模型，不依赖旧工程
    version.json                源 XML/metadata 摘要、机器人配置、模型摘要、场景版本
    instances.json              稳定实例/资产/MJCF 映射
    initial.npz                 mjSTATE_INTEGRATION + 固定头目标 + 时钟余数
    checksums.json              上述文件的完整性摘要
  initial_state.json            全 qpos/qvel/ctrl、机器人组状态、所有实例和目标位姿
  trajectory.jsonl              实际发生的控制步；未执行时为空
  events.jsonl                  异常、动作拒绝、额外观察引用、告警和来源事件
  observations/<step>/<cam>.png 三相机 RGB；step 0 初态，step n 控制后状态
  observations.json             观察时间和图像引用
  videos/<cam>.mp4               只有真实发生执行才创建
  videos.json                   帧数、FPS、实际仿真时间戳与视频引用
  final_snapshot.npz            终止现场；可用于后续检查点工具，不作为 v1 重启入口
  final_state.json               终止状态（若状态非有限，对应诊断值为 null）
  result.json                    终止原因和三个独立结论
  artifacts.json                 产物摘要；异常时也尽力完整落盘
```

加载失败不会存在完整 scene；视频渲染/编码失败不会抹去已执行轨迹。
视频恒定 FPS，`frame_times_s` 保留真实时间；异常提前停在控制周期中间时不能靠 FPS 推断实际物理时长。
每个已执行步携带 raw_action、20 维 submitted_action、完整实际 ctrl/执行器名、关节目标、动作前后状态、
接触/规则检查。控制步的相机引用通过 `observations.json` 的 step+1 和 `events.jsonl` 对应，初态使用 step 0。

完整 integration 快照包含 MuJoCo 积分状态；保存固定头目标和时钟余数保证控制器约定也能恢复。
恢复要求模型摘要、MuJoCo 版本、状态类型、长度与数值匹配。v1 的 `--snapshot` **只恢复 initial.npz**，
创建独立新 attempt；失败现场的继续恢复/分支示教属于第四阶段专用接口，不能混用。

## 终止状态与三个结果

运行终止状态：

- `execution_complete`：有界动作流/预算结束，不表示抓取成功。
- `load_error`：源文件、模型、必需机器人接口或目标解析异常。
- `invalid_initialization`：起点/初态不合规或预算内未找到有效初始化。
- `invalid_control`：动作格式、数值、限位或固定底盘控制非法。
- `execution_failed`：实际 stepping 后发生严重物理异常。
- `protocol_invalid`：固定底盘实测发生超出声明范围的移动，数据仍保留。
- `infrastructure_error`：冻结、记录、渲染/视频等基础设施异常。

独立结果：

```json
{
  "scene_validity": {"status": "valid", "reason": "lightweight_initial_checks", "scope": "load_finite_state_and_penetration_only"},
  "case_condition": {"status": "unknown", "reason": "phase_2_3_not_evaluated"},
  "task_completion": {"status": "unknown", "reason": "no_task_success_evaluator"}
}
```

阶段一的 scene validity 只声明加载/初态/明显穿透检查范围，不证明正确支撑/柜层/把手语义。
case 与 task 永远不因短动作执行通过而自动判为成功。后续检查器扩展 status 与证据，但不能重写原失败。

## 轻量协议

**仅初始化**的严重重叠阈值：碰撞几何较小包围球半径 × 0.10，限制在 [0.010, 0.030] m；参数在冻结采集配置中声明。
这是一版显式的尺度相关启发式，**尚未逐类别校准**，不是所有物体共享一个绝对阈值。
`inspect_state(..., initialization=True)` 才执行重叠过滤。运行时不按穿透毫米数终止，
所有接触保留为日志；非有限物理状态、MuJoCo 仿真警告停止并单列 `simulation_failure`。
头/躯干反馈偏差记录告警；已有固定底盘/工作空间等显式动作协议检查独立保留。
每个物理 substep 采样，而非只检查控制周期末尾。
支持接触豁免不会关闭碰撞，不修改物体摩擦，不固定可动物体。

落位、抓持、把手专用验收和成功条件在第二/三阶段实现；阶段一不提供这些检查的假实现。

## 外部格式边界

`import-legacy` 只读取显式传入 episode 和资产根目录，转换成独立 JSON。
不导入旧代码、不读取批准文件、不继承成功标签、不绑定特定 Cup 或 grasp。
非法躯干联动、源场景摘要错配、资产路径越界、未支持删除必须报错，不能忽略。
独立导入数据包含全部物体位姿与新增资产清单；转换后运行仅需要新 JSON 与原始资产。

`integrations/waypoints.py` 声明 PlanResult 和执行格式转换；没有 cuRobo 规划器或 A* 搜索实现。
绝对 arm waypoint 按当前实测位置转换为 20 维相对动作；超步长必须先重采样，不能静默截断。

### V2 FOV 规划拒绝

`PlanResult.status` 增加 `fov_constraint_failed`；与其他非成功状态一样，`positions` 必须为空。`diagnostics` 保留 `head_fov` 稀疏验证证据、`constrained_planning` 与 `finite_budget_not_impossibility`。原生有限预算未找到合约束解时仍用 `no_solution`，诊断 `failure_reason=constrained_planning_failure`；硬 FOV 检查拒绝用 `failure_reason=fov_constraint_failed`。attempt 顶层状态继续沿用未执行的 `planning_no_solution` / 已执行的 `failure`，原因单独记录，不混作基础设施异常或全局不可达证明。此扩展只用于新版 V2 操作规划，不改变已有 20D 控制协议。

## 接触指标与仿真失败

新 attempt 冻结 `contact_policy="runtime-record-only-v1"`；旧证据及结果不重写，不混合成功率。
`result.json.extra.trajectory_metrics`（操作后端的 `outcome.json` 同样提供）包含：

- `task_success`: `true/false`，未验收或仿真/基础设施异常为 `null`。
- `simulation_valid`: 已执行且未触发仿真异常为 `true`；`simulation_failure` 为 `false`；未执行为 `null`。
- `forbidden_contact_count`: 每个物理 tick 的非期望接触 **body 对数**之和；同 tick 多个 mesh 接触点不重复计数。
- `contact_count`: 原始接触点观测数；不是碰撞事件次数。

质量标记仅排除正常底盘/地面、指定手指/目标及同手双指接触；其余机器人接触仅记录。
这个计数不表示物理非法，不自动触发停止、重试停止、失败归因或删除。
逐 tick 完整接触包含 body/geom、距离、接触力、时间；最终摘要最多保存32条示例。
成功且有质量标记的轨迹不自动降为失败，由后处理决定分级。
`simulation_failure` 保留诊断，但排除任务成功率分母；五次尝试中若有此类结果，覆盖保持 incomplete，而不是补成零。

## no_edit 单episode超时

独立episode的墙钟超时记录为 `timeout`，任务物理结果仍为unknown，保留原原因、步数及轨迹/媒体；
采集继续下一episode，不将其当成整个任务中断。`timeout_trials` 单列；`terminal_trials`
及预算内成功率包含已结束的超时episode。旧 `incomplete + attempt_wall_clock_budget`
只读兼容，真正中断仍保持incomplete。详见 [超时策略](no-edit-episode-timeouts.md)。

## Episode生命周期局部错误

no_edit新增 `initialization_error` / `episode_error` / `recording_error` 及独立错误旁证，
共享资源故障仍为infrastructure_error。`completed_episodes`与可评价的`terminal_trials`分开，
不可评价的错误单列episode_errors，不作普通失败；缺失记录声明recording_complete=false。
详见 [生命周期隔离](no-edit-episode-isolation.md)。
