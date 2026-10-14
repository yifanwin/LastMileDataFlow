# LastMile 数据

`lastmile_datasets.collect` 是薄入口，调用 `no_edit.collect_scene_dataset`。
逐点试验写 `trials.jsonl`；完整的低/高成功率相邻站位配对后，通过 A* 执行 S0→S1，再用 cuRobo V2 操作，写 `episodes.jsonl`。
可显式传 `--calibration` 复用相同有效配置的标定，不复制结果。
详见 [最小数据格式](data-format.md)。名称统一属于后续步骤，不再保留旧增强输入 adapter。
