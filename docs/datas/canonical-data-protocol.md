# cuRobo 数据采集统一协议：Raw-first

**采集机器人真实看到、收到和执行的内容；模型输入格式留给 Adapter。**
cuRobo 是 expert 数据生成器，不是数据协议。MolmoBot、π0 或其他 VLA 的状态、动作和图像均不得反向限制原始采集内容。

协议标识：`raw-first-canonical/1.0.0`。状态：**MuJoCo 采集与 MolmoBot Adapter 已实现，CUDA/VLA 端到端验收待完成**（2026-10-11）。
当前实现及命令见 [使用说明](raw-first-implementation.md)。历史 attempt 不自动合规，此版本也不替换 `no-edit-dataset-v2` 的格式版本。
现有执行接口仍严格遵循 [20D 动作协议](data-format.md) 和 `robots/action.py`；本协议增加记录层，不开放额外控制自由度。

## 1. 信息只增不减，三层不能互相替代

以下为数据职责图，不代表三个独立数据集。整个 episode 包才是 Canonical Dataset。

```text
Simulator / Robot
        │ 采集原始传感器、状态、指令和实际控制
        ▼
Canonical Dataset（模型无关，成功/失败/异常证据均保留）
  raw/        原始 RGB / depth / qpos / qvel / actuator ctrl
        │ 显式单位、坐标和时序转换；原始数据不动
        ▼
  canonical/  robot_state / executed_action / task / scene / calibration / timing
        │ Dataset Adapter：版本、参数、输入引用、转换记录
        ▼
  derived/    MolmoBot 22D / native action / fisheye / π0 action / resize / 分析视频
```

- **MUST** 表示新协议必须满足；**SHOULD** 表示建议；可选数据也必须声明能力与缺失原因。
- Raw 是采样设备或渲染器的直接输出。仿真不拥有“无限分辨率原图”：保存采集配置选定的原生渲染输出及其配置，不能只留模型 resize 后的结果。
- 已采到的信息 MUST 保留；不能为了导出模型数据而删掉相机、关节、时间戳、失败或原始指令。
- 冻结后的 episode 不覆盖、不修改。后验标签、补充标定和新派生版本写入独立补充包，并引用原包摘要。
- raw/canonical/derived 是逻辑分层；允许无损分块、压缩和内容寻址，不能通过有损编码或静默降采样冒充 raw。
- 未采到的内容不能补造。事后 qpos 回放图像只能标为 derived replay，不是原时刻传感器观测。

## 2. Episode、帧与执行区间

一个 `episode_id` 对应一次连续真实执行；当前工程可与唯一 `attempt_id` 一一对应。
重试创建新 episode；同一连续 rollout 内的导航、到站、抓取、保持是 `phase`，不能用重置拼接成连续数据。
仅规划、加载失败和初始化拒绝也保留记录，但 `execution_status=not_executed`，动作区间为空，不生成虚假执行视频。

| 身份 | 定义 |
|---|---|
| `run_id / episode_id / task_id / scene_id` | 稳定标识；文件夹显示名不是唯一身份 |
| `frame_id` | episode 内递增的状态边界编号，初态为 0 |
| `step_id` | 控制执行区间编号，区间 0 连接 frame 0 → frame 1 |
| `sensor_id / sample_id` | 传感器身份及该流内唯一采样编号；异步相机不强行共用帧号 |
| `policy_step_id / control_tick_id / physics_tick_id` | 分别标识策略决策、控制提交和物理积分，不混用 |

### 2.1 不能靠固定 FPS 推断时间

每个状态帧及相机样本 MUST 保存 `timestamp`、`clock_id`、`frame_id`、`control_hz`、`physics_dt`。
`timestamp` 的单位固定为秒，仿真取实际 simulator time；实机取映射到公共单调时钟的采样时间。
物理积分 tick 为仿真精确索引；实机没有 physics tick 时使用 `null`，声明 `not_applicable`。
`control_hz` 是名义实际控制频率；策略频率另外保存 `policy_hz`。相机频率单独声明，不把三者混成一个值。
当前 position profile 的执行区间约 20 Hz；native profile 的策略/控制/物理分别为 100/20/4 ms，须按真实层级记录。

执行区间 MUST 保存：`t_start / t_end / actual_dt / requested_dt / physics_steps`、
`state_before_ref / state_after_ref / observation_before_refs / observation_after_refs`、
`completion=complete|partial` 和提前结束原因。
20 Hz、physics_dt=0.004 s 时，实际区间可以是 0.048 s 或 0.052 s；不能将两者都写成真实 0.050 s。
动态更改频率或物理步长时保存带生效时间的配置事件，不能只在 manifest 放最终值。

**因果关系固定为：**

```text
frame k: observation_k + state_k
       → command_raw_k → command_submitted_k → applied control [t_k, t_(k+1))
       → frame k+1: observation_(k+1) + state_(k+1)
```

末帧没有下一条动作；N 个完成区间应有 N+1 个状态边界。渲染失败不删除已执行区间，缺失观测明确标记。
共享边界上的相同时间合法；单个流不得时间倒退，ID 不得复用。连续区间边界须相接；间隙和丢帧必须作为事件保存。
意图动作在提交前被拒绝时只进入 commands/events，不生成 executed_action。

### 2.2 严格对齐不等于伪造同步

- 仿真：在同一个物理状态边界完成 state capture、各相机渲染和 camera pose capture；期间不能 stepping。
- 实机：保留 `source_timestamp / source_clock_id / receive_timestamp / exposure_start / exposure_end`，
  clock offset、drift、同步方法与不确定度。不可获得的曝光时间显式标记 unknown。
- canonical 的匹配记录 MUST 包含源样本引用、`alignment.method=exact|nearest|interpolated|unavailable`、
  `offset_s / uncertainty_s / tolerance_s / alignment_valid`。
- nearest 超出冻结 tolerance 必须判为无效；不能仅按“同一行”宣称同步。插值仅用于 canonical/derived，并保留两侧源样本及参数。
- 在线模型输入只能引用决策时已经可用的样本；事后对齐需标为 offline。后态不能泄漏进前态策略输入。
- UTC 创建时间用于审计，不代替执行单调时间；渲染、推理、规划的 wall time 另存性能元数据。

## 3. 图像和标定必须描述真实成像

### 3.1 Raw RGB 与 depth

头部、左右腕部以及启用的第三人称相机 MUST 各自保存直接输出的 RGB。
相机注册表列出全部可用相机和 `role=robot_observation|observer`；不可用或关闭时声明原因。
第三人称是观察者，默认 `policy_input=false`，不能隐式送入机器人策略。

RGB 保存无损 PNG 或无损数组；注明 `width / height / dtype / color_space / channel_order / image_origin / encoding`。
resize、crop、fisheye warp、去畸变、文字覆盖、拼接、小地图和墙体透明化全部进入 derived。
MP4 默认仅用于浏览；现有 libx264 视频不能代替逐帧无损 RGB。

Depth 能力声明为 `available|unavailable|not_applicable`，采集状态声明为 `recorded|disabled|error`，附原因。
新采集 SHOULD 启用可用 depth；关闭必须冻结原因，不能把“未打开渲染”写成传感器不存在。
有 depth 输出时 MUST 保存原始数值与有效 mask；不得只保存可视化灰度图。
Depth metadata MUST 包含 `dtype / native_unit / scale_to_m / representation / near_m / far_m / valid_range_m / invalid_encoding`。
`representation` 区分 optical-z、ray-distance 和非线性 depth-buffer；量化/缓冲转换记录公式或实现版本。
背景、遮挡、饱和、NaN/Inf 或零无效值按真实约定标记，不能全部当作有效 0 m。
RGB-depth 不共用光心或分辨率时分别标定；重投影对齐为 derived。

### 3.2 每个相机的标定

`canonical/calibration.json` 保存静态模型；`camera_poses.jsonl` 保存每个实际图像样本对应的动态外参。
即使头角度锁定，底盘或躯干移动也会改变头相机世界位姿；腕相机和跟随式第三人称不能只保存初末标定。

| 字段 | 必须明确的语义 |
|---|---|
| `camera_id / calibration_id / profile_id` | 身份、版本、有效区间和标定来源 |
| `resolution / fovy / projection_type` | `[width,height]`、角度单位、pinhole/fisheye/orthographic/其他实际模型 |
| `intrinsics` | pinhole 的 K（pixel）；其他模型的参数及投影定义，不能硬塞 K 冒充完整标定 |
| `distortion` | 模型、系数及顺序；无畸变也显式声明 |
| `extrinsics` | `T_world_camera_optical`，4×4，满足 `p_world=T_world_camera_optical*p_optical` |
| `optical_frame / pixel_convention` | 光学系 x 右、y 下、z 前；像素坐标原点与像素中心约定 |
| `near_m / far_m / source / uncertainty` | 实际裁剪距离、来源（实测/渲染器/推断）和精度信息 |

保留 raw simulator camera matrix、源轴定义和转换矩阵，不能把原矩阵直接贴上 optical 标签。
若源相机轴是 x 右、y 上、-z 前，则 `R_world_optical=R_world_source*diag(1,-1,-1)`；仅在核对源约定后使用。
畸变或非中心投影采用版本化投影函数/映射；orthographic 的 fovy 不适用时写 null 和原因。
只有确认渲染器为居中、方形像素 pinhole 时才可由 fovy 推导 K，并标 `source=derived_from_renderer`。
派生 fisheye 的目标参数、映射、插值、边界填充和有效 mask 另存，不能覆盖 raw 标定。

## 4. Raw 状态保留完整自由度，canonical 分组便于使用

每个采样边界 MUST 保存完整 simulator/robot `qpos / qvel / actuator_ctrl`；不能只保存 MolmoBot 22D。
仿真 qpos/qvel 包括模型中的机器人和场景自由度，另用映射声明 robot subset；实机仅声明实际可测覆盖范围。
初始化/终止 integration snapshot 继续保留；执行器内部 act、传感器、电流、力矩、外力等可获取信号 SHOULD 保存。
物理异常的 raw 二进制数组保留真实 NaN/Inf；JSON 投影用 null + validity + 诊断，不抹掉原始异常。

`raw/model-map.json` MUST 保存关节名、类型、qpos/qvel 地址和长度、单位、机器人归属、限位、执行器名与传动/增益/控制语义。
qpos 与 qvel 不能假定同长度；四元数位置不是角速度，qvel 必须按源模型的切空间定义解释。
`ctrl` 不能一概叫 torque：位置伺服、velocity servo、motor 等由 controller/actuator 描述决定。

canonical 每帧提供：

- `base`：实际世界 pose、线/角速度及有效性。
- `torso / head / left_arm / right_arm`：有序 joint names、position、velocity、逐关节单位；可选 torque/effort 附来源。
- `left_gripper / right_gripper`：每指实测 position、velocity、可测 opening width，以及独立 motor command 引用。
- 可选 `left_tcp / right_tcp`：world/base pose 与速度，注明 TCP 定义和 FK/实测来源。
- 原始记录引用、变换版本、有效性 mask 和缺失原因。

世界系采用本工程编译后 MJCF 的右手 Z-up；SI 单位，姿态为 `xyz_wxyz`，四元数归一化状态明确。
base body 轴从资产核对并记录，不能凭“底盘”猜前向。跨数据集换轴记录源→目标矩阵，raw 不修改。
躯干 h 是联动关节参数，**不是米制高度**；实际六关节状态完整保留，h 与真实 torso world height 分字段。
不存在的部件用 `not_applicable`；无法测得的速度或 effort 用 unknown，不能填零表示已测到静止。

## 5. Action 分清意图、实际下发和实际响应

### 5.1 必须保存的三个事实

| 记录 | 内容 | 不能替代什么 |
|---|---|---|
| `command_raw` | expert/policy 原始请求、原格式与协议、决策时刻；若有预测和后处理，分别保留 | 不等于实际下发，也不保证可执行 |
| `command_submitted` + raw applied control | 校验/转换/投影后的命令、绝对目标、执行器 ctrl、控制提交时刻与生效区间 | 不等于机械运动或实际力矩 |
| `executed_action` | **实际已生效控制**及其执行区间、原始控制引用，同时记录实测响应 `measured_motion` | 不以规划 waypoint、请求增量或后态差分冒充全部 action |

`executed_action` 不是固定维度向量，而是结构化执行事实：`command_submitted_ref / applied_control_refs / measured_motion / completion`。
`measured_motion` 包含关节前后实测 position/velocity、关节 delta、base world/body delta、夹爪实际变化和目标跟踪误差。
训练 Adapter 必须显式选择 `supervision_source=command_submitted|applied_control|measured_motion`；这些标签不能同名混用。
通常命令监督与可重放控制更直接对应；实测响应另供分析或特定模型使用。仅用 Δq 不能唯一反推出 ctrl。

各控制分量 MUST 声明 `mode=absolute|delta`、`quantity=position|velocity|torque|motor_command|其他显式类型`、
`unit / reference_frame / reference_state_ref / joint_or_actuator_names / layout / valid_range / controller_profile`。
混合 20D 命令按分段描述，不能用一个全局 mode/unit 包住。
所有投影、限位、平滑、重采样、stationary-target latch 和拒绝附事件及输入输出；不允许静默裁剪。
原始命令拒绝后不存在 applied control；提交但未发生物理步时保存提交事件，不伪造已执行区间。

原始控制 MUST 覆盖各次实际控制更新。未变 ctrl 可用带生效起止 tick 的无损 run-length 表示；
周期内 ctrl 有变化必须分段记录，不能仅保存末尾 ctrl。高频 state 可额外保存，但采样 cadence 必须声明；控制边界状态不表示物理步级状态全部被采集。
实机无回执时只能声称 `submitted_unconfirmed`，不能把发包成功标为 `applied`。
实机没有可观测底层 ctrl 时保存发送的控制消息、回执与能力缺失，不能伪造 MuJoCo ctrl；须使用显式硬件采集 profile。

### 5.2 夹爪分开记录

- `gripper_position`：实测单指/双指关节位置（m）及名称；不是命令。
- `gripper_position_target`：position controller 的目标（m），仅该模式下适用。
- `gripper_motor_cmd`：native motor 数值、范围、执行器身份及单位/标定来源；`[-100,100]` 是控制范围，不自动等于 N 或 N·m。
- `opening_width_m`：仅在已知几何或实测时提供；不能简单把某个负关节位置改名为开口宽度。
- `state=open|closed|partial|unknown`：按实测状态和版本化阈值判定；另存 intent，不以 close 指令证明夹爪已闭合。
- `grasp_state=holding|not_holding|unknown`：独立接触/抓持验收证据；closed 不代表抓到物体。

native 和 position 两种 profile 的同一索引不能跨模式重放。可不适用字段用 null + reason，不猜测等价换算。

### 5.3 底盘保存绝对位姿及两套实测增量

保存世界实际 pose；每个执行区间的平面响应定义为：

```text
delta_xy_world = p_xy_after - p_xy_before
delta_xy_body  = Rz(yaw_before)^T * delta_xy_world
delta_yaw     = wrap_to_pi(yaw_after - yaw_before)
```

body 增量参考 **区间起始底盘系**；world/body 字段都注明该定义。
此处是有限位移，不是 wheel odometry 或 SE(2) log-map；如需 log-map 作为派生版本单独生成。
保存原始 yaw 和角速度；区间可超过 π 的转动必须另有连续 unwrapped yaw/高频证据，不能只靠 wrap 后差值恢复旋转圈数。
非平面机器人保留完整 SE(3) pose/delta；平面投影是额外字段。
当前 20D base command 仍为 world XY/yaw 请求增量；新增 body delta 是记录字段，不改变执行语义。

## 6. Task、scene、结果和 expert metadata

`task.json` MUST 保存 `language_instruction / language / task_type / target_instance_id / receptacle_instance_id`、
版本化 `success_condition`、判据参数/单位/持续时间、评测 profile 与来源。
无 receptacle 的 pick/open 用 null + not_applicable；受语言模板影响的任务也保留模板版本和原始指令。
目标和 receptacle 使用稳定 instance ID，不只用类别或 MuJoCo 数字 ID。

`scene.json` MUST 保存场景来源与摘要、全部实例/资产映射、初始物体位姿、支撑关系、障碍物、机器人初始 pose、
随机种子（场景、初始化、规划器等分别列出）、split 与编辑来源。已发生的物体运动保留逐帧位姿；
支撑关系记录 `source=metadata|geometry|contact|unknown`、判定时间与方法，不能将保守 AABB 障碍当成真实支撑关系。

结果 MUST 分开保存：

- `execution_status / termination_reason / failure_stage`：执行、终止及已知失败位置，未知不猜。
- `success`：true/false/null；null 表示未知、未验收或仿真异常不能判定。
- `simulation_valid / protocol_valid / recording_complete`：各自独立，成功不保证数据完整。
- `collision / contact_events / grasp_state`：按冻结定义及逐 tick 证据记录，未检测不能写 false。
- `scene_validity / case_condition / task_completion`：沿用现有分离判定，不以 execution_complete 代替 task success。

保持当前 `runtime-record-only-v1`：接触事实仅记录，不因增加 collision 字段就改变终止或任务判据。
contact-point 数、body-pair/tick 计数与 collision-event 数不混称；碰撞质量标签与任务成败分开。
规划 no_solution、FOV 拒绝、未测试、基础设施异常与真实任务失败分别记录；有限预算无解不是不可达证明。

`expert/` 可保存 cuRobo request、IK/waypoints、time parameterization、cost、锁定关节、碰撞世界摘要、FOV validation、
规划成功/拒绝原因、预算、耗时与规划器版本。各条关联 plan/step/phase ID；未实际跟随的 plan 不能进入通用 action。
规划碰撞验证、执行接触观测和任务验收是三个来源不同的事实。

## 7. 推荐物理布局与必要字段

以下为逻辑参考布局。当前实现保留 attempt 根，manifest/checksums 放 canonical，物理路径以 [使用说明](raw-first-implementation.md) 为准。
JSON/JSONL 用于元数据与引用，数组采用无损 NPZ；原始 v1 文件同时保留。
包内 derived 仅指封存前已生成的产物；封存后新建 `derived_packages/<episode_id>/<adapter>/<version>/`，
用源包 ID/摘要引用原始数据，不向已封存 episode 内追加文件或重写 checksums。

```text
episodes/<episode_id>/
  manifest.json                     # schema/profile/capabilities/versions/完整性
  raw/
    model-map.json                  # nq/nv/nu、joint/actuator 地址与语义
    states/<chunk>.npz              # 全 qpos/qvel/ctrl；初态和边界/可选高频
    state-index.jsonl               # state_id、frame/tick/time、chunk+row
    commands.jsonl                  # 原始预测/请求/后处理/提交记录及事件
    controls.jsonl                  # 实际生效控制和起止 tick/时间
    observations/<camera>/<sample>.png
    depth/<camera>/<sample>.npz     # 可选：原数值+mask
    observations.jsonl              # 样本身份、时间、编码、标定引用及缺失
    contacts.jsonl                  # 原始接触/力与逐 tick 时间
    scene/                         # 现有 compiled model、snapshot 与来源证据
  canonical/
    frames.jsonl                    # 边界、对齐记录、raw state 与 observation 引用
    robot-state.jsonl               # 标准分组及单位/坐标变换
    executed-actions.jsonl          # 控制事实+实测响应，step k: frame k→k+1
    task.json / scene.json
    scene-states.jsonl              # 动态实例位姿/关系证据
    calibration.json / camera-poses.jsonl
    outcomes.json / events.jsonl
  expert/curobo/                    # 可选规划、IK、collision/FOV 等诊断
  derived/<adapter>/<version>/
    manifest.json                  # 输入摘要、转换参数、输出字段与语义
    ...                            # VLA 数据、fisheye、crop、resize、分析视频
  checksums.json                    # 封存文件摘要（不对自身递归摘要）
```

manifest MUST 包含：`schema_version / collection_profile / run_id / episode_id / attempt_id / clock_definitions / sampling_cadence / capabilities`，
`robot_asset_id / robot_asset_version / asset_sha256 / controller_profile / camera_profile / simulator_version / code_commit / code_dirty / config_sha256`。
代码有本地修改时保留源码清单或 diff 摘要；仅有 commit 不足以标识实际运行代码。
硬件 profile 另存 robot firmware、控制器版本与传感器序列号/版本，simulator_version 标记不适用。
capabilities 区分 available/recorded/disabled/error；完整性与 schema 合规是独立状态。

写入时先落盘实际状态/控制事实，再处理渲染和派生，避免图像异常抹掉已发生的执行。
使用独占目录、追加日志、原子分块发布和提交边界；刷盘策略（含 fsync cadence）及可能丢失的尾部范围显式声明。
崩溃恢复只校验/整理已落盘事实，不重启同一 episode 的物理执行、不补造缺失尾帧；未封存包标 incomplete。
`checksums.json` 在封存时覆盖整个原包；新派生包有自己的摘要和状态。
schema 使用独立语义版本：兼容新增字段为 minor，改变既有单位/布局/定义为 major，文字澄清为 patch。
读取器不能丢弃未识别的原始字段；不支持的 major/profile 必须显式拒绝，不能按 20D/22D 长度猜语义。

### 7.1 最小边界与区间示例

下例仅演示引用和时序，是**合成片段，不是真实采集证据，也不是完整可验收 episode**。
引用路径相对 episode 根目录，带行索引的引用必须绑定被引用文件摘要。

```json
{
  "frame_id": 0,
  "timestamp": 0.0,
  "clock_id": "sim",
  "control_hz": 20.0,
  "physics_dt": 0.004,
  "physics_tick_id": 0,
  "raw_state_ref": {"path": "raw/state-index.jsonl", "row": 0},
  "robot_state_ref": {"path": "canonical/robot-state.jsonl", "row": 0},
  "observation_refs": {
    "head_camera": {"path": "raw/observations.jsonl", "row": 0}
  },
  "alignment": {"method": "exact", "offset_s": 0.0, "uncertainty_s": 0.0, "tolerance_s": 0.0, "alignment_valid": true}
}
```

```json
{
  "step_id": 0,
  "frame_before_id": 0,
  "frame_after_id": 1,
  "clock_id": "sim",
  "t_start": 0.0,
  "t_end": 0.048,
  "requested_dt": 0.05,
  "actual_dt": 0.048,
  "physics_steps": 12,
  "completion": "complete",
  "state_before_ref": {"path": "raw/state-index.jsonl", "row": 0},
  "state_after_ref": {"path": "raw/state-index.jsonl", "row": 1},
  "command_raw_ref": {"path": "raw/commands.jsonl", "row": 0},
  "command_submitted_ref": {"path": "raw/commands.jsonl", "row": 1},
  "applied_control_refs": [{"path": "raw/controls.jsonl", "row": 0}],
  "measured_motion": {
    "base_delta_world_xy_yaw": [0.009, 0.0, 0.001],
    "base_delta_body_xy_yaw": [0.009, 0.0, 0.001],
    "body_reference": "frame_0_base",
    "units": ["m", "m", "rad"]
  }
}
```

第二个区间可为 `[0.048,0.100]`、13 ticks；partial 区间按已执行 tick 写实际终点。
完整记录还需逐相机对齐、全部组响应、控制描述、capabilities 及文件摘要，不能直接拿上面片段通过验收。

## 8. Adapter 可变，原始协议不随模型变

每个 Adapter MUST 声明输入字段、相机选择、状态/动作布局、监督来源、单位/坐标转换、归一化统计版本、采样频率、
图像变换、action chunk、padding mask 和缺失数据处理。统计只用指定训练 split，不从评测集估计。
派生产物绑定原始 episode 摘要、Adapter code/version/config 和逐样本输入引用；转换报告列出使用/忽略/不可转换字段。
下游模型可忽略字段，但 canonical 包不能为它删除字段。

- MolmoBot：22D state、native action、fisheye 图为 derived。用于驱动仿真的真实 native ctrl 仍属于 raw，不能因为模型名字而删掉。
- π0：joint/EE action、单/双臂、absolute/delta、gripper 语义和相机配置取决于具体 checkpoint/Adapter；本协议不预设统一“π0 action 维度”。
- position gripper 与 motor gripper 不能无标定转换；缺失必需信号时 Adapter 报 unsupported，而不是近似凑出张量。
- expert 和 observer 数据默认不作为策略输入；启用需显式配置，避免特权信息泄漏。
- Adapter 重采样、插值和裁剪不改变 raw；丢帧、末尾 chunk padding 均附 mask，不伪造连续完整动作。

## 9. 实施前差距快照及兼容迁移

以下表格保留此次实施前的静态检查快照，**不是当前能力表**。当前新增的采集/审计/Adapter 及未验证边界见 [使用说明](raw-first-implementation.md)。

| 需求 | 当前源码提供的内容 | 新协议待补齐 |
|---|---|---|
| 状态 | `Simulation.observe_state()` 提供全 qpos/qvel/ctrl、机器人组与实例位姿 | 统一索引、joint/qvel/actuator 映射、velocity 分组、raw 二进制异常值保留 |
| 时序 | command 保存 `time_before_s/time_after_s`；观察有 step/time；实际步进按余数调度 | 三层 tick、逐帧频率/clock、显式前后引用、partial 区间与对齐容差 |
| 指令与控制 | trajectory 有 raw_action、submitted_action、targets、ctrl；native 提供 adjustments | 原始命令来源独立、控制生效区间、结构化 executed_action 与实测响应 |
| RGB | recorder 可保存 PNG；no-edit 受 record_rgb/record_png 配置控制 | 新采集强制保留所有启用相机 raw；不能以仅 MP4 宣称无损 raw 完整 |
| 第三人称 | no-edit 可保存 raw observer PNG；视频可经过 AnalysisVideo 拼接 | raw observer 与 derived 分析流拆名；逐图实际 projection/extrinsics |
| Depth/标定 | 当前通用 `Simulation.render()` 输出 RGB；局部工具有部分 camera metadata | depth 能力/开关、单位和 mask；完整静态标定与逐帧外参 |
| 格式适配 | `export_rollout()` 复制现有 rollout/S0 证据，sample 是 no-edit-dataset-v2 | canonical 包与独立版本化 VLA Adapters，不把复制包称为新协议合规 |
| 结果/版本 | result、contact metrics、配置/场景摘要等已有基础 | 按新字段统一映射，独立 protocol/recording validity 和实际源码身份 |

原定实施顺序（MuJoCo 实现已落地；Adapter 本次只实现 MolmoBot，不实现 π0）：

1. **P0 — 锁定记录事实：** recorder/runtime 增加 frame/step/tick、raw 全状态映射、全部启用相机无损 RGB、实际控制区间；明确拒绝/partial/异常记录。
2. **P0 — 统一解释：** 加入 calibration、逐图相机 pose、单位/坐标定义及 structured executed_action；保持 20D controller 不变。
3. **P1 — 补充传感器：** 启用可用 depth，保存原值、mask、near/far 与 RGB-depth 标定；可用未采集必须公开说明。
4. **P1 — 导出与审计：** 新 schema validator、immutable episode 包、全 outcome 索引与分开的训练筛选索引。
5. **P2 — 模型派生：** 独立 MolmoBot/π0 Adapter 与各自 smoke，缺必要输入则 unsupported。

采集主索引包含成功、失败、未执行、异常与不完整；下游训练索引才按可追溯规则筛选。
当前 `dataset_index.jsonl` 为成功/资格筛选视图，不修改其既有语义；新建全 outcome 索引并保留旧视图。
既有 run、attempt、sample 和摘要不重写。历史导入生成新包，保留源版本/摘要，记录 missing fields 与转换来源。
无法恢复的原始 RGB、depth、逐帧标定或命令不能补造；历史包标 `legacy_partial`，不能改版本号冒充合规新采集。
新实施使用新 run-id；不跨代码/冻结配置版本强行 resume。

## 10. 协议验收：采集完成之前必须检查

以下是目标验收规则。独立审计 CLI 已删除；核心内部仍保留现有数据自洽检查，待步骤三简化。
真实相机投影精度、异步实机时钟、动态参数流和物理任务成功需分别验收，不能仅因文件审计通过而宣称完成。

- **身份/完整性：** ID 唯一，路径局限 episode 根目录、引用可解析、文件摘要一致；声明 schema/profile/capabilities，无覆盖原始证据。
- **数值/映射：** nq/nv/nu 与模型匹配；单位、joint 顺序和 qpos/qvel 地址一致；有效四元数和旋转矩阵检查；异常值保留且 validity 明确。
- **时序/因果：** 流内单调时间；N 区间/N+1 状态；control 生效覆盖与 tick/实际时长一致；拒绝无虚假 action；partial 不补齐 nominal 时长。
- **观测：** 启用 raw 流的样本、分辨率和编码可解码；缺失/关闭/失败显式报告；对齐偏差不超冻结阈值，策略输入无未来数据。
- **标定：** 每个图像引用有效标定/pose；已知世界点投影与实际图像核对，容差由采集 profile 冻结；跟随第三人称不能复用初态外参。
- **Action：** mode/quantity/frame/unit 可解释，command_raw→submitted→applied 可追溯；position/native gripper 不混用，运动反馈与命令差异均保留。
- **结果：** 不以规划成功/执行结束推导任务成功；contact quality 与 success 分离；无执行/异常的未知判定保持 null。
- **Adapter：** 输入摘要和转换可复现；只测试可逆步骤的 round-trip，不宣称 resize/warp 无损；raw 摘要在派生前后不变。

至少覆盖这些故障回归：12/13 ticks 时钟、周期中间停止、相机渲染失败、丢帧与异步超容差、动态相机外参、
native motor 限位调整、请求被拒绝、不同 control profile 禁止混放、非有限终态、depth 无效值、规划未执行、导出重复与源摘要错配。
单元 fixture 仅证明序列化/校验逻辑；真实采集 smoke 另核对物理时间、图像、投影、控制与任务证据。

## 11. 当前代码与文档入口

- [现行 v1 数据格式与 20D 动作](data-format.md)、[无编辑采集及 v2 sample](no-edit-collection.md)、[MolmoBot 评测](molmobot-station-evaluation.md)。
- [AttemptRecorder](../src/lastmile_dataflow/recording/recorder.py)：trajectory、PNG、视频与封存摘要。
- [Simulation](../src/lastmile_dataflow/runtime/simulation.py)：实际时钟、完整状态与渲染。
- [RBY1Adapter](../src/lastmile_dataflow/robots/rby1.py)、[native motor bridge](../src/lastmile_dataflow/robots/molmobot.py)：动作提交与执行器控制语义。
- [no-edit execution](../src/lastmile_dataflow/runtime/no_edit_execution.py)、[第三人称相机](../src/lastmile_dataflow/recording/third_person.py)、[分析视频](../src/lastmile_dataflow/recording/analysis_video.py)。
- [v2 rollout exporter](../src/lastmile_dataflow/exporting/no_edit_layout.py)、[现有布局回归](../tests/test_no_edit_layout.py)：旧包复制与兼容边界。
