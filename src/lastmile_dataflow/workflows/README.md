# workflows

- `scene_datasets.py`：任务发现、资格检查和场景库构建。
- `obstacle_augmentation.py`：准备输入并调用 scenes 的物理增强算法。
- `station_map.py`：`evaluate-stations` 的底盘可达质量图（固定站位 Batch IK、MuJoCo 复核、扩展可操作度、逐 grasp 图）。
- `collection.py`：`collect_lastmile`：内置逐点物理标定（或复用已有标定）与 S0→S1 连续采集。
- `lastmilebench.py`：复用既有开放底盘标定构建困难 S0、恢复区域/路径；独立策略评测与行为/Macro 汇总。

不保留 no_edit 或 lastmile_datasets 兼容文件，没有 manager/factory、父子发布或重复索引。
