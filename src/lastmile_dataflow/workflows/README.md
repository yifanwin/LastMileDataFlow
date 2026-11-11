# 流程编排

- scene_datasets.py：逐房屋发现目标，生成原场景及桌面/地面障碍编辑场景，冻结快照与预览。
- station_map.py：站位标定、全 run 调度、CPU 准备、GPU 规划、CPU 执行与热图；RunResources 与 CalibrationInputs 显式保存输入。
- collect.py：运行准备、标定复用、S0→S1 连续采集及自包含数据导出。
- lastmilebench.py：根据实测成功次数选起点、构建恢复区域并评测 MolmoBot/π0.5。
- rl_training.py、rl_evaluation.py、rl_sampling.py：RL 研究流程，本次逻辑不改。

入口位于 scripts/，算法与实际执行分别由领域模块和 runtime 提供。标定和采集只使用 cuRobo，策略推理保留在 benchmark 中。
