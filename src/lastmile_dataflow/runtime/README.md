# runtime

`simulation.py` 是构建和执行共用的真实 stepping 入口；begin() 后不能恢复或重置。
`execution_config.py` 管理运行参数与 robot/camera/backend profile。

- `control/`：机器人映射、20D 动作、限位、躯干与 native motor 语义。
- `construction/`：准备、静置、按名称迁移状态、编辑事务和 native 场景转换。
- `execution/`：`context` 连续控制、`navigation` 导航、`curobo` attempt 编排、
  `manipulation` V2 操作、`molmobot` 策略循环、`checks` 终止判定、`lifecycle` 失败隔离与关闭。
- `outcomes/`：接触、数值健康、pick/open 物理结果；VLA 差异在 `vla.py`。

两个 attempt 入口的 `root`、`attempt_id` 为关键字参数。没有旧模块路径兼容层。
轨迹和 PNG 由 Simulation 的初始/实际执行边界记录一次；执行器只消费观测、设置 phase 和生成可选视频。
