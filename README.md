# LastMileDataFlow：最后一公里（Last-Mile）移动操作任务模式、数据管线与验证

**场景增强与采样 → 场景库 → 逐点成败图 → S0→S1→操作 / 困难 S0 评测**。

规划使用 cuRobo V2（0.8.0），策略使用 MolmoBot，导航使用 A*。当前只有五个命令。

>什么是“最后一公里”任务：导航和操作的衔接段；机器人已到目标附近，但当前站位无法可靠完成操作，仍然需要通过 底盘移动的调整 来提高操作的成功率。

## 从这里开始

1. [安装与数据准备](docs/setup.md)：一个 `.venv`，显式配置资产和权重位置。
2. [运行手册](docs/运行手册.md)：五个命令、控制模式、标定复用和负结果。
3. [代码结构](docs/architecture.md)：从 workflow 找到算法与实际执行。
4. [数据格式](docs/data-format.md)：场景、trial、episode 和轨迹。
5. [测试说明](tests/README.md)：离线回归与显式真实验证。

```bash
scripts/install.sh
.venv/bin/lastmile-dataflow --help
```

真实运行需要先完成数据准备；基础安装成功不代表资产、CUDA 规划或模型推理可用。

## 五个命令

| 命令 | 用途 |
|---|---|
| `build-scenes` | 发现任务、资格筛选、场景增强与站位采样，保存场景库 |
| `evaluate-stations` | 对场景库逐点执行 cuRobo / MolmoBot，保存实测率与热图 |
| `collect-lastmile` | 复用匹配标定，或在当前 run 标定一次，再执行 S0→S1→操作 |
| `build-bench` | 根据完整匹配标定构建困难 S0 benchmark |
| `evaluate-bench` | 独立 MolmoBot 评测与覆盖率汇总 |

## 当前边界

- 连续采集仅支持 cuRobo。
- 已有有限真实实验包含导航成功、规划无解与策略超时，**尚未验证成功操作示教和真实困难 S0 评测闭环**。
- 当前机器人专用 XML 与规划描述能否从公开资产完整取得，尚未验证。
- 无合格 S0/S1 配对时保留负结果，不用手工配对或未标定 smoke 冒充困难样本。

当前验证状态见 [reports](reports/README.md)。
