# 执行配置

`curobo_v2.json` 仅 cuRobo V2；`molmobot.json` 指定 native 策略控制及独立服务配置。
配置由 `runtime.execution_config.load_execution_config` 读取为 `ExecutionConfig`，用于两个 collection 入口；不区分原场景/增强场景输入协议。
记录参数仍是包根 `CollectionConfig`，用于图像、视频、深度和日志设置。
