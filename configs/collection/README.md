# 记录配置

`smoke.json` 是 Python `load_config(CollectionConfig, ...)` 的示例。**当前五个 CLI 命令不直接加载此 JSON**；evaluate-stations / collect-lastmile 在 CLI 中创建 CollectionConfig，记录开关通过命令行传入，输出目录用 `--output-dir`。

## 重点手动配置

- Python 直接加载本文件时，先改 `data_root` 和 `output_dir`。当前 `data_root=data`、`output_dir=../outputs`，解析后为仓库下 outputs，不是 configs/outputs。
- CLI 运行用 `--record-video`、`--record-depth`、`--record-physics` 开启相应记录；不要只改 smoke.json 然后期待 CLI 生效。
- CLI 执行图像尺寸取 [execution](../execution/README.md)；本文件 width/height 不是对执行配置的全局覆盖。

| smoke.json 字段 | 含义 |
|---|---|
| `data_root` | output_dir 的数据根目录，相对仓库根目录 |
| `output_dir` | 输出位置，相对 data_root；具体工作流显式传输出根目录时以工作流参数为准 |
| `seed` | CollectionConfig 种子；当前逐点执行 trial 的种子由 ExecutionConfig.seed 生成 |
| `max_steps` | 通用记录/采集步数配置，当前示例 6；当前 cuRobo/MolmoBot 执行不以它作为 episode 终止预算 |
| `max_initialization_trials` | 初始化候选尝试上限，当前 80；不表示 episode 内可重置次数 |
| `width` / `height` | 通用图像尺寸，320×240；当前执行器绑定相机时取 ExecutionConfig 的尺寸 |
| `record_video` | 是否编码原始相机视频，示例 true |

可选记录字段：`record_images=true` 保存图像、`record_depth=false` 保存深度开关、`record_physics=false` 保存逐物理步日志开关。其余通用参数见包根 `config.py` 的 CollectionConfig，不建议为了增加通过率修改物理判定参数。

真实执行只记录一份 trajectory.npz；图像默认开启。没有 raw-first/canonical 协议开关，也不会因为启用记录而产生未执行的轨迹。
