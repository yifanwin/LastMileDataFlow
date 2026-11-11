# 逐站位运行配置

`scripts/station_map.py` 和 `scripts/collect.py` 的 `--config` 读这里的 JSON，对应 `config.StationRun`。
路径相对仓库根目录，也可写绝对路径。省略的字段取下表的默认值。

| 字段 | 默认 | 含义 |
|---|---|---|
| `execution` | 必填 | 执行配置，见 [execution](../execution/README.md) |
| `assets_dir` | `configs/local.json` 的 `assets_dir` | 资产目录 |
| `max_trials` | 不限 | 每场景标定预算；不完整的标定不能用于正式配对 |
| `record_video` | `false` | 额外记录视频；RGB 和数值轨迹按运行模式保存 |
| `recording_mode` | 有视频时 `visual`，否则 `numerical` | Panda 标定默认只存数值 |
| `station_workers` | 1 | cuRobo 站位进程数 |
| `planning_workers_per_gpu` | `[1]` | 每张卡的规划 worker 数（1–4），一个值或每卡一个值 |
| `cpu_workers` | 60 | 独立 trial 执行容量 |
| `preparation_workers` | 12 | 站位准备并发 |

`curobo_v2.json` 是 RBY-1，`panda_omron_robocasa_curobo.json` 是 Panda RoboCasa。要改记录或并行就复制一份新文件，
不要改动已有 run 用过的文件。

## GPU 与渲染

`--gpu` 使用物理 GPU 编号。入口自动查询 CUDA 与 EGL 的对应关系，准备、执行和规划子进程继承同一映射。
CPU 准备和执行进程的 CUDA 可见列表为空，但仍在首张所选 GPU 上渲染；规划进程在各自 GPU 上计算和渲染。

```bash
PYTHONPATH=src /data1/wenyifan/lmdf/LastMileDataFlow/.venv/bin/python scripts/station_map.py \
  --dataset <场景库> --config <运行配置> --output <新目录> --gpu 0
```

需要手动覆盖时，`--egl-devices` 与 `--gpu` 一一对应，填 EGL 枚举编号，不是物理 GPU 编号。
本机 2026-11-11 查询及最小出图验证：GPU 0 对应 EGL 7，可用 `--gpu 0 --egl-devices 7`。
`build_scenes.py`、`collect.py` 和 `bench_evaluate.py` 同样支持该覆盖；通常不需要手动设置。
首次渲染前完成设备绑定，运行中不要切换已经建立的 EGL 上下文。
