# PandaOmron 局部任务场景与多环境 batch 分析

- 状态：archived
- 日期：2026-10-24
- 替代：[当前文档](../../../info/01-current-status.md)
- 关联：[trial 次数建议](2026-10-24-panda-curobo-trial-count.md)、[耗时分析](2026-10-20-scene-datasets-and-calibration-cost.md)、[E03 实现](../delivery/E03-telemetry-parallel-planner-reuse.md)

**2026-10-24 归档：保留当时的设置、结论与未验证边界；当前实验以 PandaOmron、每站位 3 次为准。本文中的旧预算与待办不作为当前方案。**

**用户当前关注 PandaOmron 从局部初始站位完成任务的难度，不要求跨房间任务。建议先把完整房屋转为每任务固定的局部场景，再测现有多 worker；原生 cuRobo batch 可继续研究，GPU batch 物理作为后续选项。** 本次只核对实现与官方资料、提出方案，没有裁剪资产、修改执行代码或运行新物理实验。

## 三种“多环境”

| 方式 | 当前情况 | 主要作用与边界 |
|---|---|---|
| 多 worker、多个独立仿真 | E03 已实现；可共用 GPU | CPU 物理并行、planner 各自执行，不是一个 tensor batch；实际提速仍待测 |
| 多环境共用原生 batched planner | cuRobo v0.8.0 有 BatchMotionPlanner，本仓库尚未接入 | 一次 GPU 调用处理多组初态/目标与碰撞环境；不会自动批量化 MuJoCo 物理 |
| GPU batch 物理 | 本仓库尚未接入 MJX/MJWarp | 面向高吞吐；需适配控制、接触力、逐 4 ms 判定、渲染与记录，不是修改 batch_size 即可完成 |

局部实验适合把同一任务的不同站位、或同站位的不同 trial 放入不同环境，不要求这些环境来自不同房间。每个环境仍有独立状态、seed、物理判定和记录。当前共享模型的 clone 方式已能给同一个只读 MuJoCo model 创建多份独立 MjData；线程方案还须确认控制适配器不写共享模型。

### cuRobo 原生 batch 的实际限制

已核对固定 v0.8.0 源码 `curobo/_src/motion/motion_planner_batch.py` 和 `motion_planner_cfg.py`：

- BatchMotionPlanner 支持批量 plan_pose/plan_grasp；`max_batch_size` 控制容量，`multi_env=True` 为每个问题使用不同碰撞环境。
- 本地源码中批量 plan_pose 实际有重试循环，但默认 `max_attempts=1`；单体入口默认是 5。文件顶部仍有“无重试”的旧描述，应以函数实现为准。
- `multi_env=True` 时不构造 PRM graph planner。这是 cuRobo 内部路径搜索，不是项目的 A* 导航。
- 现有世界坐标以每个初始底盘为参考，站位变化会改变该坐标系；同一个物理场景不能未经坐标统一就当成共享碰撞世界。
- Panda 闭爪后重新按实测状态锁定底盘/夹爪，各环境的值不同。现有 V2Planner 的仿真引用、回调、附着与接触许可状态均按单环境组织，需要对应的批量适配。
- 每环境规划 seed、候选数、搜索预算、物理超时与有效结果必须明确。批量大小或 slot 顺序不能被误当作新的试验身份，也不能把批次中尚未成功的问题直接当作任务失败。

因此原生 batch 有实现基础，但不是当前 `--station-workers` 的另一个开关。固定机器人与控制 profile、场景拓扑一致的局部任务更容易分组；不同规模场景可以分开成 batch，不必一次填入所有 ProcTHOR 房屋。

## 多房间场景会拖累哪些部分

当前 `Simulation.prepare_spec()` 将原房屋与机器人一起编译，`from_snapshot()` 恢复整个 model；没有按任务位置裁剪 MuJoCo 模型。缩小站位半径或相机画面不等于缩小物理场景。

| 链路 | 当前代码行为 | 多房间可能带来的成本 |
|---|---|---|
| 载入、编译、renderer 初始化 | 使用整套房屋的模型和资产 | 更多 mesh/texture、内存与固定启动成本 |
| 物理与额外 forward | 对完整 model 执行 mj_step/mj_forward | 更多动态自由度、远处物体的有效接触及约束；身体/几何派生量计算 |
| 接触事实与遥测 | 处理引擎产生的全部相关接触 | 远处物体与家具/地面接触也可能增加工作量 |
| 记录与初始化状态 | 每帧复制完整 qpos/qvel/ctrl，维护场景对象信息 | 无关动态对象也占状态复制、轨迹存储与初始化遍历 |
| 相机 | renderer.update_scene 接收完整模型，再渲染选定视角 | 图像中看不见不代表模型/资产不被载入，场景更新与资源成本仍可能增加 |
| cuRobo 世界导出 | 已按距参考底盘的 geom 距离筛选，范围为 `2*radius_m+1` | 仍遍历全部 geom；局部范围内或包围球相交的几何才导出，大 geom 仍整块导出 |

多房间的影响不能简单按房间数线性计算。远处静态几何会受 MuJoCo 的宽相位和 BVH 筛除，不等于所有 geom 两两做精确碰撞。真实收益取决于被去掉的动态物体、有效接触、自由度和资产规模。[MuJoCo 计算流程与碰撞检测](https://mujoco.readthedocs.io/en/3.5.0/computation/index.html)

历史整场景微基准确有 `mj_step=17.05 ms`、额外 `mj_forward=14.35 ms` 的样本，但没有对应局部裁剪模型，不据此承诺裁剪倍率。cuRobo 已经局部筛选，物理侧可能比规划侧有更直接的缩小空间；这仍需要分项计时验证。

## 建议的局部模型边界

每个任务提取一次，所有候选站位和 trial 共用同一局部模型。局部边界覆盖**全部站位及允许的底盘移动区域，再加机器人全身/手臂可扫及范围与必要余量**，而不是只围绕 TCP 或目标中心截一个小盒子。

保留目标、完整支撑面与相关结构、附近障碍物、必要地板/墙/门和影响训练相机视图的遮挡；按完整物体或结构子树保留，不截断桌腿/支撑关系。Panda 是单臂但当前允许局部移动底盘，局部场景不等于固定底盘实验。

尤其不能为每个站位分别删除它附近不同的障碍物，否则各站位测的不是同一任务。区域和保留对象在成败试验前确定，不按试验是否成功调整。裁剪后的模型是新的局部任务场景，需要重新构建有效初态并标定；不承诺它与原整屋的物理和视觉标签完全等价。

无需丢弃多房间来源：ProcTHOR 可继续提供目标、家具和布局，由其中提取多个独立的局部任务。按 room_id 取任务房间可作为较简单的第一步，再根据任务运动范围决定是否进一步缩小。

## 最小推进顺序

1. 保持原始 XML/资产只读，生成新的局部任务模型与冻结初态；不恢复已删除的旧 outputs。
2. 同一任务对比整屋与局部模型的载入、mj_step、额外 forward、接触、render 和规划时间，检查任务局部几何/支撑和执行约束。
3. 在局部模型上先测现有 1/2/4 worker，共享一张 GPU 时单独观察显存、GPU 等待和 CPU/磁盘争用，不预设 worker 等于 GPU 数。
4. 若规划/初始化仍为主项，先适配 cuRobo batch；先试小 batch，保持搜索预算和逐环境真实结果。
5. 若物理仍占主要成本且并行环境数足够，再评估 MJWarp。迁移后需重新验证和标定，不能直接沿用 CPU MuJoCo 的成败标签。

MuJoCo 的 `mujoco.rollout` 提供原生 CPU 线程池，但接口基于初始状态和预设控制序列；当前运行还包含闭环跟踪、控制反馈和每 4 ms 接触判定，不能直接替换成整段开环调用。[官方 rollout 说明](https://mujoco.readthedocs.io/en/3.5.0/python.html#rollout)

MJWarp 官方将其定位为高吞吐并行仿真，单环境低延迟与复杂场景不保证优于 CPU MuJoCo。因此先缩小模型再比较更符合当前局部任务目标。[MJWarp 官方说明](https://mujoco.readthedocs.io/en/3.5.0/mjwarp/index.html)

本轮没有新的裁剪或 batch 性能测量，也没有修改 planner 搜索、物理后端、trial 数和数据格式。旧 RBY 整屋的 8 worker 耗时估算不作为 Panda 局部 batch 的性能结论。
