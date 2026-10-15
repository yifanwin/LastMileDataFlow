> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# 步骤五：目录与采集入口整理

2026-10-14，工作树：`LastMileDataFlow/.worktrees/refactor-light`。

**步骤五已完成。** 源码收敛为六个领域目录，两个旧采集 workflow 已删除，逐点评测和连续采集分别暴露独立函数。最终回归：**170 项通过，1 项跳过**。

## 执行过程与结构

在步骤三、四已经简化的实现上调整归属，先迁移模块及调用，再分离采集入口，最后同步测试和文档。不复制旧实现，不保留重导出兼容壳。

| 新目录 | 接管职责 |
|---|---|
| `scenes` | 场景/资产加载、任务发现、站位采样、障碍物算法和场景物理检查 |
| `runtime` | 机器人动作、实际仿真、编辑事务、任务及物理判定 |
| `planning` | cuRobo V2、A*、规划结果与 waypoint 执行边界 |
| `policies` | 独立策略配置、客户端及服务 |
| `data` | 单套轨迹记录、图像/视频、统计、选点、热图和按需转换 |
| `workflows` | 场景构建、增强编排、统一采集、最小 benchmark |

原 `catalog/construction/tasks/stations/robots/validation/navigation/recording/exporting/integrations/benchmark` 源码目录已删除。包根仍保留薄 CLI、共用配置和读写，不额外增加目录层级。

## 两种采集用途独立，共用执行底座

新公开 API 位于 `workflows/collection.py`：

- `evaluate_stations()`：逐点 cuRobo / MolmoBot rollout，写 `trials.jsonl` 和热图，绝不执行连续恢复。
- `collect_lastmile()`：读取匹配标定；没有标定时在同一个 run 标定一次，再选择 S0/S1，执行 A* + cuRobo V2。已有标定不复制、不重跑；有效次数不足不执行正式恢复。

两个入口共用 run 准备、场景遍历、真实执行器和记录器。不再有 Python API 的 continuous/calibration_only/worker/resume 模式参数。
`workflows/no_edit.py`、`workflows/lastmile_datasets.py` 均已删除，没有兼容壳。

共享循环没有接管所有算法：标定和选点在 data，规划在 planning，真实运动和任务判定在 runtime。标定计数与候选配对只计算一次，绘图和连续执行使用同一份结果。

## 命名与可读性

- `NoEditConfig` → `runtime.execution_config.ExecutionConfig`；当前配置目录改为 `configs/execution/`，示例和默认引用同步更新，删除无用的旧配置 schema 字段。
- `no_edit_execution/evidence/v2` → `runtime.execution/execution_checks/curobo_execution`；`run_raw_attempt` 改为 `run_curobo_attempt`。
- 站位采样/统计/热图分别为 `scenes.sampling`、`data.statistics`、`data.heatmap`。
- 几何候选检查改为 `sample_stations`，移入 scenes，避免与 rollout 的 `evaluate_stations` 混淆。
- 实际障碍物构建提取到 `scenes.augmentation.build_variant`；等时未编辑对照留在 runtime 编辑底座。workflow 只准备输入、调用并写主记录。
- benchmark 的共用设备/profile 函数移到 `runtime.execution_config`，不再导入采集 workflow。
- 清理过时阶段说明、冗余空行和迁移后无用导入；没有新增 manager/factory/registry。

position/native motor、夹爪单位、底盘增量、躯干限位、碰撞及任务成功判据保留，没有用目录合并改变控制语义。

## 验证结果

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-step5-mpl \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  <existing-python> -m unittest discover -s tests
```

最终：**171 项，170 通过，1 跳过，8.187 秒**。跳过项为需显式启用的实际 OpenGL 渲染测试。
日志：`outputs/logs/refactor-step5-tests.log`。

保留原 168 项回归，随迁移更新引用；新增 3 项入口关系测试，确认：

1. 缺少标定时在当前连续 run 内标定一次，没有子目录或重复配置。
2. 不完整标定不触发重复标定或恢复执行。
3. CLI 的两种采集用途分别调用两个公开函数。

原有逐点评测测试新增“不调用恢复”断言；匹配标定不重跑、配置不匹配拒绝、无 A* 路径不伪造执行的回归继续通过。
所有源码/测试语法解析、9 个 CLI help、当前执行/场景/增强示例配置读取通过；确认实际导入的是本 worktree 的 `collection.py`。`git diff --check` 通过。

## 本轮没有做什么

- 步骤六未执行：旧 CLI 命令名、`--calibration-only` CLI 参数和薄 Shell 包装暂留；它们已分发到新函数，不代表旧 workflow 还存在。
- 步骤七未执行：验证仍借用现有解释器，不把它写成仓库依赖；示例中的旧实验路径没有机械替换，独立安装与数据准备仍待处理。
- 步骤八未执行：测试使用简化 MuJoCo 模型和局部 mock，未运行真实资产的 CUDA 规划、MolmoBot 推理或完整采集。
- 不修改历史实验报告、既有数据和其他工作树。旧 Python 导入路径及配置字段不再兼容。

已更新 README、架构、模块说明、配置说明、测试说明及实施方案进度。`AGENTS.md` 按所有者维护约定未改。没有 Git 提交或分支合并。
