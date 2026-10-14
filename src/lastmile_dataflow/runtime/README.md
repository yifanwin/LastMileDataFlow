# runtime

`simulation.py` 是真实 stepping 入口，构建和执行共用。begin() 后不能恢复/重置场景。
`execution.py` 负责连续上下文、导航执行和 cuRobo rollout；`curobo_execution.py` 负责 V2 操作流程；`molmobot_execution.py` 负责策略循环。
`robot.py`、action/action_limits/torso_command 和 `native_actions.py` 保留实际控制语义，不把 motor 命令当作米。
contacts/health/pick/outcomes/vla_outcomes 保留实际物理和任务判定；VLA 差异使用同一套 pick/open 判据。
`execution_config.py` 是运行参数及共用 robot/camera/backend profile，benchmark 不通过采集 workflow 获取设备工具。
