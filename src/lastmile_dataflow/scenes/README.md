# scenes

- `loading/`：场景来源、MJCF、实例映射、资产定位与测量。
- `tasks/`：任务发现、pick/open 候选和资格规则。
- `stations/`：站位采样、机器人初始化、支撑与可见性筛选；不执行 rollout。
- `augmentation/`：障碍物配置、候选采样、区域、物理检查和覆盖选点。
- `dataset.py` / `dataset_config.py`：场景库记录与实验配方。
- `geometry.py` / `graph.py`：共用几何和场景观测图。

构建流程在 `workflows.scene_datasets`，构建期真实编辑在 `runtime.construction`。
