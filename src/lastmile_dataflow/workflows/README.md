# workflows

- `scene_datasets.py`：任务发现、资格检查和场景库构建。
- `obstacle_augmentation.py`：准备输入并调用 scenes 的物理增强算法。
- `collection.py`：两个公开入口 `evaluate_stations` / `collect_lastmile`；共用运行准备和执行器。
- `lastmilebench.py`：复用既有开放底盘标定构建困难 S0、恢复区域/路径；独立策略评测与行为/Macro 汇总。

不保留 no_edit 或 lastmile_datasets 兼容文件，没有 manager/factory、父子发布或重复索引。
