# MolmoBot 最小推理迁移与单环境实施

**方案 B 已落实：推理代码随仓库安装，不再读取外部 MolmoBot 源码。最小策略依赖与现有 cuRobo V2 未发现严重冲突，统一使用 `.venv`。**

## 做了什么

1. 从实际 multitask 调用链迁入 `policies/multitask/`，保留 LLM、SigLIP 视觉、连接器、action expert、flow matching 和 checkpoint 参数名。
2. 提取本地 config.yaml / model.pt 严格加载；只保留内存 RGB、文本与 state 的推理处理。不迁入训练、分布式并行、云 IO、数据集加载、视频解码与完整评测工程。
3. 保留三相机顺序、head fisheye、22D state、16×20 动作块、8 步动作缓冲，以及 open 的首次 conditioning image / 点提示。
4. 现有 service/client 改用仓库内推理实现；删除 `source_dir`、运行时源码路径注入和从外部评测文件读取默认值的逻辑。
5. 策略配置加入本地 `tokenizer_path`，默认解释器改为 `.venv/bin/python`。修复实际服务测试发现的问题：路径处理不能解引用虚拟环境 Python 的符号链接，否则会误用基础解释器。

## 为什么可以统一环境

当前组合是 Torch 2.7.1、torchvision 0.22.1、transformers 5.4.0、MuJoCo 3.5.0、cuRobo V2 0.8.0。策略不再引入上游完整评测环境，也就不带入其 MuJoCo 版本要求。

统一环境的依赖检查和 cuRobo / multitask 联合导入通过。不需要为了进程隔离再维护一套 Python 环境：模型服务仍在独立进程运行，解释器来自同一个 `.venv`。

- 安装脚本默认完整安装；base / planning / policy 只是同一环境的依赖子集。
- 主环境未安装 molmobot、TorchMetrics、OmegaConf、datasets、boto3、decord、ipykernel 或 MolmoSpaces。
- 本轮创建的参考及最小策略环境在验证结束后清理，不作为运行依赖。

```bash
scripts/install.sh          # 默认：research + planning + policy
scripts/install.sh base     # 数据处理；同一个 .venv
scripts/install.sh planning # 同一个 .venv，补规划依赖
scripts/install.sh policy   # 同一个 .venv，补策略依赖
```

以后接入其他 VLA 如果确有严重依赖冲突，再拆成 `.venv` 与 `.venv-policy`；当前不提前维护双环境。

## 验证结果

| 验证 | 实际结果 |
|---|---|
| 迁移前后真实权重对比 | seed=17，同一合成 RGB/state；token、position、图像、pooling、归一化 state 完全一致 |
| 真实 action chunk 对比 | 两端均为 16×20，有限值；最大绝对差 **0** |
| 干净策略依赖环境的服务测试 | 真实加载并返回 pick/open 动作，缓冲下一步复用；pick 连续夹爪，open 二值夹爪及点提示通过 |
| 统一环境全套单元回归 | **180 项，179 通过，1 项可选真实渲染跳过** |
| 统一环境依赖与联合导入 | pip check、cuRobo V2 和 multitask 导入通过 |
| 统一 `.venv` 真实服务测试 | pick/open 真实权重推理与缓冲通过；CUDA 6，加载约 189 秒，峰值显存约 9.43 GiB |

真实对比先选当时空闲显存最多的 CUDA 3；服务测试重新查看利用率后选空闲 CUDA 6。不把卡号写成默认值。

日志在 `outputs/logs/multitask-*.log`；固定输入对比在 `outputs/diagnostics/multitask-parity/`。最终统一环境结果在 `outputs/diagnostics/multitask-single-env-service/`。服务 smoke 保存配置、seed、Git commit、结果和动作；不生成来源追踪或发布审计产物。

## 范围与运行方式

本轮使用已有本地权重和 tokenizer，没有下载大型模型。前置数据说明与显式模型 smoke 命令已更新到 [setup](../docs/setup.md)。

**这些测试证明模型推理及服务调用，不证明真实场景抓取、开门成功，也不证明完整 rollout 主链。** 本轮未执行机器人任务、cuRobo 真实场景规划或 benchmark。改动未提交，原有工作树改动保留。
