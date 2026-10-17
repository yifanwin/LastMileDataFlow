# PandaOmron 全流程接入实施记录

状态：done  
日期：2026-10-17  
替代：无  
关联：[需求](../../requirement/02-panda-omron-full-flow.md)、[方案](../../plan/02-panda-omron-full-flow.md)

## 交付结果

**已完成 PandaOmron 全流程接入，并通过原始 val103 Cup 的完整标定、两个 S0→S1 连续抓取配对、物化和实际读取验收。** 工作仍位于 `.worktrees/panda-omron-phase01` / `feat/panda-omron-phase01`，没有提交或合并。

实现包括：实际相机与站位初始化、控制目标快照/克隆、共享语义导航、Panda cuRobo 局部移动模型与同步轨迹执行、闭爪后固定底盘抬升，以及按本体布局记录和物化读取。复用现有 build/evaluate/collect 入口，没有另建生产采集链。

## 已取得的实际结果

- [最终软件回归](../../../../outputs/diagnostics/panda-omron/regression-phase02-final.log)：224 项，223 通过、1 项显式 OpenGL 测试跳过；包括 Panda 快照/克隆、真实 stepping 的命令/状态边界及 Panda 物化读取检查。
- 原整机展示相机不适合站位筛选，新增固定安装的前向相机配置，保留首期配置。原场景 49 个候选中 17 个通过几何与可见性；按保守整机通行约束筛选后剩 4 个，再用现有覆盖选点取 3 个作为本轮小规模完整标定输入。
- [独立绕障 navigation-002](../../../../outputs/diagnostics/panda-omron/navigation-002/result.json)：整机绕实体箱体移动到 `[2,0,π/2]`；653 个跟踪步，到达位置误差 1.4342 mm、偏航误差 0.004561 rad、臂保持误差 0.0000161 rad、lift 误差低于 1 μm，禁止整机接触计数为 0。
- [生产入口局部试验 flow-evaluate-003](../../../../outputs/diagnostics/panda-omron/flow-evaluate-003/trials.jsonl)：cuRobo 联合规划与真实 pick 成功。此 run 使用单次试验预算，标定不完整，不能用于正式 S0/S1 配对。
- 首轮完整标定 `flow-calibration-001`：3 个有效站位，每站 2 次，4 成功、2 有限预算规划无解，得到 2 个合格配对；补齐底盘后沿碰撞球后另启 `flow-calibration-002`，后续只复用最终模型的标定。
- [最终移动 FK 与几何检查](../../../../outputs/diagnostics/panda-omron/align-mobile-003/result.json)：20 个随机底盘/臂组合姿态，覆盖非零平移与 yaw=0/90/180°参考系；TCP 位置最大约 1.801e-7 m，旋转最大约 0.000958 rad，达到目标；pivot 通过。底盘、固定柱与升降柱的实际碰撞几何顶点均被对应球近似覆盖。
- [生产场景构建](../../../../outputs/diagnostics/panda-omron/scene_datasets/panda-build-001/summary.json)：`build-scenes` 真正通过任务发现、资格检查、Case2 放置/物理检查、快照与站位保存，接受 1 个增强场景并导出预览。此增强场景未被用来冒充原场景的抓取闭环。

## 实际排错

- 第一次生产加载时，快照早于新增机器人配置字段；重新生成匹配配置的输入快照，没有改写旧快照。
- `flow-evaluate-002` 的底盘跟踪超时来自 Omron 固定支撑柱碰家具。依据真实几何补齐固定/升降支撑柱的规划碰撞球后，同站位 `flow-evaluate-003` 已成功；没有删除家具或放宽物理约束。
- 核对实际底盘几何后，补齐旧球近似漏掉的后沿；最终模型的底盘/支撑覆盖与移动 FK 在 `align-mobile-003` 复验，完整标定重新运行，没有混用旧碰撞模型的标定。
- `navigation-001` 用底盘足迹绕过箱体时，上方机械臂仍接触箱体。Panda 移动配置改用保持姿态整机包络和高度，并将同一通行约束用于站位筛选；`navigation-002` 已真实通过。RBY 默认继续使用原导航配置。
- Cup 杯沿可以局部夹持，整物体包围盒并不等于所需开口。站位几何配置区分实际夹爪宽度与任务发现的物体尺寸粗筛上界，实际能否抓取仍由候选、规划和物理试验决定。

## 完整标定与连续采集

最终碰撞模型的完整标定 [flow-calibration-002](../../../../outputs/diagnostics/panda-omron/flow-calibration-002/trials.jsonl)：S0021 为 2/2 成功、S0020 为 0/2（有限预算规划无解）、S0013 为 2/2 成功，得到 S0020→S0021 和 S0020→S0013 两个配对。每站两次仅用于小规模接入验收，不据此估计跨场景成功率。

[flow-collect-001 主配对结果](../../../../outputs/diagnostics/panda-omron/flow-collect-001/pair_index.jsonl) 直接复用最终完整标定，没有重复标定，也没有到站恢复快照；两个配对均连续成功。

| 配对 | 控制步 | 到站位置 / yaw 误差 | 操作阶段底盘最大位移 / 臂最大变化 | 最大抬升 / 严格尾部保持 | 禁止接触 |
|---|---:|---|---|---|---:|
| S0020→S0021 | 315 | 5.2044 mm / 0.004271 rad | 0.4964 m / 0.9808 rad | 97.04 mm / 2.024 s | 0 |
| S0020→S0013 | 375 | 0.3055 mm / 0.004296 rad | 0.5002 m / 0.9115 rad | 99.55 mm / 2.032 s | 0 |

底盘位移与臂变化均从到站后的操作段实测，不能用导航位移冒充联合操作。禁止接触计数检查了两个 attempt 的完整逐物理步记录；导航、到站、接近、闭爪、抬升和保持各阶段均为 0。底盘局部 XY 每轴限制 ±0.35 m，并受场景工作区约束；上表为世界坐标位移范数，反馈跟踪误差另有记录。
第一配对证据：[实际轨迹](../../../../outputs/diagnostics/panda-omron/flow-collect-001/rollouts/r000000/trajectory.npz)、[逐物理步事实](../../../../outputs/diagnostics/panda-omron/flow-collect-001/rollouts/r000000/physics.jsonl.gz)、[前向相机视频](../../../../outputs/diagnostics/panda-omron/flow-collect-001/rollouts/r000000/videos/front_camera.mp4)、[腕相机视频](../../../../outputs/diagnostics/panda-omron/flow-collect-001/rollouts/r000000/videos/robot0_eye_in_hand.mp4)。已实际查看成功末帧，物理成功仍由完整事实判定。

## 记录语义

Panda 记录布局 `panda-semantic-v1` / `panda-absolute-targets-v1`：世界 base x/y/yaw、固定 lift、按关节名顺序的 7 个 arm_q、总夹爪开口，单位 m/rad，动作是绝对目标。仅在 recorder 的本体边界展平；物理关节和规划活动变量仍独立定义。每条记录附字段顺序与 `state[t], action[t], state[t+1]` 对应，不冒充 RBY-1 的 20D/state22。

## 物化与实际读取

通过生产命令物化 [flow-materialized-001](../../../../outputs/diagnostics/panda-omron/flow-materialized-001/summary.json)，状态 completed，6 条不同实际 attempt：4 条 `s1_direct_operation`、2 条 `s0_to_s1_continuous`。失败试验没有进入成功示范。

实际调用 `load_training_batch` 读取两个连续示例（索引 4、5），每条 16 个控制间隔：`state/actions` 均为 `(2,16,12)`，前向与腕相机均为 `(2,16,240,320,3)`。该长度来自本次明确语义字段的记录布局，不是物理自由度或统一训练动作维数。第一轨迹有 316 个状态/315 条动作，第二有 376 个状态/375 条动作；状态与命令时间边界一致。

## 验证范围与限制

- 所有实际运行使用 worktree 独立环境，GPU 0 按运行前利用率与空闲显存选择。MuJoCo/cuRobo 版本沿用首期环境。
- 本轮证明原始 val103 Cup 的完整链路可用；增强 Case2 的构建已经实际 accepted，但未对该增强场景重新做抓取标定，不能据此声称其采集成功。
- 每站两次只是接入检查，不代表跨场景成功率。杯沿径向夹持参数继续显式配置，不作为所有物体通用修正。
- lift 全程固定在初始化高度，Omron 采用源全向平面模型；未验证真实轮系运动学。站位使用实际前向相机筛选，本轮未实现 Panda 相机 FOV 优化约束。
- MolmoBot/VLA、open 与 benchmark 未纳入；RBY-1 受影响软件回归通过，本次未重跑其真实 GPU 采集。
- 运行指南、机器人/执行/场景配置说明和数据格式文档已同步；没有提交或合并。
