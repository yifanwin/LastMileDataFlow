# PandaOmron 首期实施结果

状态：done  
日期：2026-10-17  
替代：无  
关联：[需求](../../requirement/01-panda-omron.md)、[方案](../../plan/01-panda-omron-phase01.md)、[运行指南](../../docs/panda-omron.md)

**已完成 factory 整机、物理控制、相机、cuRobo V2 对齐及固定场景固定站位的一次真实 pick。** 最终版本复验也已通过。实现位于 `.worktrees/panda-omron-phase01`，分支 `feat/panda-omron-phase01`，从 `main` 创建；没有提交或合并。

## 实际改动

- 新增 Panda 专用配置、factory 组合与资产导出。整机经工厂生成，保留源关节/执行器名和升降机构；后续加载导出的 XML/网格，运行不导入 molmospaces 包。
- 新增语义 `RobotCommand` 与薄接口，Panda 按 `base_xyyaw/lift/arm_q/gripper_width` 控制，不定义训练向量长度。RBY-1 原数组与 native motor 协议保留在其适配器内。
- Simulation 统一创建适配器、物理 stepping、TCP 与相机访问；共享 hold 保持锁定目标，避免重复追随反馈漂移。
- V2 通过本体模型描述获取关节、参考系、TCP、接触链和附着 link；补齐可动手指 URDF/碰撞球。Panda 固定 pick 只释放臂，计划和执行同时锁定 base/lift；关闭 FOV 时不读取 RBY-1 头部。
- 提取共享语义目标跟随，复用真实接触/抬升/保持判据；按实际手指及子部件统计双指力，不给 Panda 伪造 head、idle_arm 或六关节 torso。
- 新增显式 `factory/control/align/pick` smoke 入口与必要软件回归，保留五个生产 CLI 的现有支持边界。

## 三项硬验收与控制

全部实际运行使用本 worktree 独立 `.venv`、MuJoCo 3.5.0、cuRobo V2 0.8.0、Torch 2.7.1，GPU 2 按运行前利用率及显存选择。沙箱外验证设备可用；最初沙箱内的驱动不可见不代表服务器没有 GPU。

| 验证 | 真实结果 | 证据 |
|---|---|---|
| factory 整机 | 完整组合、重载、连续 stepping 5 s，通过；物理 qpos/执行器数均为 13，不代表训练动作维度 | [factory-001](../../../../outputs/diagnostics/panda-omron/factory-001/result.json) |
| 控制与相机 | 平移、偏航、臂、夹爪开闭/中间开度均通过；逐物理步 lift 最大误差 1.1635 mm，低于 2 mm | [control-003](../../../../outputs/diagnostics/panda-omron/control-003/result.json) |
| pivot | 原点及非零平移，yaw=0/90/180°；位置数值残差低于 1e-12 m，旋转最大约 9.9e-8 rad | [align-002](../../../../outputs/diagnostics/panda-omron/align-002/result.json) |
| 随机 TCP/FK | seed=42，20 姿态；位置最大 0.000247 mm，旋转最大 0.001021 rad，全部达到用户确认的目标 | 同上 |
| 手指与碰撞 | 开/闭爪分支坐标误差低于 0.02 mm；MuJoCo 与 V2 的分离/底盘重叠查询一致，开/闭爪碰撞球叠加图已实际查看 | 同上 |

FK 目标为位置 ≤2 mm、旋转 ≤0.005 rad，最大允许值为 ≤5 mm、≤0.01 rad；超过最大值判未对齐。pivot 使用独立刚体公式验证，标准仍为 ≤1 mm、≤0.001 rad。V2 运行时也采用本体描述中的最大误差门槛。

## 固定场景 pick 与实际排错

使用原始 ProcTHOR `val_103` 的 Cup_30，源对象 `Cup|surface|2|28`；固定底盘 `[6.55, 2.0988, 0]`，lift=0.2 m。目标与场景几何未修改，不使用旧站位标定，不搜索 S0/S1。

| attempt | 观察 | 后续处理 |
|---|---|---|
| pick-001 | V2 目标、预抓取、接近均成功；闭爪只有单指承力 | 显式增加接近深度 0.01 m，保持场景/站位 |
| pick-002 | 双指承力，但抬升时滑脱，未完成成功判定 | 核对杯沿夹持几何；候选绕接近轴旋转 π/2，改为径向夹持 |
| pick-003 | 同一真实连续 attempt 完成接近、闭爪、抬升和保持 | 成功，记录完整物理事实 |
| pick-004 | 最终版本加入运行时 FK 最大误差门槛后，使用同一有效配置复验 | 再次成功；尾部保持 3.504 s，最大抬升约 99.65 mm |

另外，首次保持测试升降下沉约 6.2 mm；将重力补偿覆盖实际可动升降结构后解决。控制结果写出曾遇到 NumPy 布尔类型序列化错误，已修正。前一轮对齐按旧的旋转阈值未通过；用户调整标准后另建 align-002 实际复验，旧结果未改写。

pick-003 实测：目标最大抬升 **99.65 mm**，尾部持续满足严格抓取条件 **3.504 s**；底盘最大平移漂移 **0.0651 mm**、偏航 **0.0000903 rad**，lift 漂移 **0.0471 mm**。MuJoCo 中目标没有 weld；附着仅用于 V2 携物规划。执行开始后没有恢复快照、重置或改写 qpos。

最终复验证据：[pick-004 主结果](../../../../outputs/diagnostics/panda-omron/pick-004/result.json)、[最终复验物理事实](../../../../outputs/diagnostics/panda-omron/pick-004/physics.jsonl)。

首个成功的证据：[主结果](../../../../outputs/diagnostics/panda-omron/pick-003/result.json)、[物理事实](../../../../outputs/diagnostics/panda-omron/pick-003/physics.jsonl)、[整机视频](../../../../outputs/diagnostics/panda-omron/pick-003/third_person_camera.mp4)、[腕相机视频](../../../../outputs/diagnostics/panda-omron/pick-003/robot0_eye_in_hand.mp4)。已查看视频末帧；物理判定读取完整逐步事实，画面不替代数值判定。

![成功 pick 的腕相机末帧](../../../../outputs/diagnostics/panda-omron/pick-003/robot0_eye_in_hand-success.png)

## 软件验证与边界

离线回归 **222 项，跳过 1 项**，其余通过；最终 FK 门槛与 native hold 检查更新后，相关 21 项再次通过，真实 pick-004 也通过。跳过的是显式启用的 OpenGL 构建渲染测试。真实相机渲染已通过上述 GPU smoke 独立执行。[回归日志](../../../../outputs/diagnostics/panda-omron/regression-final.log)。

本结果证明该固定 Cup_30 设置下的 PandaOmron 控制、规划和抓取闭环可用；不证明跨场景成功率。接近偏置与径向夹持参数是该案例的有效配置，不作为所有目标的通用修正。尚未接入 station sampling、S0/S1、A*、连续采集、生产 recorder/dataset、MolmoBot、open 或 benchmark，也未验证 Panda 快照生产链。

## 文档归档调整

2026-10-17：本模块文档统一归入 `reports/panda-omron/`，按 requirement、plan、delivery/phase_01、docs 分类；同步修正文档、源码和验收产物的相对链接。
