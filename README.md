# LastMileDataFlow

面向场景增强、逐点成败图、LastMile 连续数据与困难起点评测的科研代码。
历史五阶段、四 Agent 场景编辑、legacy 导入和专用交付入口已删除；不恢复旧路线。

## 当前重构进度

2026-10-14：执行轻量化步骤一、二。核心算法和共享执行底座保留，历史专用功能删除。
数据协议、内部审计、采集 workflow 合并、目录调整和独立环境尚未重构。
源码入口保留不表示真实 GPU、模型或完整采集已经验证。

- [实施方案](reports/2026-10-14-refactor-light-implementation-plan.md)
- [步骤一、二执行报告](reports/2026-10-14-refactor-light-step12.md)
- [当前核心调用关系](docs/architecture.md)

## 当前命令

本轮不改核心命令名，不删除 Shell 包装；这些在后续步骤处理。

| 用途 | 命令 |
|---|---|
| 场景任务发现、站位资格检查、场景库构建 | `discover-scene-datasets`、`qualify-scene-datasets`、`build-scene-datasets` |
| 独立程序化障碍物增强 | `augment-obstacles` |
| 逐点 cuRobo / MolmoBot 评测与热图 | `collect-no-edit` |
| 场景库驱动的 cuRobo 标定与 S0→S1 连续采集 | `collect-lastmile-datasets` |
| 最小困难起点评测定义与 MolmoBot 执行 | `build-lastmilebench`、`evaluate-lastmilebench` |
| 按需模型格式转换 | `export-molmobot` |

`scene-dataset-unit` 是构造 worker 的内部命令。`no-edit-status` 当前只是空分支，不能当作进度查询；后续 CLI 收敛时处理。
`audit*`、`review-lastmile-timeouts`、`index-canonical` 和旧阶段命令不再提供。
场景构造、采集与 adapter 内部仍有旧协议检查，待步骤三解除，不是已经全面去除审计。

## 安装与启动

从本工作树根目录运行，不固定调用其他 worktree 的 `bin/`。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
DATAFLOW_PYTHON=.venv/bin/python bin/lastmile-dataflow --help
```

以上安装基础 Python 依赖；cuRobo CUDA、MolmoBot 代码/权重以及场景、对象、机器人、规划和抓取资产仍需准备。
当前配置和运行说明仍有本机路径，独立环境及下载教程将在步骤七重建；现在不能宣称按上述三条命令即可复现真实采集。

- [合并运行手册](docs/运行手册.md)
- [增强场景构造指南](docs/增强场景构造运行指南.md)
- [scene-datasets 现有协议](docs/datas/scene-datasets-v1.md)
- [LastMile 数据现有协议](docs/datas/lastmile-datasets-v0.md)
- [动作与现有记录格式](docs/datas/data-format.md)
- [Raw-first 与按需 MolmoBot 转换](docs/datas/raw-first-implementation.md)

## 开发与测试

核心算法在领域模块，workflow 负责调用，真实运动只通过 `runtime.Simulation` 执行。
开始执行后不恢复快照或传送；任务成功、规划无解、异常和未测试分别记录。
不依赖 `lastmile_pipeline` 的 Python 代码，不修改原始资产或已有采集结果。

```bash
# <python> 为具备测试依赖的解释器；简化模型/单测不证明真实任务成功
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl PYTHONPATH=src:tests \
  <python> -m unittest discover -s tests -v
```

测试说明见 [tests/README.md](tests/README.md)，配置说明见 [configs/README.md](configs/README.md)。
本轮真实执行状态及已知基线测试失败见步骤一、二执行报告；旧实验报告保留原结论，不作为运行输入。
