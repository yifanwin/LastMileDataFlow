# LastMileDataFlow

面向快速实验的机器人数据代码：场景增强与采样 → 场景库 → 逐点成败图 → S0→S1→操作 / 困难 S0 评测。
历史五阶段已删除；规划仅支持 **cuRobo V2（nvidia-curobo 0.8.0）**，没有旧版回退。策略当前为 MolmoBot，导航为 A*。

## 重构进度

步骤一至五已执行：删除历史代码和审计体系，缩减数据格式，源码收敛到六个领域目录；两种采集入口统一到 `workflows/collection.py`。CLI 命令名和薄 Shell 入口暂留，独立环境及数据下载说明尚未完成。
单元回归不代表真实 GPU 规划、策略推理或全量数据采集成功。

- [实施方案](reports/2026-10-14-refactor-light-implementation-plan.md)
- [步骤三、四执行报告](reports/2026-10-14-refactor-light-step34.md)
- [步骤五执行报告](reports/2026-10-14-refactor-light-step5.md)
- [数据格式](docs/datas/data-format.md)
- [运行手册](docs/运行手册.md)

## 安装与入口

Python 3.11+；基础依赖见 `pyproject.toml`。场景资产、机器人描述和策略权重需用户自行准备；当前示例配置仍有相邻仓库路径，独立部署改造尚未完成。

```bash
python -m pip install -e '.[augmentation]'
python -m lastmile_dataflow.cli --help
# 可选薄包装，不是每次必须经过的协议层
bin/lastmile-dataflow --help
```

V2 规划需要 CUDA、`nvidia-curobo==0.8.0` 及 `planning-v080` 可选依赖。只装基础包不能运行规划。MolmoBot 使用显式配置的独立策略服务，不由本仓库重新实现模型。

## 当前命令

| 功能 | 命令 |
|---|---|
| 场景任务发现、站位资格检查、场景库构建 | `discover-scene-datasets`、`qualify-scene-datasets`、`build-scene-datasets` |
| 独立障碍物增强 | `augment-obstacles` |
| 场景库逐点 cuRobo / MolmoBot 成败图 | `collect-no-edit` |
| 共用逐点标定及 S0→S1→操作采集 | `collect-lastmile-datasets` |
| 最小困难起点评测构建、MolmoBot 执行 | `build-lastmilebench`、`evaluate-lastmilebench` |
| 按需数据转换 | `export-molmobot` |

不再提供 audit、seal、重复索引、worker 队列或旧数据兼容命令。每次新建实验目录，保留配置和种子；视频、深度和逐物理步诊断按需启用。

## 代码入口

源码只有六个领域目录：`scenes` 构建与采样，`runtime` 实际执行与任务判定，`planning` 包含 V2 和 A*，`policies` 接策略，`data` 记录/选点/绘图，`workflows` 编排。

Python 实验可直接调用两个函数，不需要 CLI 模式开关：

```python
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.runtime.execution_config import load_execution_config
from lastmile_dataflow.workflows.collection import evaluate_stations, collect_lastmile

config = load_execution_config("configs/execution/curobo_v2.json")
recording = CollectionConfig(record_video=False)
calibration = evaluate_stations("<scene-dataset>", "<assets>", config, recording,
                               "outputs", run_id="stations", gpu_ids=[0])
collect_lastmile("<scene-dataset>", "<assets>", config, recording, "outputs",
                 run_id="lastmile", gpu_ids=[0], calibration=calibration)
```

已有匹配标定不再执行逐点 rollout；没有标定时，在连续采集 run 内标定一次。有效试验不足时不执行正式 S0/S1 恢复。
当前 CLI 的 `collect-no-edit` 和 `collect-lastmile-datasets` 分发到上述函数；命令改名留在步骤六。
