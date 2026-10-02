# `planning/`

`curobo.py` 直接使用原生 cuRobo，构建活动臂与实测锁定关节、实际碰撞 geom 世界和 FK 坐标检查。
没有旧管线/MolmoSpaces Python 包依赖。CUDA 不可用记设施异常，不用假路径。
`PlanResult` 保留无解状态和有限预算；失败不能含可执行路点。

当前普通抓取尚不在规划器中附着持物模型，物理全过程仍检查接触。
持物规划与连续示教是后续扩展。[阶段三说明](../../../docs/phase3.md)。
