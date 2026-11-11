# 执行流程收尾

- 状态：active
- 日期：2026-11-12
- 时间：2026-11-12T00:02:59+08:00
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 实际改动

抓取执行改为顶层 _Manipulation 对象，按规划、监控初始化、接近闭合、闭爪后重建、抬升保持、开门目标计算和开门保持拆分；planner 资源与回调通过成员显式持有，删除 release/query 排序闭包。候选排序使用具名评分函数和显式参数，保留排序、随机序列、seed、规划预算和输出。

VLA 执行改为顶层 _VLARollout，导航适配通过 _PolicyNavigation dataclass 组合；导航检查、实际 stepping、物理检查、观测准备、策略请求、动作应用、视频和结果收尾分别处理，不再依赖嵌套执行闭包。公开入口及隔离装饰器保留，资源清理与成败阈值不改。

修复两个保留执行预设里的废弃 smoothing_support_distance_m 字段。生命周期仍保留实际需要的初始化/执行/记录局部错误和共享资源错误，不为删代码而移除磁盘、GPU及资源关闭故障隔离。

## 验证

42项相关测试通过，包括 episode 隔离、初始化失败关闭、planner 预算/seed/跟踪、成败判定、实际spawn调度。未定义名称检查与 git diff --check 通过。真实规划/策略验证将在固定提交上执行后补记；当前不将初始化隔离测试等同于完整VLA执行通过。

## 剩余

固定代码后的必要真实标定/数据导出/benchmark与RL接口检查，以及最终计划逐项结论尚待完成。完整目标继续active。

## 固定版本真实结果（c1b5b7f）

- 2026-11-11 23:46:31–23:47:15 +08:00：GPU0上真实Panda S0020标定trial成功，235个控制步，recording/observation完整；三路RGB各236帧、三路视频各236帧，无physics/depth文件。max_trials=1为主动缩小的集成检查，不属于完整标定。
- 2026-11-11 23:48:15–23:51:02 +08:00：复用既有完整标定，ground_obstacle场景3个配对均连续采集成功，3条自包含训练索引没有source_run/source外部引用。load_training_batch读出state(1,1,16)、actions(1,1,12)和三路256×256 RGB。三条轨迹所有数组与基线完全一致（397/400/395个动作）。
- benchmark-check：CPU重建2个episode，episodes.jsonl/oracle.json与基线字节一致，summary仅elapsed_s不同。
- RL专属文件无改动；PandaOmronConfig、disk_samples、robot_contacts、floor_support、room_triangles、body_pose、descendants、geom_points、local_bounds、update_hold_window和video_writer的AST与起点版本一致。

## 最后代码清理

批量seed回调、CPU规划回调、物理检查回调、失败记录回调与串行标定生成器改为具名函数/成员和显式参数绑定；未新增抽象层。删除已无生产者的场景代理初始化缓存和未使用proposal参数。删除仅旧CLI使用的通用load_config。场景配置归入计划要求的configs/scenes，调用和说明同步。

相关局部测试分别36项、30项、28项通过；不重复运行已确认的其他领域测试。完整目标仍待最终固定版本策略/回归检查与计划结论更新。
