# 逐站位运行配置

`scripts/station_map.py` 和 `scripts/collect.py` 的 `--config` 读这里的 JSON，对应 `config.StationRun`。
路径相对仓库根目录，也可写绝对路径。省略的字段取下表的默认值。

| 字段 | 默认 | 含义 |
|---|---|---|
| `execution` | 必填 | 执行配置，见 [execution](../execution/README.md) |
| `assets_dir` | `configs/local.json` 的 `assets_dir` | 资产目录 |
| `max_trials` | 不限 | 每场景标定预算；不完整的标定不能用于正式配对 |
| `record_video` / `record_depth` / `record_physics` | `false` | 额外记录视频、深度、逐物理步日志 |
| `recording_mode` | 有视频或深度时 `visual`，否则 `numerical` | Panda 标定默认只存数值 |
| `station_workers` | 1 | cuRobo 站位进程数 |
| `planning_workers_per_gpu` | `[1]` | 每张卡的规划 worker 数（1–4），一个值或每卡一个值 |
| `cpu_workers` | 60 | 独立 trial 执行容量 |
| `preparation_workers` | 12 | 站位准备并发 |

`curobo_v2.json` 是 RBY-1，`panda_omron_robocasa_curobo.json` 是 Panda RoboCasa。要改记录或并行就复制一份新文件，
不要改动已有 run 用过的文件。
