# MolmoBot 配置

`molmobot.json` 由 `policies.molmobot_config.load_policy_config()` 读取。推理实现随仓库安装，不再配置外部源码。

- `checkpoint`：本地 RBY1Multitask 的 config.yaml / model.pt 目录。
- `tokenizer_path`：对应 Qwen3 tokenizer 的本地目录；与权重分开准备。
- `python`：本仓库的 `.venv/bin/python`。
- `gripper_mode`：model_default 保留 pick 连续夹爪、open 二值夹爪；binary/continuous 显式覆盖。
- horizon 与 timeout：模型循环与加载/RPC 预算。

data_root 必须显式设置；checkpoint/tokenizer_path 相对它，python 相对 repo root 或写绝对路径。实时查看利用率/显存后显式指定 GPU。
安装和数据准备见 [setup](../../docs/setup.md)。
