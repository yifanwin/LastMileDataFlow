# scenes

- `loading/`：场景来源、MJCF、实例映射、资产定位与测量。
- `tasks/`：任务发现、pick/open 候选和资格规则。
- `stations/`：站位采样、机器人初始化、支撑与可见性筛选；不执行 rollout。
- `augmentation/`：测量后的放置、支撑区域与共用物理检查。
- `build/`：新模式的任务共享准备、提案、物理事务检查、一次 repair 与最终站位搜索内核。
- `modes/`：八类候选构造器与共用几何代理；只声明构造意图，最终分类依赖真实标定。
- `dataset.py` / `dataset_config.py`：场景库记录与实验配方。
- `geometry.py` / `graph.py`：共用几何和场景观测图。

构建流程在 `workflows.scene_datasets`，构建期真实编辑在 `runtime.construction`。
