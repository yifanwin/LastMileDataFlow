# planning

`curobo_v2.py` 仅 V2Planner（0.8.0），保留 11D 底盘/躯干/手臂、碰撞和 head-FOV 约束；没有旧 planner。
`astar.py` 为导航搜索；`waypoints.py` 定义规划结果及执行边界。位姿数学在 `scenes.geometry`，真实步进在 runtime。
