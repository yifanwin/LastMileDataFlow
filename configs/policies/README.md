# `configs/policies/` 配置说明

VLA 策略（MolmoBot）模型配置。加载入口
`policies/molmobot_config.py: load_policy_config(path)`，字段见同文件 `MolmoBotConfig`。
`source_dir`、`checkpoint`、`python` 以本文件所在目录为基准解析。
被 `no_edit/molmobot.json` 通过 `policy_config` 引用。

## `molmobot.json`

```json
{"source_dir":".../MolmoBot/.worktrees/upstream-main/MolmoBot",
 "checkpoint":".../nas_wenyifan/models/MolmoBot-RBY1Multitask",
 "python":".../MolmoBot/MolmoBot/.venv/bin/python",
 "gripper_mode":"model_default","task_horizon":400,
 "min_free_gpu_mb":24576,"max_gpu_utilization":20,
 "gpu_sample_count":3,"gpu_sample_interval_s":5.0}
```

| 字段 | 当前值 | 含义 |
|---|---|---|
| `source_dir` | MolmoBot upstream 源码目录 | 冻结 upstream commit、源码摘要；运行模型进程时 `PYTHONPATH` 优先指向此处。只读引用，不修改外部工程。 |
| `checkpoint` | `MolmoBot-RBY1Multitask` | 模型权重目录，需含 `config.yaml` 与 `model.pt`，两者 SHA-256 被记录。 |
| `python` | MolmoBot 的 venv python | 模型解释器，需具备所选 upstream 的推理依赖。 |
| `gripper_mode` | `model_default` | `model_default`（pick 连续、open 按阈值 5 二值化为 ±100）或 `binary`/`continuous`（显式覆盖全部任务）。改模式须新 run，不混合分母。 |
| `task_horizon` | `400` | 每次 attempt 的策略动作上限。 |
| `min_free_gpu_mb` | `24576` | 选卡要求的空闲显存下限（MiB）。smoke 实测峰值约 10.2 GiB，仍保守留 24 GiB。 |
| `max_gpu_utilization` | `20` | 选卡要求的 GPU 利用率上限（%）。 |
| `gpu_sample_count` | `3` | 连续采样次数，须每次都满足利用率/显存条件。 |
| `gpu_sample_interval_s` | `5.0` | 采样间隔（s）。采样窗口 = `(count-1) × interval`。 |

未写出、走默认值的字段：`service_timeout_s=300`、`rpc_timeout_s=30`、`load_timeout_s=900`。

`rpc_timeout_s` 是**单次模型往返**的固定超时，与 attempt 的 300 s 墙钟预算相互独立：执行循环不再把
"剩余预算"当作 RPC 超时，否则跑满 task_horizon 的最后一步会拿不到 1 s 的等待时间，被误判成
`model response timeout`（归为 shared 故障、直接中止整个 scene）。预算由调用方在每次应答返回后复查。

## 选卡逻辑

按窗口内**最大利用率升序、最小空闲显存降序**排序选卡；没有满足条件的卡则等待。
GPU 编号仍由 CLI `--gpu-ids` 显式指定，不在代码硬编码。

## 协议要点

- 使用 `rby1-multitask-molmobot-native-v1`：20D 布局不变，但**夹爪是原生 motor 命令（范围
  [-100,100]），不是旧位置协议的米值**。状态输入的夹爪仍为实测位置。
- 三层时序 100/20/4 ms（policy/control/physics），与 `RobotConfig` 的 10 Hz / 0.004 s 对齐
  （由 CLI 在 molmobot 后端下改写 `control_hz=10`、`control_profile=molmobot-native-v1`）。
- 真实渲染 1024×576，输入顺序 right wrist / head / left wrist。详见
  [`docs/molmobot-station-evaluation.md`](../../docs/molmobot-station-evaluation.md) 与
  [`docs/data-format.md`](../../docs/data-format.md)。
