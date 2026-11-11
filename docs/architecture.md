# 架构

- 状态：active
- 日期：2026-11-11
- 替代：无
- 关联：../reports/simplify-refactor/plan/01-refactor-plan.md

入口脚本负责参数和设备绑定，workflows 编排，领域模块实现算法，runtime 实际推进仿真。

| 目录 | 责任 |
|---|---|
| scenes/loading | 场景、快照和资产加载；assets.py 统一索引、选择与缓存 |
| scenes/tasks | 找目标、操作定义和抓取输入 |
| scenes/stations | 站位采样、初始化、覆盖选点 |
| scenes/edits | placement 摆放，checks 物理校验，search 搜索和冻结 |
| planning | cuRobo V2、本体模型、FOV/碰撞、A*、规划池和CPU代理 |
| runtime/control | Panda/RBY-1 适配、动作协议与控制；action.py 统一限位和躯干命令 |
| runtime/construction | 准备、沉降和障碍编辑事务 |
| runtime/execution | 上下文、操作、导航、cuRobo/VLA执行及批次失败隔离 |
| runtime/outcomes | 接触、抓取、Open/VLA 成败与健康检查 |
| data | 唯一实时记录器、成功示教导出、统计与渲染 |
| policies | MolmoBot/π0.5；config.py 统一配置，第三方推理代码保留 |
| workflows | scene_datasets、station_map、collect、lastmilebench 和独立 RL 编排 |

## 场景准备

每任务一个共享准备上下文。先确定真实支撑面、停放机器人、沉降，再生成原场景和障碍编辑场景。原场景使用同一份基线；编辑候选通过碰撞、支撑、沉降、原场景物体影响和求解器健康检查后采站位、冻结快照。没有八类模式、廉价模式代理或修复阶段。

## 站位与采集

station_map 中 CalibrationInputs 显式保存输入，调度器分别处理准备、规划响应、试验执行、写记录和关闭。CPU 准备缓存只读快照；GPU PlanningPool 持有规划视图与常驻求解器；CPU PlanningClient 把后续实测状态发回规划池。没有单站位 StationPlannerCache 或诊断IK查询。

collect 共用准备和调度，按实测成功率生成 S0/S1 全组合，再由同一 runtime 链路执行导航和操作。父进程唯一写 trials.jsonl；AttemptRecorder 写一个 attempt 的真实动作、状态、RGB和视频。采集收尾导出训练所需副本及相对索引，保留原 attempt 供执行分析。

## 控制与结果

开始前可以恢复/初始化状态；begin 后运动通过 stepping 推进。规划参考经本体适配、FK与真实控制器执行，不将规划器成功当任务成功。Panda RoboCasa 参考跟踪位于 execution/context.py。

抓取以 strict-pick-v3 的真实力、抬升、支撑、保持与滑移事实判定。执行终止状态和任务结果分别保存；普通躯干反馈偏差记录为遥测，严重失控仍停止。异常隔离和资源关闭保留，物理判定不依赖被删除的 physics 日志。

## 其他边界

benchmark 仅按标定成功率选困难起点和恢复参考，A* 使用实际几何检查和软净空代价。VLA评估与移动抓取评估归入 outcomes/evaluation.py。RL 专属文件及 third_person 渲染接口保持不变；共享 pick_state 的阈值和 hold-window 逻辑不变。

设备绑定在 CUDA/渲染初始化前完成。gpu.py 查询 EGL 映射，spawn 进程使用显式映射；CUDA_VISIBLE_DEVICES 不能替代 EGL 绑定。
