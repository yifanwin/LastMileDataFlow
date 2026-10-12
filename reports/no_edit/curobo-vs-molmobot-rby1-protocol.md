# cuRobo 后端 vs MolmoBot 后端：RBY-1 控制协议与相机协议差异

本文只比较两个 `operation_backend` 在 **RBY-1 控制语义**与**相机/图像语义**上的差别，
依据是当前工作树代码、`configs/`、以及被引用的 MolmoBot upstream 与 molmo_spaces 相机配置。
不评价任务成功率。

- 代码入口：`workflows/no_edit.py` 按 `operation_backend` 分派
  （`curobo` → `runtime/no_edit_execution.py`；`molmobot` → `runtime/molmobot_execution.py`）。
- 机器人适配：`robots/rby1.py`，其中 `apply()` 在 `control_profile=='molmobot-native-v1'`
  时转发到 `robots/molmobot.py: apply_native()`。

## 0. 一句话结论

| | curobo 后端 | molmobot 后端 |
|---|---|---|
| 控制画像 | `position-v1`，20 Hz，夹爪位置伺服（米） | `molmobot-native-v1`，10 Hz，夹爪原生 motor（−100…+100） |
| 相机画像 | 45° 透视，320×240，纯 pinhole | head 139° / wrist 58°，1024×576，head 图在模型进程里做一次**软件鱼眼**后处理 |

两个后端共用同一份 20D 索引布局，但**索引 10/18 的物理含义完全不同**，动作不可跨后端重放。

## 1. 控制协议差异

### 1.1 时序

| 项 | curobo | molmobot |
|---|---|---|
| `control_profile` | `position-v1` | `molmobot-native-v1`（CLI 在 molmobot 下自动改写） |
| `control_hz` | 20 Hz | 10 Hz |
| `physics_dt` | 0.004 s | 0.004 s |
| 单次动作物理步 | `step()`：`_clock_remainder` 在 12/13 物理步间交替，长期平均 20 Hz | `step_policy()`：一次设定目标后 5 控制 tick × 5 物理步 = 25 步，`timing_ms={100,20,4}` |
| 动作来源 | cuRobo 规划器逐 waypoint | 策略一次输出 16 步 chunk，执行 8 步后重新推理 |

### 1.2 夹爪（最大的语义分叉）

- **curobo**：`prepare_robot_spec()` 把夹爪 actuator 从力控 motor **改写为位置伺服**
  （`gaintype=FIXED`、`biastype=AFFINE`、`kp=4000`、`kv=400`、`ctrlrange=gripper_limits(-0.05,0)`）。
  20D 的 `[10]` / `[18]` 是**米制绝对张合位置**。
- **molmobot**：`prepare_robot_spec()` 走 native 分支，**保留原生 motor**
  （要求 `biastype=BIAS_NONE` 且 `ctrlrange==[-100,100]`，否则直接报错）。
  20D 的 `[10]` / `[18]` 是**原生 motor 控制量**：−100 张开、+100 闭合、0 不主动闭合。
  `molmobot.py` 也会对这两个值做 ctrlrange clip 并记入 `adjustments`。
- 两指是 `equality` 从动（`gripper_finger_*2 = -gripper_finger_*1`），所以每侧只有一个执行器通道。

> 因为语义不同，`docs/data-format.md` 明确规定：不得按旧位置协议重放 native 动作，
> 重放必须使用冻结的 control profile。

### 1.3 底座与双臂

- **curobo**：底盘/双臂都是「相对**下发时刻实测**的增量」；移动底盘由 cuRobo V2 联合规划
  （base x/y/theta + 单臂 7 关节 + torso，`rby1m_mobile_torso.py: joint_names`）。
  `sanitize_action()` 把目标投影到关节∩执行器∩协议限位交集（`robots/action_limits.py`）。
  `fixed_base=True` 时底盘锁定在首次进入位置。
- **molmobot**：`operation_base_mode=policy_relative`，**不调用任何规划器**
  （`workflows/no_edit.py` 下 molmobot 分支 `candidates=[]`）。
  模型直接输出底盘 3 维相对增量与双臂 7 维相对增量。

### 1.4 躯干与头部

| | curobo | molmobot |
|---|---|---|
| 躯干语义 | h → `[0,h,-2h,h,0,0]`，V2 用 `bounded_torso_command` + 反馈闭环 | 同样 `torso_joints(h)` 展开，但只是 `clip(h, torso_limits)` |
| 躯干误差 | `torso_error_policy=record_only`，严重超限才 stop | 同，只记录 |
| 头部 | `fixed_head` 锁定；规划器可加 **head FOV 硬约束** | `targets['head']=robot.fixed_head`，head 不进入策略动作 |

### 1.5 状态输入

- **curobo**：无策略状态输入（规划器用 sim 实测 qpos）。
- **molmobot**：模型需要 22D state = base3 + left7 + grip1 + right7 + grip1 + torso3，
  torso 取实测 6 关节的 `[1,2,3]`（`state_indices`，源自 upstream `MolmoBotRBY1DoorPlusOpenPolicyConfig`）。
  `molmobot_service.py: TraceAgent` 强制断言 state 形状为 `(22,)`。

### 1.6 安全界（两者共用）

`max_base_delta=0.10 m`、`max_yaw_delta=0.20 rad`、`max_arm_delta=0.20 rad`；
关节/执行器限位在写 `data.ctrl` 前统一校验，**原子写入**，不静默裁剪
（native 路径下 clip 会显式写入 `adjustments` 证据）。

## 2. 相机协议差异

### 2.1 相机与视场

| 项 | curobo | molmobot |
|---|---|---|
| 相机集合 | `head_camera` + `wrist_camera_l` + `wrist_camera_r`（+可选第三人称分析相机） | 同 |
| `fovy`（head / wrist） | XML 默认 **45° / 45°**，`prepare_robot_spec` 不改写 | **head=139°、wrist=58°**（`robots/rby1.py:46-47`，对应 nominal `RBY1GoProD455CameraSystem`） |
| 离屏渲染尺寸 | 320×240（默认配置） | 1024×576（**强制**，CLI 与 `NoEditConfig` 双重校验） |
| offscreen buffer | 默认 | 提升到 ≥1280×720（`runtime/simulation.py:89-91`） |
| 投影类型 | MuJoCo pinhole（`cam_projection=0`） | 仍是 MuJoCo pinhole；**鱼眼是后处理畸变，不是 MuJoCo 投影** |

### 2.2 鱼眼（molmobot 独有）

molmobot 后端下，「机器人原视频」是**鱼眼前**的原始 RGB；模型输入才做鱼眼：

- 位置：`MolmoBotRBY1MultitaskPolicy._populate_action_buffer()` →
  `olmo/data/image_warping_utils.apply_fisheye_warping`。
- 流程：4:3 中心裁剪（1024×576 → 768×576）→ k1..k4 桶形畸变 → 30% 边缘裁剪 → ~308×232，
  再交给 checkpoint 预处理器。
- 畸变参数 `k1..k4 = 0.051 / 0.144 / 0.015 / −0.018`，且每次 forward 有
  `get_randomized_distortion_parameters(±0.001)` 的微扰，按 attempt seed 管理。
- 点提示坐标：`warp_point_coordinates()` 用同一套管线把首帧点同步变形，再转成 Molmo html-v2
  的 0–1000 整数坐标（`_format_point_prompt`）。
- 本工程**不额外做外参抖动**：`molmobot_execution.py` 记录
  `extrinsic_jitter=False`、`fisheye='upstream image_warping_utils, once in model process'`。
  upstream camera system 里定义的 fov/pos/orientation noise 未被启用（名义 rig）。

### 2.3 相机用途与顺序

| | curobo | molmobot |
|---|---|---|
| 用途 | 记录 PNG/视频；head FOV 作为 cuRobo **几何硬约束**（只用 head_camera 的 fovy 推 `tan_half_x/y`） | 策略观测输入 + 记录；记录的顺序是 `wrist_camera_r, head_camera, wrist_camera_l` |
| 输入顺序 | `render()` 按 `robot.camera_names` 遍历 | 模型固定按 `wrist_camera_r / head_camera / wrist_camera_l` |
| FOV 约束来源 | `planning/head_fov.py: camera_definition()`：要求 `cam_mode=FIXED`、`cam_projection=0`（透视），从 `cam_fovy` 与图像宽高比推 `tan` | 不走 head FOV 约束（无规划器）；139° 只影响渲染，随后被软件鱼眼覆盖 |

> 注意：`head_fov.py` 明确要求「现有透视 fovy 相机」。molmobot 把 head fovy 改成 139°
> 并做离屏 1024×576，若将来在 molmobot 后端上启用 cuRobo head FOV 约束，
> 139° 的 `tan_half_y` 会让约束形同虚设——目前后端分离，二者不会相遇。

## 3. 证据落点（attempt 产物）

| 证据 | curobo | molmobot |
|---|---|---|
| 相机元数据 | 无独立文件；第三人称 `third_person_*.json` | `camera_profile.json`（分辨率、相机顺序、每台 `fovy_deg`、`extrinsic_jitter`、`fisheye` 说明） |
| 模型输入 | 不适用 | `model_input_*.png`、`policy_requests.jsonl`（校验前预测）、`policy_actions.jsonl`（已实际 stepping） |
| 规划 | `planning.json`、`v080-*/`、`head_fov_*` | 无 |
| 动作 | `trajectory.jsonl`、`physics.jsonl.gz` | 同 + `policy_*` |

## 4. 核对范围与限制

- 本文结论来自源码与配置静态核对，**未新跑真实模型或 GPU 采样**。
- upstream 的鱼眼管线实现位于相邻只读工程 `MolmoBot/.worktrees/upstream-main/MolmoBot`，
  本工作树不修改它；其行为变化需要重新核对而非沿用本文。
- 尚未核对：molmobot 后端下 `fovy=139°` 在 MuJoCo 离屏渲染时的实际畸变/边缘表现
  （属于上游预处理已覆盖的范围），以及两后端相机外参在真实资产上的数值差。
