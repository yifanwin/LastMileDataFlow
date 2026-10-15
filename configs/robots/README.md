# 机器人配置

**重点手动配置：`data_root`、`model_path`。** 按真实机器人资产位置填写，不改写外部 XML。加载器为包根 `config.py` 的 `RobotConfig` / `load_config`；场景库使用同义的 `load_scene_robot`。

| 字段 | 含义 |
|---|---|
| `data_root` | 数据根目录，相对仓库根目录或绝对路径 |
| `model_path` | MuJoCo 机器人 XML，相对 data_root |
| `control_hz` | 名义控制频率，当前 20 Hz，即 50 ms 控制周期 |
| `physics_dt` | 物理积分步长，当前 0.004 秒，即 250 Hz |
| `cameras` | 采集的模型相机名；当前头部、左腕、右腕；必须与 XML 名称匹配 |
| `planning_adapter` | 规划适配器；当前仅 `joint_waypoints_v1`，一般不改 |
| `control_profile` | `position-v1` 或 `molmobot-native-v1`；省略默认 position-v1；不单靠改字符串就能复用旧标定 |

`rby1.json` 显式设置规划适配器，省略控制 profile；[scene_datasets/rby1_position_head320.json](../scene_datasets/rby1_position_head320.json) 显式设置 position profile，规划适配器取相同默认值。两个文件的当前物理配置相同。

执行流程会从场景快照读取机器人配置，修改此文件不会追溯修改已有快照。控制实现位于 `runtime/control/`。position 与 native motor 动作语义不同；改变控制模式、步长、相机或分辨率后，重新生成匹配的场景/标定。相机分辨率不在本 JSON 中设置，见 [场景配方](../scene_datasets/README.md) 和 [执行配置](../execution/README.md)。
