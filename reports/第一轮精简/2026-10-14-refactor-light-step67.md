# 步骤六、七执行记录

> 后续更新：multitask 最小推理已迁入，环境统一为 `.venv`。最新验证见 [迁移与单环境记录](2026-10-14-molmobot-multitask-implementation.md)；下文保留本阶段当时的执行状态。

2026-10-14，工作树：`refactor-light`。

**步骤六完成；步骤七的路径、数据说明、基础/V2 独立环境已验证。MolmoBot 模型运行验证未完成，按用户选择先交付。** 不把安装脚本或模型导入描述为真实推理通过。

## 本轮改动

- CLI 收敛为 `build-scenes / evaluate-stations / collect-lastmile / build-bench / evaluate-bench`，没有旧别名；删除 Shell 包装与 DATAFLOW_PYTHON。
- build-scenes 内部依次发现、资格检查、构建，临时基线自动清理；Python API 仍可独立调用。
- 新增统一 `paths.py`：配置路径相对 JSON；CLI 路径相对 cwd；支持 data_root 和 LASTMILE_DATA_ROOT；保存的场景/run 引用相对所属目录。
- 逐项检查已提交示例后移除兄弟工作树/NAS 默认路径及旧快照默认引用，不改已有实验数据；原始示例仍在 Git 基线中。
- 新增 `scripts/install.sh` 与 `docs/setup.md`，创建本仓库环境，不借其他工程 site-packages。必要模型/资产获取来源、授权及未验证输入已披露。
- 基础固定 MuJoCo 3.5.0；V2 固定官方 v0.8.0 / Torch 2.7.1。策略环境与基础环境隔离。

## 已验证

本工作树 `.venv` 重新安装公开发行包；部分依赖使用下载缓存并以 copy 模式安装，不引用相邻环境。安装入口在仓库外 cwd、不设置 PYTHONPATH 时也可启动，实际导入当前工作树源码。

软件回归 **176 项：175 通过，1 项真实渲染跳过，7.207 秒**。新增配置根路径、移动简化场景/初态重载、安装入口及构建编排回归。
日志：`outputs/logs/refactor-step67-tests.log`。语法检查、当前示例配置读取、installer 语法、基础 .venv 的 pip check 与 diff --check 通过。

按本轮实际 GPU 查询选择物理 CUDA 6：查询时利用率 0%，已用 18,439 MiB，余量约 30 GiB；它是 0% 利用率卡中余量最多的一张。没有新增自动择卡/队列代码。
该卡通过 **V2 API 导入、8 元素 Torch CUDA 运算、Warp 设备分配与读回**。环境为 Torch 2.7.1+cu126、cuRobo 0.8.0、Warp 1.18.0；这不是完整 V2 规划或机器人任务验证。
日志：`outputs/logs/refactor-step67-cuda-smoke.log`。

## 策略环境尚未达到模型可运行

已创建 `.venv-policy` 并在忽略的 data 目录获取官方 MolmoBot 源码，没有下载模型权重。
完整 eval extra 固定 MuJoCo 3.4.0，与本仓库 3.5.0 冲突。当前桥接使用 isolated RBY1 multitask 模块，因此安装方案不引入完整 eval/MolmoSpaces，保持两个环境独立。

上游基础包仍声明 notebook、视频、datasets、云存储等依赖，解析约 153 个包。部分发行包下载曾持续无进展，用户选择先交付。停止请求发出前，安装进程已自然完成，上游基础包共约 153 项安装成功。

随后只做了导入检查：isolated policy / wrapper 顶层导入通过且未导入 MolmoSpaces；实际 checkpoint / model-config 惰性导入仍报缺少 torchmetrics，policy 环境的 pip check 还提示 decord 平台不支持。没有继续下载依赖或加载模型权重。
**上游基础包安装完成，不等于当前 `.venv-policy` 已能加载真实模型。** 日志：`outputs/logs/refactor-step67-policy-install.log`。后续应先调研真正的 multitask 推理依赖，再决定是否迁移推理代码，避免继续装整仓依赖。

## 边界

公开资产是否包含当前专用机器人 XML、holobase URDF/YAML 和匹配的 metadata 尚未验证，setup 中明确说明；没有虚构一键复现命令。
MJB 仍要求保存时的 MuJoCo 版本。简化场景移动测试不证明真实增强 XML 独立打包或跨版本恢复。
步骤八未执行：没有真实 rollout、VLA 权重加载/推理或困难 benchmark 验收。AGENTS.md 按所有者维护约定未改；未提交 Git或合并分支。
