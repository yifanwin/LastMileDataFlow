# `configs/collection/` 配置说明

采集/执行层配置，被 `run`、`build`、`station-map`、`case-edit` 共用。加载入口
`config.py: load_config(CollectionConfig, path)`，字段见 `CollectionConfig`。
`output_dir` 以本文件所在目录为基准解析。

## `smoke.json`

```json
{"output_dir":"../../outputs","seed":20261002,"max_steps":6,"max_initialization_trials":80,
 "agent_call_budget":0,"width":320,"height":240,"record_video":true,"protocol_version":"lightweight-v1"}
```

| 字段 | 当前值 | 含义 |
|---|---|---|
| `output_dir` | `../../outputs` | 产物根目录。CLI `--output-dir` 可覆盖。 |
| `seed` | `20261002` | 随机种子。CLI `--seed` 可覆盖。 |
| `max_steps` | `6` | 单个 attempt 最多执行的控制步数。CLI `--steps` 可覆盖。 |
| `max_initialization_trials` | `80` | 找不到合法初态时的最大初始化尝试次数。 |
| `agent_call_budget` | `0` | 允许的 Agent 调用次数；阶段一/二默认 0，即不调外部模型。 |
| `width` / `height` | `320` / `240` | 图像/视频分辨率（必须为偶数）。MolmoBot 评测要求 1024×576，由 no-edit 配置强制覆盖。 |
| `record_video` | `true` | 是否编码 MP4。注意：这是**死配置**，评测流水线里成功失败都存视频、无 CLI 开关；该开关主要在阶段一 `--no-video` 生效。 |
| `protocol_version` | `lightweight-v1` | 轻量验收协议版本，当前只支持这一个值。 |

## 未显式写出、走默认值的字段

| 字段 | 默认 | 含义 |
|---|---|---|
| `penetration_ratio` | `0.10` | 初始化严重重叠阈值系数：碰撞几何小包围球半径 × 该系数。 |
| `penetration_floor_m` | `0.010` | 上述阈值的下限（m）。 |
| `penetration_ceiling_m` | `0.030` | 上述阈值的上限（m）。 |
| `feedback_warning_rad` | `0.05` | 头/躯干反馈偏差超此值记告警（不终止）。 |

重叠阈值 `clip(scale × ratio, floor, ceiling)` 是**尺度相关的启发式，尚未逐类别校准**，
只在初始化过滤（`inspect_state(..., initialization=True)`）时生效；运行期不按穿透毫米数终止。
