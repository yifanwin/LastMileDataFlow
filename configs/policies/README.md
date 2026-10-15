# MolmoBot 模型服务配置

由 `policies/molmobot_config.py` 的 `load_policy_config` 加载；推理实现随本仓库安装，不配置外部源码目录。

## 重点手动配置

- **必须核对** `data_root`、`checkpoint`、`tokenizer_path` 和 `python`。模型权重与 tokenizer 分开准备，不能只创建空目录。
- `python` 指向**当前 worktree** 的 `.venv/bin/python`，不借相邻工程环境。
- 按实验设置 `task_horizon`；夹爪通常保持 `model_default`。GPU 通过 CLI `--gpu` 显式选择，不写在此 JSON。

| 字段 | 含义 |
|---|---|
| `data_root` | 数据根目录，相对仓库根目录或绝对路径 |
| `checkpoint` | 含 config.yaml / model.pt 的本地 RBY1Multitask 权重目录，相对 data_root |
| `tokenizer_path` | 匹配的本地 Qwen3 tokenizer 目录，相对 data_root |
| `python` | 策略子进程解释器，相对仓库根目录或绝对路径 |
| `gripper_mode` | `model_default` 保留模型按任务选择：pick 连续、open 二值；`binary` / `continuous` 显式覆盖 |
| `task_horizon` | 每任务策略循环步数上限，当前 400；不是物理步数或秒数 |

可选字段（当前 JSON 未填）：`load_timeout_s=900` 为模型服务启动加载等待；`rpc_timeout_s=30` 为默认单次 RPC 等待；`service_timeout_s=300` 当前仅在配置类中声明/校验，未接入服务调用，不应靠它调整运行时限。episode 总执行预算使用 execution 的 `attempt_timeout_s`，不能与加载时限混淆。

相机/控制需匹配 native 策略 profile；不能仅替换权重后复用 position 标定。安装和数据准备见 [setup](../../docs/setup.md)。
