# 核心调用关系

场景数据与执行共享仿真底座，但构建不调用策略或规划。

| workflow | 调用与职责 |
|---|---|
| `scene_datasets` | discover_tasks、站位几何/可见性、obstacle_augmentation；输出场景库 |
| `obstacle_augmentation` | asset catalog、几何放置、编辑 session、静置与物理检查；直接写场景记录 |
| `no_edit` | 统一加载场景库、逐点 execution、统计热图、A* 连续采集 |
| `lastmile_datasets` | 薄入口转调 no_edit；不创建 child run |
| `lastmilebench` | 由完整标定挑困难 S0、保存评测场景，然后独立 MolmoBot 执行 |

执行：`Simulation` → 真实 stepping → `Recorder`；cuRobo 仅 `V2Planner`，MolmoBot 为独立策略客户端。通用位姿计算在 `scenes.geometry`，不依赖 cuRobo。
`no_edit` 与 `lastmile_datasets` 已共享实现；步骤五再合并命名和目录，避免同时迁移与抽象。
