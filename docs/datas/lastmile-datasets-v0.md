# LastMile 数据

`workflows.collection.evaluate_stations()` 写 `trials.jsonl` 与热图。
`workflows.collection.collect_lastmile()` 读取匹配标定；没有标定时在当前 run 内采集一次，不创建子 run。
完整低/高成功率相邻站位配对后，通过 A* 执行 S0→S1，再用 cuRobo V2 操作，写 `episodes.jsonl`。

两种用途共用真实执行器和记录器。两个旧 workflow 已删除，无兼容壳；`lastmile-datasets` 仍是数据用途名称。
当前 CLI 仍可显式传 `--calibration` 或 `--calibration-only`，命令名在步骤六统一。
详见 [最小数据格式](data-format.md) 和 [代码调用关系](../architecture.md)。
