# LastMileDataFlow

面向快速实验的机器人数据代码：场景增强与采样 → 场景库 → 逐点成败图 → S0→S1→操作 / 困难 S0 评测。
规划仅支持 **cuRobo V2（0.8.0）**，策略当前为 MolmoBot，导航为 A*。没有旧五阶段、旧 planner 或审计发布体系。

## 安装与数据准备

从当前仓库根目录运行：

```bash
scripts/install.sh
.venv/bin/lastmile-dataflow --help
```

默认一次安装研究工具、cuRobo V2 和 MolmoBot 策略依赖。完整环境、前置数据来源、授权与摆放方式见 [setup.md](docs/setup.md)。数据、测试、cuRobo V2 和最小 multitask 策略共用本仓库的 `.venv`，不借相邻工作树环境。
**当前机器人专用 XML/规划描述是否随公开资产提供尚未验证。** 基础安装成功不代表模型、资产和真实采集已可复现。

## 五个命令

| 命令 | 用途 |
|---|---|
| `build-scenes` | 内部依次发现、资格检查、增强/保存场景库 |
| `evaluate-stations` | 场景库逐点 cuRobo / MolmoBot rollout 与热图 |
| `collect-lastmile` | 匹配标定 → S0→S1→操作；无标定则在当前 run 标定一次 |
| `build-bench` | 从完整标定构建最小困难 S0 benchmark |
| `evaluate-bench` | 独立 MolmoBot 评测与覆盖率汇总 |

每次新建 run，保存有效配置和 seed。图像默认保留，视频、深度和逐物理步日志按需。
显式用 `--gpu <index>` 选择单卡；没有自动队列、旧命令别名或 Shell 启动包装。

```bash
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/val103_cup30_minimal_v3.json --run-id small --gpu <index>
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/small --assets-dir data/assets --run-id stations --gpu <index>
.venv/bin/lastmile-dataflow collect-lastmile \
  --dataset outputs/scene_datasets/small --assets-dir data/assets \
  --calibration outputs/stations --run-id lastmile --gpu <index>
```

这些命令需先按 setup 完成输入准备；没有合格困难配对时保存负结果，不伪造成功。

## 代码入口

六个领域目录：`scenes / runtime / planning / policies / data / workflows`。
Python 实验直接调用 `workflows.collection.evaluate_stations()` / `collect_lastmile()`；模型格式转换使用 `data.export.molmobot_dataset.export_molmobot()`，不另建索引。

- [代码调用关系](docs/architecture.md)
- [第二轮精简实施结果](reports/第二轮精简/实施结果.md)
- [最小数据格式](docs/datas/data-format.md)
- [运行手册](docs/运行手册.md)
- [测试说明](tests/README.md)
- [实施方案](reports/第一轮精简/2026-10-14-refactor-light-implementation-plan.md)
- [步骤六、七执行报告](reports/第一轮精简/2026-10-14-refactor-light-step67.md)

步骤一至五已执行；本轮步骤六、七的安装与验证范围见执行报告。真实 GPU rollout、VLA 推理和完整主链验收属于步骤八，不能用软件回归代替。

最小 multitask 推理迁移与单环境验证见 [实施记录](reports/第一轮精简/2026-10-14-molmobot-multitask-implementation.md)。
