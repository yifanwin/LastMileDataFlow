# Native VLA 逐点评测预设

`native_vla_base.json` 当前与 [execution/molmobot.json](../execution/molmobot.json) 内容相同，均由 `load_execution_config` 读取，可传给 `evaluate-stations --config`；不是另一个 schema，也不是机器人配置。

**重点手动配置：**核对 `policy_config` 指向的权重、tokenizer 和解释器；按实验修改 `trials_per_station`、`operations`、`attempt_timeout_s`、`seed`。保持 MolmoBot 的 `operation_base_mode=policy_relative` 和 1024×576 图像要求。CLI 显式传匹配的数据集、资产路径、输出路径、新 run-id 和 GPU。

全部字段含义见 [execution 字段表](../execution/README.md#json-字段)：`operation_backend`、`policy_config`、`operation_base_mode`、`torso_error_policy`、`third_person_enabled`、`radius_m`、`spacing_m`、`trials_per_station`、`operations`、`min_target_pixels`、`map_resolution_m`、`smoothing_sigma_m`、`smoothing_support_distance_m`、`attempt_timeout_s`、`width`、`height`、`seed`。

本文件不是自动继承；修改 execution/molmobot.json 不会自动修改此文件。若无区别需求，直接使用 execution 中的预设即可。
