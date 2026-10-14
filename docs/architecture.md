# 当前核心调用关系

历史五阶段编排已删除。下面是保留代码的实际调用关系，不表示真实闭环已验证。

| Workflow | 实际职责与调用 |
|---|---|
| `scene_datasets` | 任务发现、资格检查、构造编排；Case1/2 调用 `obstacle_augmentation.augment_obstacles`，Case3 保留原 open 场景；整理场景包 |
| `obstacle_augmentation` | 资产选择、放置、编辑、静置、几何/支撑检查、增强前后站位与视图 |
| `no_edit` | 读场景包或原始/增强输入，逐点调用 cuRobo / VLA 执行器，统计与热图；cuRobo 分支调用 A* 和连续操作 |
| `lastmile_datasets` | 遍历场景包并调用 `no_edit.collect_batch`，汇总连续数据；现有二次审计和索引待删除 |
| `lastmilebench` | 从场景包和显式标定选起点；直接调用 VLA 执行器评测，不调用 `lastmile_datasets.collect` |

## 共享底座与不能整文件删除的部分

- `scenes`、`tasks`、`stations`：场景/任务读取、站位采样、初始化、统计和选择。
- `runtime.Simulation`、`robots`：物理执行、控制语义和生命周期。
- `runtime.obstacle_edit_session`：程序化编辑；仍调用 `runtime.build_session.migrate_state`。
- `runtime.build_session`：旧 `BuildSession` 类已删除，仅保留状态迁移及快照加载仍调用的身份函数；旧快照协议在步骤三处理。
- `planning.curobo_v2`：当前 v0.8.0 路线；仍借用 `planning.curobo` 的位姿工具，不整块删除旧文件。
- `policies.molmobot_service`：由模型子进程动态启动，不能仅凭静态 import 未命中删除。
- `workflows.native_station_input`：现有 native 站位适配脚本仍使用，不属于历史五阶段。
- `recording`、`validation`、`integrations.molmobot_dataset`：核心记录、任务判定与按需转换。内部 canonical 审计和重复索引暂存到协议重构。

## 后续目标

`no_edit` 与 `lastmile_datasets` 合并为 `workflows/collection.py`，拆清逐点评测与连续采集；本轮尚未合并或搬目录。
删除场景、记录和下游封存/审计耦合之后再整理模块，避免保留两套兼容路径。
完整步骤见 [实施方案](../reports/2026-10-14-refactor-light-implementation-plan.md)。
