# 控制、策略配置与本体公共工具精简

- 状态：done（本轮增量；完整精简计划尚未完成）
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 实际改动

根据用户“减少测试、实机测试，优先完成构建”的最新要求，先实施计划阶段 7 中可独立完成的部分，未等待阶段 2 的 GPU 对照。

- 将 action_limits.py、torso_command.py 并入 action.py，删除旧模块并更新内部调用；不保留转发壳。
- 将动作投影拆成增量关节投影、夹爪限位交集、耦合躯干限位交集。共用有限数值和容差裁剪逻辑，保留异常类型、异常消息、限位物理约束、事件字典格式和输出顺序。
- 将站位朝向初始化中的闭包改成成员方法，调整记录通过参数显式传递。
- 将 MolmoBotConfig 和专用读取函数并入 policies/config.py，保留统一后端选择和专用 MolmoBot 边界检查，删除 molmobot_config.py。JSON 配置及路径解析规则不变。
- 将两种本体共用的五个轨迹/状态工具抽到 planning/robots/common.py；cuRobo 不再为通用工具依赖 RBY-1 模型模块。工具实现不变。
- 同步相关现有测试的 import 和当前模块说明；没有新增测试、兼容层或数据结构。外部记录仍用原字典格式，避免改变输出契约。

## 验证与结果

使用主仓库现有 Python 环境、PYTHONPATH=src、MUJOCO_GL=disable 执行：

```bash
python -m unittest tests.runtime.test_actions tests.runtime.test_torso \
  tests.runtime.test_native_control tests.planning.test_curobo \
  tests.workflows.test_config tests.workflows.test_benchmark_progress
```

40 项通过。git diff --check 通过，源码和测试已无已删除模块的 import。

没有运行全套回归、GPU、渲染、真实策略推理或实机测试。相关现有测试通过仅支持其覆盖的软件行为，不能证明真实执行结果与基线一致。

## 未完成项

- 阶段 3 场景构建、阶段 4 规划器站位图、阶段 5 自包含采集、阶段 6 benchmark 尚未实施。
- 阶段 7 的 execution_config 精简依赖阶段 4 的功能删除，尚未完成；阶段 8 的整体收尾尚未完成。
- 阶段 2 场景构建及标定的真实对照、渲染设备隔离仍未解决。
- RL 专属文件、第三方模型代码、本次实际数据与资产均未修改。
