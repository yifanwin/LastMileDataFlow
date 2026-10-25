# PandaOmron 时间优化实施交付

- 状态：done
- 日期：2026-10-25
- 替代：本文件此前的分阶段开发记录
- 关联：../plan/01-panda-omron-time-optimization.md、../plan/03-execution-schedule.md、2026-10-24-stage1-experiment.md、2026-10-24-val100-experiment.md

完成 P00–P14 的软件实现、回归、首阶段正式全量、提前估时与百来源全 Case 正式实验。首阶段达到单任务 20×3 数值结果及回放资料就绪 ≤30 min；最终批次保留了拒绝、准备失败和规划无解，未将这些结果改写成成功。

## 实际改动

- 两阶段 Panda 配方及 execution/lastmile/benchmark 配置：position-v1、每点 3 次、低阈值 1/3、高阈值 1.0；最终固定 val0–99，每房最多 1 个 pick/1 个 open。GPU 列表、CPU 容量 1–60、准备并发 1–12、numerical/visual 模式显式传入 run。
- 每任务固定局部模型，按完整对象/结构保留目标、支撑和关节结构；房屋元数据与几何复用，发现候选直接进入完整资格检查。增强改变模型后重查，覆盖选点后才渲染最终点可见性。
- 房屋发现及任务/Case/变体 spawn 构建，父进程唯一写 scenes.jsonl；复用现有组合编译缓存，临时目录唯一。沿用真实构建事务、数量/支撑/碰撞和站位约束，失败保留 rejected。
- 每站位真实摆位、静置一次，保存完整积分状态、控制目标和控制时钟余数；三个 trial 从同一准备状态分别新建 Simulation/MjData。准备失败单列，活动仿真不重置、不传送。
- cuRobo 0.8.0 共享世界 batch 对应三个独立 core/LM 随机流，保留 32 IK/4 TrajOpt seeds、各原生阶段 5 次重试；失败 slot 无占位执行。所选 GPU 各一个常驻进程，兼容 solver 最多缓存两个 family，按实测状态更新锁定关节、限位、碰撞世界与附着并保留 FK 检查；CUDA Graph 关闭。
- 独立 CPU 池复用原跟踪、monitor 与 recorder；闭爪后保留原物理实例，通过数值 RPC 请求后续规划再继续。GPU 优先响应正在执行的 trial，跨场景补充空闲容量；CPU worker 不初始化 CUDA。300 s 预算从首次 batch 连续累计规划和实际物理时间，排队/准备/I/O 单列。
- 数值标定关闭在线 RGB/深度/视频，按实际控制回调记录一次整场景积分状态及相机参数。离线用稳定模型和独立 MjData 恢复实测边界、mj_forward、按实测时间渲染；不重新规划或积分。recording/replay/observation 完整性分开，数值成功不自动进入视觉训练索引。
- 保持五个顶层 CLI；evaluate-stations 与 collect-lastmile 内置标定共用全局调度，离线视频放在 evaluate-stations 后处理入口。更新当前架构、数据格式、配置与运行说明。

## 执行与验证

1. 先完成软件集成，再进行 P11 全套回归：274 项，272 通过、2 条件跳过。日志 `outputs/diagnostics/curobo-time/p11-software-regression-02.log`；跳过为 pi05-data 可选依赖和既有 opt-in OpenGL 构建检查。第一轮两个 FOV 夹具缺字段，修正夹具后局部和完整回归通过。
正式构建修复后再跑最终全套回归：276 项，274 通过、2 条件跳过；日志 `outputs/diagnostics/curobo-time/final-software-regression-20261025.log`。

2. 首阶段正式构建发现两个实际 XML 重读问题：MuJoCo 3.5 skybox gridlayout 长度、场景/机器人相对资源目录丢失。修正派生 XML 导出及加载时绝对资产引用，原始输入不变；14 项相关回归通过，日志 `p12-asset-path-fix-tests-03.log`。原失败 run 保留，使用新 r03 完成正式构建/标定。
3. [首阶段交付](2026-10-24-stage1-experiment.md)：1 accepted、20 点×3、20 成功/38 执行前无解/2 执行后无解，标定 225.458 s；成功与失败离线视频解码通过。
4. 在最终运行前给出正常/保守估时，构建完成后再按真实 accepted/站位更新估时；[百来源交付](2026-10-24-val100-experiment.md)保存简洁复现命令和完整结果。最终 183 accepted/37 rejected，计划 8,466、记录 8,463；820 成功、284 执行失败、7,359 执行前无解，另 1 站准备失败。标定 2.88 h，6 段实际轨迹视频解码/时序检查通过。
5. 对最终全部记录核对身份、seed、时间/帧数、积分状态和相机数组；8,463 条初始积分状态均与对应公共准备状态相同，183 个模型引用可读。3,868 次 FK 检查及 3,864 次 solver 复用记录已核对，CPU 活动峰值 18、GPU 查询峰值 2、准备峰值 12。

## 结果边界

Case3 全部为执行前规划无解，没有开门执行视频或该轮开门后的真实续规划证据；软件覆盖不能当作开门成功。准备失败站位对应的三次未生成 trial，该场景不进入正式困难配对。其余未知任务判定、执行失败和共享故障分开报告，没有补造证据。

数值轨迹与按需视频不等于完整视觉训练数据；资产仍依赖当前 data_root，MJB 要求匹配 MuJoCo 3.5.0。RoboCasa、连续导航采集、benchmark 和像素逐位一致性不由本轮实验重新证明。没有匹配旧基线，不计算加速倍数。当前范围内无后续实施项。
