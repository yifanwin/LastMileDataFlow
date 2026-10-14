# scenes

从 `source.py` / `mjcf.py` 加载场景；`tasks.py` 发现任务与抓取候选；`dataset.py` 读取主场景记录。
`sampling.py` 生成采样点、初始化与可见性检查；它不执行 rollout。
`augmentation.py` 为有界障碍物放置，配合 obstacle_sampling/regions/checks 和 runtime 编辑 session。
`assets.py` / `asset_library.py` 负责必要的资产定位和测量。没有封存或额外检索索引。
