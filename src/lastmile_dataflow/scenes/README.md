# 场景

- loading/：场景、资产与冻结快照加载。
- tasks/：目标发现和抓取输入。
- stations/：站位采样、初始化和覆盖选点。
- edits/placement.py：支撑区域、资产选择与桌面/地面障碍摆放。
- edits/checks.py：碰撞、沉降、支撑、原场景影响与健康检查。
- edits/search.py：任务准备、原场景和编辑场景搜索、冻结快照。
- dataset.py：SceneRecipe、SceneEditConfig、SceneStationConfig，以及读取已接受场景记录的 load_scenes。

场景编辑只改变障碍物，不构造模式标签，也不执行操作或判定任务成功。
