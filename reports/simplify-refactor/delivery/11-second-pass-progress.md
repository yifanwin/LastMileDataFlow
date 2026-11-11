# 第二轮精简：进度记录

方案：`../plan/02-further-simplify-plan.md`。基线 `bfd707d`：单元测试 342 项，原有 3 项不通过（`test_rl_rewards` 需 mjlab 环境；`test_maps.test_support_parameter_validation_and_custom_radius` 用了已删除的 `smoothing_support_distance_m`；`test_tasks.test_task_identity_survives_body_renumbering`），与本轮无关，后续随相关清理一并处理。

测试命令：`MUJOCO_GL=disable PYTHONPATH=src ../../.venv/bin/python -m unittest discover -s tests -t .`（在 worktree 根目录）。

## 已完成

1. Simulation 来源字段显式化：`source/source_metadata/spec/frozen_dir/local_model` 在 `__init__` 声明；派生统一走 `Simulation.derive()`，配合 `closed_on_error`；删除恒为 None 的 `restoration`；`_clock_remainder` 改公开 `clock_remainder`，状态安装统一为 `set_state()`（`SimulationState.initialize`、`restore_snapshot` 共用）；删除旧快照 `fixed_head` 兼容分支、`enable_third_person`、两份 `capture_sensors` 与深度分支；帧缓存由 `Simulation.render` 自己维护（recorder 不再写私有字段，录制不再逐帧计算相机标定）；substep 检查改为返回失败原因字符串或 None；`migrate_state` 拆成四个函数并改用 `mj_name2id`；`session.candidate` 删除恒空的 `target_moves`；vla 接触统计复用同一个 `ContactSampler`；删除无调用方的 `follow_arm_points`。验证：全量单元测试，结果与基线相同。
2. 执行层：新增 `runtime/execution/attempt.py`（建仿真、失败归类、导航收尾、轨迹指标、结果字段，两后端共用）；`curobo.py` 150 行主函数拆成 `CuroboOutcome` + `_settle/_manipulate_with_retries/_release_for_retry/_finalize/_diagnostics`，执行语义常量从 `outcomes/pick.py` 挪到这里，重试判断改用开局记下的目标位置（不再回读 initial_state.json）；`vla.py` 九处 `self.pi05` 分支收成 `VLABackend`（`MOLMOBOT`、`PI05` 两个实例），`VLARollout` 自己就是导航的 runner，删掉 `_PolicyNavigation` 和导航后重复的到达检查；`navigation.py` 改成显式 `NavigationRunner` 协议，新增 `base_error/arrived/base_step_limits`，context 的 `mobile_tick` 共用限速；`ContinuousContext.yaw_gap` 改为模块函数 `joint_base_error`（RBY-1 底盘偏航是有界关节，故意不回绕）；robocasa 跟踪并进 `ContinuousContext._follow_robocasa`；`OperationFailure` 挪到 `protocol.py`；`manipulate_v2` 改名 `manipulate`；只给冒烟用的 `semantic_tick` 挪到 `tests/smoke/stepping.py`。验证：全量单元测试，结果与基线相同。

## 剩余（按方案第六节）

- runtime：R6 机器人适配器（`JointMap` 组合、去掉 `PandaRoboCasaAdapter` 继承、`NativeControl`、一张 `ROBOTS` 分派表）；R7 非有限标签合一、躯干期望统一、`pick.py`+`evaluation.py` 合并、`health.inspect_state` 并入 stations；`manipulation.py` 的 `GraspPlan`；`lifecycle.execute_isolated_episode` 拆分；`execution_config` 锁死字段改常量；`settle_scene` 拆分与 `SettleReport`；`contacts.ContactSample`；`panda_robocasa.OscState`；约 30 处函数内 import。
- planning/scenes、data/policies、workflows：见 `../analyze/02–04` 与方案第三节，均未开始。
- 配置改 YAML、RBY-1 迁新调度器（需 GPU 对照）、统一格式、目录分层迁移：未开始。
