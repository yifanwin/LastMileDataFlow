# `configs/robots/` 配置说明

机器人本体配置。加载入口 `lastmile_dataflow.config.load_config(RobotConfig, path)`，
字段定义见 `src/lastmile_dataflow/config.py` 的 `RobotConfig`。未知字段直接报错；
`model_path`、`output_dir` 这类路径字段以**本 JSON 所在目录**为基准解析。

## `rby1.json`

只显式声明模型路径与时序，其余控制参数与初态走 `RobotConfig` 默认值，运行时完整冻结到
attempt 的 `config.json`（SHA-256 标识）。

| 字段 | 当前值 | 含义 |
|---|---|---|
| `model_path` | `../../../molmospaces_data/assets/robots/rby1m/rby1_v1.2_site_control.xml` | 机器人 MJCF。相对本文件解析；这是本机示例路径，换机器必须显式改。 |
| `control_hz` | `20` | 控制频率（Hz）。动作按余数累积在 12/13 个物理步间切换，长期平均为该值。 |
| `physics_dt` | `0.004` | MuJoCo 物理步长（s）。约束 `control_hz * physics_dt <= 1`。 |
| `cameras` | `["head_camera","wrist_camera_l","wrist_camera_r"]` | 必需相机别名。缺失直接报错，不静默降级为两相机。 |
| `planning_adapter` | `"joint_waypoints_v1"` | 规划适配器。当前只支持这一个值。 |

## 未在文件中出现、但由 `RobotConfig` 默认值生效的字段

改这些字段会改变物理语义，先读 `config.py` 与 `runtime/action.py`：

| 字段 | 默认 | 含义 |
|---|---|---|
| `name` | `rby1m` | 机器人型号，v1 只支持 `rby1m`。 |
| `namespace` | `robot_0/` | MJCF 里机器人 body/joint 前缀，v1 固定。 |
| `torso_limits` | `(0.0, 0.738)` | 躯干联动参数 h 的合法区间（协议上界）。 |
| `gripper_limits` | `(-0.05, 0.0)` | 位置协议下夹爪绝对位置区间（m）。 |
| `max_base_delta` | `0.10` | 单控制步底盘平移上限（m）。 |
| `max_yaw_delta` | `0.20` | 单控制步底盘 yaw 上限（rad）。 |
| `max_arm_delta` | `0.20` | 单控制步单臂关节增量上限（rad）。 |
| `gripper_kp` / `gripper_kv` | `4000` / `400` | 内存内把夹爪 motor 重写为位置伺服的增益。不写回源 XML。 |
| `gravity_compensation` | `true` | 对机器人 body 打开重力补偿。 |
| `control_profile` | `position-v1` | `position-v1` 为位置伺服；`molmobot-native-v1` 保留原生 motor（由 no-edit molmobot 后端在 CLI 内改写）。 |
| `initial` | 见下 | 全组初态；每次 `initialize()` 必须给全组。 |

`initial` 默认值：

```json
{"base":[0,0,0],"torso":[0],"head":[0,0.6],
 "left_arm":[0.5,0,0,-2.3,0,-0.5,0],"right_arm":[0.5,0,0,-2.3,0,-0.5,0],
 "left_gripper":[-0.05],"right_gripper":[-0.05]}
```

## 约束提醒

- 20D 动作协议：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`。
  底盘/双臂为增量，夹爪/躯干为绝对量。详见 [`docs/data-format.md`](../../docs/data-format.md)。
- 机器人位姿只能在初始化阶段设置；`Simulation.begin()` 后禁止恢复快照或传送。
