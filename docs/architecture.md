# 从 workflow 找到算法和执行

源码收敛为六个领域目录，没有旧目录兼容壳。

| 目录 | 职责与主要入口 |
|---|---|
| `scenes/` | 场景/资产加载、任务发现、采样初始化、障碍物放置与场景物理检查；`dataset.py`、`tasks.py`、`sampling.py`、`augmentation.py` |
| `runtime/` | 实际 stepping、机器人动作、构建事务、任务判定；`simulation.py`、`execution.py`、`curobo_execution.py`、`molmobot_execution.py`、`pick.py`、`outcomes.py` |
| `planning/` | cuRobo V2、碰撞/FOV、A* 和规划结果执行边界 |
| `policies/` | MolmoBot 配置、服务与客户端 |
| `data/` | 单套 recorder、图像/视频、统计与 S0/S1 选择、热图、benchmark 选择、按需模型转换 |
| `workflows/` | 场景构建、采集与 benchmark 的短编排 |

## 四个 workflow

- `scene_datasets`：发现/资格检查，调用 `scenes.sampling.sample_stations`，pick 构建调用增强器，open 保存场景。
- `obstacle_augmentation`：准备资产和基线，调用 `scenes.augmentation.build_variant`。真实放置算法不再塞在 workflow。
- `collection`：`evaluate_stations()` 单独跑逐点 rollout 与热图；`collect_lastmile()` 用匹配标定或在当前 run 标定一次，再执行 S0→S1→操作。两个入口共享 run 准备、场景循环和真实执行器，没有 continuous/calibration_only Python 模式开关。
- `lastmilebench`：由完整标定选择困难 S0，保存 native 场景并独立执行策略。设备/profile 工具来自 `runtime.execution_config`，不依赖采集 workflow。

`sample_stations` 只检查候选点的初始化与可见性；`evaluate_stations` 才执行规划/策略 rollout，两者不混用。
cuRobo 和 VLA 共用 `Simulation`、接触/任务判定及 `data.recorder`；原生 motor 和 position 的实际语义仍由各自适配器处理。

`config.py`、`io.py` 和 `cli.py` 是包根的共用配置、读写和薄入口。CLI 仅五个核心命令，build-scenes 在内部调用发现/资格检查/构建；不保留旧 workflow 文件、旧命令别名或 Shell 包装。
