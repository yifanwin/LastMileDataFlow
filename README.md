# LastMileDataFlow：最后一公里（Last-Mile）移动操作任务模式、数据管线与验证

**场景增强与采样 → 场景库 → 逐点成败图 → S0→S1→操作 / 困难 S0 评测**。

规划使用 cuRobo V2（0.8.0），策略支持 MolmoBot / π0.5，导航使用 A*。当前只有五个命令。

>什么是“最后一公里”任务：导航和操作的衔接段；机器人已到目标附近，但当前站位无法可靠完成操作，仍然需要通过 底盘移动的调整 来提高操作的成功率。

## 从这里开始

1. [安装与数据准备](docs/setup.md)：主环境 `.venv`，显式配置资产和权重位置；π0.5 使用项目 `.venv-pi05`。
2. [运行手册](docs/运行手册.md)：五个命令、控制模式、标定复用和负结果。
3. [代码结构](docs/architecture.md)：从 workflow 找到算法与实际执行。
4. [数据格式](docs/data-format.md)：场景、trial、配对、训练索引和轨迹。
5. [测试说明](tests/README.md)：离线回归与显式真实验证。

```bash
scripts/install.sh
.venv/bin/lastmile-dataflow --help
```

真实运行需要先完成数据准备；基础安装成功不代表资产、CUDA 规划或模型推理可用。

## 五个命令

| 命令 | 用途 |
|---|---|
| `build-scenes` | Panda RoboCasa 八类模式候选构造与站位采样，保存场景库 |
| `evaluate-stations` | 对场景库逐点执行 cuRobo / MolmoBot / π0.5，保存实测率与热图 |
| `collect-lastmile` | 复用成功标定示教，执行 S0×S1 全组合；支持独立物化为自包含训练数据集 |
| `build-bench` | 复用已有标定；模式数据消费已保存标签及合格 S1；可用 CPU |
| `evaluate-bench` | 独立 MolmoBot / π0.5 闭环评测、恢复/底盘诊断、分组成功率与 Macro |

## 当前边界

- 新构建使用 [v2 任务模式配方](configs/task_modes/README.md)，旧 Case 构造入口已退出。场景构建、完整真实标定和多标签诊断已交付；六类有真实正例，Orientation/Visibility 尚无正例。`proposal_intent` 不作为最终标签。按用户最新要求，本轮止于标定，见 [交付记录](reports/task-modes/delivery/02-calibration-delivery.md)。

- 连续采集仅支持 cuRobo；独立物化为 CPU 后处理，不需要 GPU 或资产。
- PandaOmron 已接入站位采样、cuRobo 标定、A* 与局部底盘—机械臂联合操作，并在 val103 Cup 完成真实连续 S0→S1→pick；步骤与实际结果见 [Panda 指南](reports/panda-omron/docs/panda-omron.md) 和 [实施报告](reports/panda-omron/delivery/phase_02/2026-10-17-panda-omron-full-flow.md)。
- π0.5 已加入双本体适配与可选评测入口；Panda RoboCasa profile 已验证真权重推理和 cuRobo→FK→共用 controller 固定抓取。策略任务表现、批量 benchmark 与 RBY-1 微调分别验收，见 [π0.5 运行指南](reports/pi05/docs/01-running.md) 和 [构建报告](reports/pi05/delivery/03-implementation.md)。
- `evaluate-bench` / `evaluate-stations` 可选 PyRUA 代码 Agent（`configs/policies/pyrua_panda_omron.json`）：保留 PyRUA Agent 与 RPent RoboCasa 原语/RLDX-1，环境接口接本项目 Panda `robocasa-v1` 场景；接口实测与真实运行边界见 [交付记录](reports/pyrua/delivery/01-implementation.md)。
- Panda 已支持从已有标定构建 benchmark；原 position profile 的快照需要按 RoboCasa profile 重建后再供 Panda π0.5 使用。[benchmark 指南](reports/benchmark/docs/运行指南.md)说明命令与产物。
- 当前机器人专用 XML 与规划描述能否从公开资产完整取得，尚未验证。
- 无合格 S0/S1 配对时保留负结果，不用手工配对或未标定 smoke 冒充困难样本。

当前验证状态见 [reports](reports/README.md)。

## 报告与研究文档

`reports/` 按主题组织：主题内的需求放 `requirement/`，方案与计划放 `plan/`，结果与交付放 `delivery/`，分析放 `analyze/`。暂未明确分类的文档放在所属主题根目录，例如 `reports/build-lastmile-datasets/`。

完整目录约定与当前验证状态见 [报告说明](reports/README.md)。
