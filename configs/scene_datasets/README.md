# 旧场景库配方

这里保留已有实验使用的 Case JSON，供历史结果查阅。公开 `build-scenes` 已改用 v2 任务模式配方，不能再加载这些文件。新运行请按 [task_modes](../task_modes/README.md) 配置和执行。

`panda_omron_val103_cup30_base_map.json` 不是 Case JSON，而是 `evaluate-stations --recipe` 的 `base-map-scene-v1` 场景文件：源场景 XML 与 metadata、目标 `asset_id`/`object_id`、机器人配置、参考站位、升降与夹爪。机器人 JSON 中的 `model_path` 按本文件的 `data_root` 解析。
