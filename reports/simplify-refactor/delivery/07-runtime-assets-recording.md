# 资产、运行时与记录器精简

- 状态：done（本轮增量，完整目标仍 active）
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 实际改动

- asset_profiles.py、asset_library.py 并入 loading/assets.py：离线资产索引、按角色选资产、运行时几何缓存使用同一模块；删除旧文件并更新源码、脚本、测试 import。
- execution/checks.py 并入 outcomes/pick.py，抓取物理阈值与成功判定不变；通过函数内读取 evaluator 避免 outcome 之间循环导入。
- robocasa_tracking.py 并入 execution/context.py，保留相同参考跟踪和超时条件。
- outcomes/vla.py 并入 outcomes/evaluation.py，VLA_PROTOCOL 独立命名，避免覆盖 pick 的阈值字典。
- 删除逐物理步 physics、policy_actions、policy_requests 的 gzip 日志、NullTrace、open_trace，以及所有配置开关与写入分支；物理 monitor、接触统计、严重反馈限制与任务判定保留。
- 删除 AttemptRecorder 深度录制分支和相关配置字段；保留数值状态、RGB、视频及录像资源关闭。历史训练数据的深度读取继续由 load_training_batch 支持，接口不变；独立历史观测回放能力仍保留。
- 删除已无人使用的执行配置字段 start_count、max_s1_candidates、lastmile_nearby_m、smoothing_support_distance_m、torso_heights。已有标定记录中的这些选择参数仍不参与执行条件匹配。

## 验证

- 51 项相关测试通过（记录器、躯干安全、入口/调度、训练数据、成败判定、原生控制、RoboCasa 跟踪和预算）。
- 5 项资产索引/选择测试通过。
- 一次全套离线回归：341 项，1 失败、2 错误、14 跳过。其中 task_identity 失败和 mjlab 缺失是既有问题；新增的 station_preparation 测试 import 错误已修复。
- 修复 import 并删除无用配置字段后，26 项针对性测试通过（station_preparation、collection、dataset、asset_profiles）。未重复跑整套。
- git diff --check 和改动模块的未定义名称检查通过；未运行 GPU 或实机实验。

## 未完成

当前 README/主运行手册、部分配置和源码模块说明仍有旧 CLI/模式文本，尚需统一清理；还需对完整计划逐项核对、必要真实构建验证及最终总结。运行时部分长函数和生命周期异常分类仍需最后检查。没有修改 RL 专属逻辑或第三方模型代码。
