# 策略

- `config.py`：后端表 `BACKENDS`（molmobot、pi05）、`load_policy_config`、`create_policy`、`check_profile`。
- `process.py`：通用策略子进程 RPC（`PolicyProcess`：启动、带认证的本地连接、超时与错误分类）和统一的单步返回 `PolicyAction`。
- `serving.py`：两个服务进程共用的骨架 `serve()`（父进程退出时自杀、监听、加载、逐条应答、错误回包）。
- `molmobot.py`：MolmoBot 配置与客户端；`molmobot_service.py`：独立 GPU 进程，服务 pick/open 请求。
- `multitask/`：RBY1Multitask 最小推理代码：图像/文本/状态预处理、LLM、视觉连接器、action expert、flow matching 与动作缓冲。
- `pi05/`：π0.5 配置（含相机映射 `PI05_CAMERAS`）、客户端、服务、推理、归一化和 RBY-1 训练数据视图；`core/` 是移植的 JAX 模型代码。
- `task.py`：策略和训练数据共用的语言指令。

不导入外部 MolmoBot 包，不包含训练、分布式并行、远端 checkpoint 或通用评测框架。策略返回 20D 动作（Panda π0.5 为 12D），仿真仍由 runtime 控制。

`configs/policies/pi05_rby1` 的 `norm_stats` 指向 `outputs/training/pi05-rby1-position/lerobot/assets/norm_stats.json`，该文件目前不存在：使用前需先运行 `scripts/export_pi05.py` 重新生成统计。
