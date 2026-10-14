# 策略

- `molmobot.py`：现有仿真进程的模型客户端。
- `molmobot_service.py`：独立 GPU 进程，服务 pick/open 请求。
- `molmobot_config.py`：本地权重、tokenizer、解释器和执行预算。
- `multitask/`：RBY1Multitask 最小推理代码：图像/文本/状态预处理、LLM、视觉连接器、action expert、flow matching 与动作缓冲。

不导入外部 MolmoBot 包，不包含训练、分布式并行、远端 checkpoint 或通用评测框架。策略返回 20D 动作，仿真仍由 runtime 控制。
