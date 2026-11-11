# 第二轮精简：进度记录

方案：`../plan/02-further-simplify-plan.md`。基线 `bfd707d`：单元测试 342 项，原有 3 项不通过（`test_rl_rewards` 需 mjlab 环境；`test_maps.test_support_parameter_validation_and_custom_radius` 用了已删除的 `smoothing_support_distance_m`；`test_tasks.test_task_identity_survives_body_renumbering`），与本轮无关，后续随相关清理一并处理。

测试命令：`MUJOCO_GL=disable PYTHONPATH=src ../../.venv/bin/python -m unittest discover -s tests -t .`（在 worktree 根目录）。

## 已完成

1. Simulation 来源字段显式化：`source/source_metadata/spec/frozen_dir/local_model` 在 `__init__` 声明；派生统一走 `Simulation.derive()`，配合 `closed_on_error`；删除恒为 None 的 `restoration`；`_clock_remainder` 改公开 `clock_remainder`，状态安装统一为 `set_state()`（`SimulationState.initialize`、`restore_snapshot` 共用）；删除旧快照 `fixed_head` 兼容分支、`enable_third_person`、两份 `capture_sensors` 与深度分支；帧缓存由 `Simulation.render` 自己维护（recorder 不再写私有字段，录制不再逐帧计算相机标定）；substep 检查改为返回失败原因字符串或 None；`migrate_state` 拆成四个函数并改用 `mj_name2id`；`session.candidate` 删除恒空的 `target_moves`；vla 接触统计复用同一个 `ContactSampler`；删除无调用方的 `follow_arm_points`。验证：全量单元测试，结果与基线相同。
2. 执行层：新增 `runtime/execution/attempt.py`（建仿真、失败归类、导航收尾、轨迹指标、结果字段，两后端共用）；`curobo.py` 150 行主函数拆成 `CuroboOutcome` + `_settle/_manipulate_with_retries/_release_for_retry/_finalize/_diagnostics`，执行语义常量从 `outcomes/pick.py` 挪到这里，重试判断改用开局记下的目标位置（不再回读 initial_state.json）；`vla.py` 九处 `self.pi05` 分支收成 `VLABackend`（`MOLMOBOT`、`PI05` 两个实例），`VLARollout` 自己就是导航的 runner，删掉 `_PolicyNavigation` 和导航后重复的到达检查；`navigation.py` 改成显式 `NavigationRunner` 协议，新增 `base_error/arrived/base_step_limits`，context 的 `mobile_tick` 共用限速；`ContinuousContext.yaw_gap` 改为模块函数 `joint_base_error`（RBY-1 底盘偏航是有界关节，故意不回绕）；robocasa 跟踪并进 `ContinuousContext._follow_robocasa`；`OperationFailure` 挪到 `protocol.py`；`manipulate_v2` 改名 `manipulate`；只给冒烟用的 `semantic_tick` 挪到 `tests/smoke/stepping.py`。验证：全量单元测试，结果与基线相同。

3. R6 机器人适配器（9df3d22）：新增 `runtime/control/joints.py` 的 `JointMap`（关节/执行器/qpos/dof 地址、`check_joint`、`command_interval`），三个适配器都持有 `robot.joints`；20 维布局常量 `BASE_SLICE/ARM_SLICE/GRIPPER_INDEX/TORSO_INDEX/DELTA_GROUPS` 取代魔数和无人用的 `ACTION_LAYOUT`；原生控制改成持有自身状态的 `NativeControl`；`ROBOTS` 一张分派表；`state22` 挪到 robot.py；`planning_model` 改由 `planning/robots.planning_model()` 按机器人名分派，`SCALE` 挪到 control/panda_omron.py（control 不再引用 planning）；`tcp_matrix` 并入 `scenes/geometry.tcp_pose`；`aim_station` 拆成 yaw/pitch 两步；所有适配器都有 `update_control()`，Simulation 不再 getattr；RoboCasa 控制状态改成 `OscState`，OSC/升降/夹爪魔数改命名常量，`update_control` 拆出 `_arm_torque/_lift_force`；Panda 工厂导出工具挪到 `scenes/loading/panda_factory.py`。顺手修掉基线遗留两项：`test_maps` 删掉已删字段的校验；`discover_tasks` 的关节类型判断 `numpy int in (枚举…)` 恒为 False，导致开门/抽屉任务从来发现不了，改成 `int(...)`（**会让新建场景库多出 open 任务**）。验证：全量单元测试，只剩 test_rl_rewards（缺 mjlab）。
4. R7 与 outcomes 合并（73bd644）：`pick.py`+`evaluation.py` 合成 `outcomes/task.py`（纯判定）；执行期早停和 `timed` 挪到 `execution/checkpoint.py`；`health.py` 删除，`inspect_state` 并入 `scenes/stations/initialization.py`，`independent_results` 删除；非有限标签统一为 `nonfinite_physics`（`PickMonitor`、`inspect_state` 都用 `simulation_failure`），`SIMULATION_FAILURES` 保留旧标签；躯干期望统一用 `torso_joints`；删除永不返回的 legacy 标签循环和 `malformed_physics_evidence` 大 try/except；`ContactFrame.contacts` 改成 `ContactSample`；删除 `contact_observations` 和 `ContactStatistics.update`；`constraint`/`mobile_constraint_failure` 共用 `workspace_constraint`；地面穿透阈值命名为 `FLOOR_PENETRATION_M`。验证同上。
5. GraspPlan 与锁死字段（d110939）：`manipulation._plan()` 返回 `GraspPlan`，后续步骤以参数接收，不再在 `__init__` 外挂属性；`ContinuousContext` 显式持有 `planner_factory`、`prepared_state`、`max_plans`；`planner_backend/max_attempts/torso_error_policy` 改为 `execution_config` 模块常量（结果文件照旧写出），`operation_retries` 上限为 `MAX_OPERATION_RETRIES`；`ExecutionConfig` 校验改成字段表；`head_fov_enabled` 默认 False，配置里删掉 `head_fov_enabled: false` 和已删字段；`require_target_visibility/require_navigation_clearance` 从 ExecutionConfig 删掉；`execution_profile/calibration_execution` 挪到 workflows/collect.py，旧键（mode_config 等）不再列。验证同上。

## 默认决定与用户补充决定（逐条记录，最终总结列出）

- 11-12 用户：『按默认决定一直完成，最后再总结，不要停止直到完成全部』。要拍板的事按方案和审查报告的建议默认决定；GPU 对照不一致时自己查清修好再继续。
- 11-12 用户（目录）：『robots 目录抽出来而不是放到 planning 下』。最终迁移时新建顶层 `lastmile_dataflow/robots/`：原 `planning/robots`（规划模型 + assets/）和原 `runtime/control` 的机器人适配器（robot.py 的 RBY1、panda_omron、panda_robocasa、joints、action、native_actions、interface、ROBOTS、load_robot_spec、state22）都收进去，按机器人分文件，公共的 joints/action/interface 放 robots/ 根下。分层 `geometry → robots → sim → planning → …`，robots 只依赖 config/geometry/utils。`dls.py`（RL 用）不搬，sim/ 里不再有 control/。方案 02 第二节、AGENTS.md、docs/architecture.md 按此改。
- 11-12 用户（目录）：『gpu 放到 utils』。迁移时 `lastmile_dataflow/gpu.py` 移到 `utils/gpu.py`，脚本、workflows、tests 的 import 和 patch 路径一起改；RL 文件只改 import 行。方案 02 第二节、AGENTS.md、docs/architecture.md 同步。
- 默认：Panda 两个适配器不再互相覆盖，共用一个只放关节表、状态读取和相机对准的 `PandaBody` 基类（没有被覆盖的方法），控制方法各写各的。
- 默认：`discover_tasks` 的枚举比较 bug 直接修（见第 3 条）。
- 默认：原生控制的越界报错文字从 “safety bound” 统一成 “configured bound”。

## 剩余（按方案第六节）

- runtime：`lifecycle.execute_isolated_episode` 拆分；`settle_scene` 拆分与 `SettleReport`（`utils.io.diagnostic` 已改名 `jsonable` 并支持 dataclass，未提交）；约 30 处函数内 import。
- planning/scenes、data/policies、workflows：见 `../analyze/02–04` 与方案第三节，均未开始。
- 配置改 YAML、RBY-1 迁新调度器（需 GPU 对照）、统一格式、目录分层迁移：未开始。
