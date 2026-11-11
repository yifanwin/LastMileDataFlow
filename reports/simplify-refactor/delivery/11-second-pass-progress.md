# 第二轮精简：进度记录

方案：`../plan/02-further-simplify-plan.md`。基线 `bfd707d`：单元测试 342 项，原有 3 项不通过（`test_rl_rewards` 需 mjlab 环境；`test_maps.test_support_parameter_validation_and_custom_radius` 用了已删除的 `smoothing_support_distance_m`；`test_tasks.test_task_identity_survives_body_renumbering`），与本轮无关，后续随相关清理一并处理。

测试命令：`MUJOCO_GL=disable PYTHONPATH=src ../../.venv/bin/python -m unittest discover -s tests -t .`（在 worktree 根目录）。

## 已完成

1. Simulation 来源字段显式化：`source/source_metadata/spec/frozen_dir/local_model` 在 `__init__` 声明；派生统一走 `Simulation.derive()`，配合 `closed_on_error`；删除恒为 None 的 `restoration`；`_clock_remainder` 改公开 `clock_remainder`，状态安装统一为 `set_state()`（`SimulationState.initialize`、`restore_snapshot` 共用）；删除旧快照 `fixed_head` 兼容分支、`enable_third_person`、两份 `capture_sensors` 与深度分支；帧缓存由 `Simulation.render` 自己维护（recorder 不再写私有字段，录制不再逐帧计算相机标定）；substep 检查改为返回失败原因字符串或 None；`migrate_state` 拆成四个函数并改用 `mj_name2id`；`session.candidate` 删除恒空的 `target_moves`；vla 接触统计复用同一个 `ContactSampler`；删除无调用方的 `follow_arm_points`。验证：全量单元测试，结果与基线相同。
