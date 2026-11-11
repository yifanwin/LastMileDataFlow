# 代码精简收束总结

- 状态：done
- 日期：2026-11-12
- 时间：2026-11-12T00:14:45+08:00
- 替代：09-execution-cleanup.md（最新执行状态）
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 收束范围

用户已清除持续目标，并要求结束所有实验、对比和验证、总结结果及更新文档。已启动运行均已结束；本次仅整理既有结果，不再启动实验或重跑测试。总结完成不等于原计划全部验收项通过。

工作区为 .worktrees/simplify，分支 refactor/simplify。最新源码提交72e44e4；之后本次只更新文档和归置已结束运行的日志，不修改源码。未push、未改其他分支，未删除真实数据、权重或资产。

## 代码结果

- 五阶段脚本替代统一CLI；保留Panda、RBY-1、MolmoBot、π0.5与GPU规划池/CPU代理。
- 八模式构造、代理判定、修复、多标签诊断、诊断IK、单站位planner缓存和固定抓取后端已删除。场景改为原场景加桌面/地面障碍编辑，真实物理检查保留。
- 站位标定与采集拆分；采集直接导出自包含示教。benchmark只按实测成功率选择S0/S1及恢复区域。
- 资产加载、动作限位、策略配置、跟踪与成败模块已合并；physics/depth录制和无用配置字段已删除。数值轨迹、RGB、视频、物理判定及必要资源关闭保留。
- 主要执行/调度回调改为显式参数或成员；场景配置位于configs/scenes。过时配置及说明已清理。
- Python模块数：src 203→164，scripts 28→9，tests 91→81。行数受分层与格式展开影响，不将行数压缩作为质量结论。
- RL专属逻辑和第三方模型代码未改。已比较的冻结共享函数/类AST与起点版本一致；未将静态一致性当作RL训练验收。

## 已完成的真实运行

| Run | 源码 | 真实结果及边界 |
|---|---|---|
| scene-check | 71668c4 | house103/Cup_30、seed42，原场景/编辑场景2/2接受；桌面1个障碍、地面0个、各5站位 |
| scene-preview-check | 6de1c22 | 同输入2/2接受，自动预览2张、失败0；已查看编辑前后对比 |
| calibration-check | c1b5b7f | 2026-11-11 23:46:31–23:47:15 +08:00；Panda S0020的1个trial成功，235控制步，三路RGB与视频各236帧，记录完整，无physics/depth文件 |
| collection-check | c1b5b7f | 2026-11-11 23:48:15–23:51:02 +08:00；复用旧完整标定，不重复标定；一个场景3/3连续配对成功、3条自包含示教，耗时167.2s |
| benchmark-check | c1b5b7f | CPU重建2个episode，不执行新标定 |
| policy-check | 72e44e4 | 2026-11-12 00:05:29–00:05:47 +08:00；真实π0.5加载约13.6s，2个episode各执行2步，记录完整，均因policy_step_budget_exhausted失败，无基础设施未知 |

GPU运行只选择当时空闲的GPU0，没有干预其他用户进程。既有独立EGL探针验证准备/规划子进程只在GPU0创建渲染上下文，见03-egl-device-binding.md。π0.5两步检查证明短调用链可执行，不代表完整策略或恢复能力验收。

## 对比与读取结果

- 原场景对阶段0 noedit：站位逐项一致、scene_id一致、MJB字节一致、initial.npz全部数组一致。
- 3条连续轨迹（397/400/395个动作）全部数组与基线一致。train_index无source_run/source外部引用；load_training_batch实际读出state(1,1,16)、actions(1,1,12)及三路(1,1,256,256,3) RGB。
- RGB不逐位一致：3585个PNG中1463个存在像素差异，最大绝对差2/255。原因未定位；不能将渲染卡映射变化直接认定为已证实原因，也不能记为图像一致性验收通过。
- benchmark的episodes.jsonl与oracle.json对基线字节一致，summary仅elapsed_s不同。
- 未计算或保存文件哈希、指纹及逐文件校验清单。

## 最后一次回归

源码72e44e4，主环境、禁用渲染，342项，325通过、14跳过、1失败、2错误，耗时20.556s。

| 未通过项 | 状态 |
|---|---|
| tests.data.test_maps.SupportMapTests.test_support_parameter_validation_and_custom_radius | 本次删除smoothing_support_distance_m后残留旧测试，构造ExecutionConfig时报TypeError；尚未适配 |
| tests.runtime.test_rl_rewards | 主环境缺mjlab，导入错误；既有环境边界，未重装依赖 |
| tests.scenes.test_tasks.TaskTests.test_task_identity_survives_body_renumbering | 既有失败，缺少预期open任务；本次未修复 |

已通过的局部测试见阶段报告；用户要求停止后，不再修改测试、修复问题或重复验证来改变这份结果。

## 未做与未全部验收

没有重跑全量5站位×3次成功率/耗时对照、全部6条采集示教、RBY-1真实站位、完整策略episode、RL smoke/短训练。新地面障碍分支及默认1–3/0–2组合未做完整真实参数覆盖。上述内容不能由一个桌面编辑场景或局部测试推定通过。

代码精简已形成可审阅提交，整体回归尚未全绿，图像严格一致性未通过，原计划的全部真实验收未完成。本轮按用户要求收束，遗留问题如上保留，不再继续运行。

## 产物位置

原始结果、有效配置、PNG/视频及结束后的运行日志统一保留在 outputs/experiments/simplify-refactor/runs/；源码版本与Runs表见该实验单元README。旧基线位于 outputs/experiments/simplify-baseline/，没有改写。此前报告保留为各阶段记录，最新结论以本总结为准。
