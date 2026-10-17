# PandaOmron 首期实施方案

状态：draft  
日期：2026-10-17  
替代：无  
关联：[需求与验收](../requirement/01-panda-omron.md)、[方案编制结果](../delivery/phase_01/2026-10-17-panda-omron-plan.md)

## 1. 目标与实施顺序

**交付由 robosuite factory 生成的整机、薄机器人接口、物理控制与相机、cuRobo V2 对齐，以及固定场景固定站位的一次真实 pick。** 本方案尚未执行，文中入口、配置及参数均为拟实施内容。

```text
环境/固定输入 → factory 整机 → 薄接口与 stepping → 控制/相机
→ pivot 独立校验 → 随机 TCP/FK → collision 对齐 → 固定站位真实 pick
```

升降保留，初始高度默认 0.2 m；进入执行后保持该目标。固定站位 pick 在规划和控制两侧锁定底盘与升降。站位采样、S0/S1、A*、生产 recorder/dataset、open、MolmoBot 和 benchmark 不进入首期。

## 2. 动作与接口设计

使用一个小型语义命令结构，不指定扁平长度；`arm_q` 与 `gripper_width` 按实际 arm id 对应。初始化参数、期望目标、实测反馈分开保存。

| 字段 | 首期明确的语义 | 适配规则 |
|---|---|---|
| `base_xyyaw` | 世界坐标下底盘物理参考点的绝对 x/y/yaw，m/m/rad | 接口统一位姿语义，适配器转换到源关节；yaw 跨界使用连续角目标 |
| `lift` | 升降关节绝对位置，m | 初始化时设置，之后锁定；不同于 RBY-1 躯干联动参数 |
| `arm_q` | 按声明关节顺序给出的绝对角度，rad | Panda 对应单臂 7 个关节，不补另一条臂 |
| `gripper_width` | 两指总开口，m | Panda 目标为 `[w/2, -w/2]`，实际宽度为两关节位置之差 |

薄接口只提供 `state / hold / base / arm+gripper / cameras`。固定元信息包含可用臂、关节顺序、TCP、手指接触部件、真实相机和挂载；不在每个采样中重复复制。`hold` 锁存目标，之后不因反馈漂移改写基线。组装/创建入口集中选择本体；共享执行、规划和判定不分支判断机器人名称。

Simulation 仍负责 `begin/step/close`、物理时钟和资源关闭；适配器负责目标与 ctrl 映射。旧 RBY-1 数组协议保留在原适配边界，新增语义路径不改写已有 position/native 行为。native 夹爪 motor 值不能直接当作宽度，也不在本期编造通用的宽度到力矩映射。RBY-1 未具备的 lift 不伪造为零，其原有 torso 参数保留明确单位。

## 3. 改动落点

下列为预计落点；能在原模块表达的逻辑不额外拆包。

| 落点 | 改动内容 |
|---|---|
| `config.py`、`configs/robots/panda_omron.json` | 新增 Panda 专用参数与配置创建入口，区分本体字段；保留 RBY-1 默认值与旧配置读取 |
| `runtime/control/interface.py`（新增） | 小型语义命令和薄接口契约，不引入注册框架或训练维度 |
| `runtime/control/robot.py`、`panda_omron.py`（新增） | 在创建入口选择适配器；原 RBY-1 适配器继续存在；Panda 封装装配、控制、坐标与部件映射 |
| `runtime/simulation.py`、`scenes/geometry.py` | 经接口准备模型、创建适配器、下发命令和查询 TCP/相机；消除首期路径对 fixed_head/20D 的强制依赖 |
| `planning/panda_omron.py`（新增）、`planning/curobo_v2.py` | 提供 Panda 规划描述；用描述传入关节名、TCP、锁定组、坐标转换、接触与附着 link；共用 V2 求解链 |
| `planning/collision_world_v2.py`、`curobo_v2_costs.py`、`head_fov.py` | 参考系和接触链由描述提供；固定底盘时不构造依赖活动 base_x/base_y 的移动 workspace 代价；关闭 FOV 时不读取头部定义 |
| `runtime/execution/context.py`、`manipulation.py` | 提取必要的语义目标跟随与固定站位 pick 调用，继续通过 Simulation stepping；不另写 Panda 执行器 |
| `runtime/outcomes/pick.py`、`evaluation.py` | 使用实际 TCP/指尖和存在的保持组；共享抓取物理判据，RBY-1 保留原协议检查 |
| `tests/runtime`、`tests/planning`、`tests/smoke/panda_omron.py`（新增） | 相关离线回归；一个真实入口分阶段驱动整机、控制、对齐和 pick，不新增生产 CLI |

Panda 首期不经过 `ContinuousContext` 当前强制绑定的 20D recorder；将所需控制/采样原语整理成共享函数，旧上下文和新 smoke 薄编排共用。测试脚本只负责输入和阶段编排，不复制轨迹跟随、接触判定或 V2 求解算法。快照辅助状态若触及该路径，通过适配器提供；不扩展跨本体或跨版本快照兼容。

## 4. 分步实施与完成条件

### S0：环境与固定输入

使用项目 `.venv`，按现有安装脚本准备 Python 3.11、MuJoCo 3.5.0、cuRobo V2 0.8.0。factory 生成阶段显式指定本地 robosuite 源码目录，生成所需依赖声明在项目中；不借相邻工程环境或持久写入相邻路径。GPU 执行前查看实时利用率，显式传设备编号，不固定占卡。

选定一个已有室内场景、一个有匹配 grasp 候选的杯子、一个人工指定且空间充分的底盘位姿。保存 case 配置：源 XML/metadata、目标 body、候选来源、初始 base/lift/arm/width、seed 和相机参数；不读取旧 RBY-1 站位标定。若选定输入不可用，先补齐该输入，不转入场景采样或批量搜索。

### S1：factory 组合整机

按 `create_robot("PandaOmron") → add_base → update_joints/actuators → gripper_factory → add_gripper` 生成整机。先在项目版本中编译并短时 stepping，再从同一组合结果生成可独立加载的 XML/网格资产。生成器与模型准备函数共用装配逻辑；运行时不导入 molmospaces。

保留源 joint/actuator/TCP 名称，外加统一 namespace。生成资产放 `data_root` 下 `assets/robots/panda_omron/`，URDF/规划配置同属该资产目录；源码不提交大型网格。导出后重载整机验证引用，后续 pick 使用该来源模型。此步验收整机结构、初态与 stepping；三个 XML 分别编译不计通过。

### S2：薄接口、物理控制与相机

实现 Panda 位置伺服，按原 motor 限幅保留力限制。参考本地已有伺服参数起步；源底盘/升降的大静摩擦与位置伺服不匹配时，采用参考实现中的关节 frictionloss 处理，保留真实几何接触摩擦。先验证保持，再依次执行底盘平移/转向、臂多关节目标、夹爪开闭/中间开度和组合运动中的 lift-hold。

统一命令路径写入 ctrl，经 `Simulation.step` 执行；控制采样返回实际位姿、宽度和 lift。首次 hold 取得基线，后续保持沿用锁定值。保留 factory 腕相机，整机视角使用真实相机挂载；录制整机和腕相机画面，碰撞组的显示处理仅影响渲染。

### S3：pivot 与 TCP/FK 三项硬验收

先独立验证枢轴，再将 URDF/YAML 转成 V2 描述并比较 FK。参考 URDF 含根偏移 `+0.21 m`；适配时只转换一次，不能把 MuJoCo 根、底盘枢轴和世界原点混用。

| 项目 | 实施方式 | 本方案建议通过值 |
|---|---|---|
| factory 整机 | S1 的完整装配模型，在目标环境重载、stepping，并贯穿后续验证 | 整机可运行，关节/执行器/相机对应有效，无 MuJoCo 数值告警 |
| pivot 独立检查 | 在原点及一个非零平移下分别检查 yaw=0/90/180°；固定臂和 lift，直接由源几何计算预期刚体变换 | 参考点不随原地转向平移，根/TCP 世界位置残差 ≤1 mm、旋转残差 ≤0.001 rad |
| 随机 TCP/FK | seed 固定，20 个合法随机姿态覆盖 arm、base 平移与 yaw；scratch MjData 与 V2 使用相同 q、同一 TCP 和参考系 | 目标：位置 ≤2 mm、旋转 ≤0.005 rad；允许最大值：位置 ≤5 mm、旋转 ≤0.01 rad；超过最大值判模型/坐标未对齐，报告逐项、目标达成情况及最大值 |

设局部 pivot 为 `p=(-0.21,0,0)`、世界 pivot 目标为 `b`、偏航旋转为 `R`，直接检查 `t_root=b-Rp`，固定臂时任意根局部点满足 `x_world=b+R(x_local-p)`。这一独立计算不调用 URDF 适配变换。FK 旋转误差使用相对旋转角；有效但自碰撞的随机关节姿态仍可用于纯 FK，不算可执行轨迹。

### S4：V2 碰撞与固定基座规划

转换参考配置的 `ee_link/retract_config` 等旧字段，明确 V2 `tool_frames/default_joint_position`、关节顺序和锁定值。完整描述保留 base/lift 链；固定 pick 只释放臂关节，不把活动规划关节数量解释成训练动作维度。先用完整链做随机 FK，再用锁定后的配置复核固定站位 FK。

参考 URDF 没有可动手指分支，必须按源 MJCF 补充两个指关节、对应碰撞 link/spheres 与锁定开度；覆盖开爪、闭爪两种状态及掌部、升降立柱、底盘。将球叠加到 MuJoCo 视图，检查坐标、尺寸和明显漏覆盖/外扩，并用少量有明确几何关系的分离/接触姿态对照两侧碰撞结果；不要求近似球与网格距离完全相等。

沿用现有逐 geom 场景转换和目标独立碰撞查询。允许的接触只作用于当前目标与手指，掌部、环境和自碰撞仍检查；不能剔除起始重叠障碍来获取成功。闭爪后按实际宽度更新锁定值与碰撞状态；抬升阶段携物几何只附着到规划模型，MuJoCo 中不 weld 目标。

### S5：固定场景固定站位真实 pick

复用任务目标与 grasp 候选，V2 执行 pregrasp → approach → close → lift → hold；语义控制跟随按实际关节名排序，使用现有轨迹重定时逻辑。每个阶段读取反馈，规划初态以实际关节状态构造。base/lift 在计划和控制中均锁定，不以改宽限位或传送修复执行误差。

闭爪后确认双指真实承力，再从实测状态更新规划并抬升。`PickMonitor/evaluate_pick` 提取公共物理判据和本体保持约束，按实际手指接触子部件统计力，不伪造 head/idle_arm 字段。公共判据继续检查双指、无支撑、抬升、保持、滑移及物理采样连续性。

至少一次 success 须来自同一真实连续 attempt。单次失败按当前报错定位并用新 attempt 重试，不预设大批低概率分支；成功与失败均保留该 attempt 的原始结果。未成功则报告实际失败阶段，首期不能判完成。

## 5. 数值与软件验收

以下数值为方案拟采用值；实施前写入明确的验证配置，执行时不为通过临时放宽。几何对齐与物理伺服误差分开判断。

| 验证 | 通过标准 |
|---|---|
| 无负载目标跟踪 | 每项预设运动在 5 s 内到位；base XY ≤5 mm、yaw ≤0.01 rad，arm 最大关节误差 ≤0.02 rad，夹爪总开口误差 ≤2 mm；闭爪抓物不要求达到空载零宽度 |
| 升降保持 | 保持及各项运动中，相对锁定目标的最大高度误差 ≤2 mm；报告全程最大值 |
| 固定 pick 保持 | base 相对基线平移 ≤2 mm、yaw ≤0.2°；lift ≤2 mm；全过程连续 stepping，无复位或传送 |
| 真实抓取 | 双指承力、目标无支撑、抬升 ≥5 cm，连续保持 ≥2 s；沿用现有 1 cm/5° 相对滑移约束及物理采样间隔要求 |
| RBY-1 回归 | 受影响的 position/native 动作、Simulation、规划顺序、相机与 pick 判定测试通过；最终跑现有离线回归 |

测试聚焦语义到 ctrl 的实际映射、保持目标不漂移、接口下两种本体行为、规划锁定组和任务判据。真实 smoke 不由 unittest 自动触发；import、mock 或软件回归不能代替三项硬验收和真实 pick。

## 6. 入口、产物与交付顺序

拟用一个 `tests/smoke/panda_omron.py`，提供 `factory / control / align / pick` 阶段及显式 `--robot/--case/--gpu/--output` 参数；factory 阶段再接收本地 robosuite 根目录。各阶段创建新 Simulation，失败重跑新输出目录；align 的随机状态写入 scratch data。

每个阶段仅保存有效配置、seed、源资产引用、一个主结果 JSON、必要数值采样，以及确有用途的相机画面/视频。真实 pick 需要逐物理步的任务事实，采用现有采样器加轻量写出，不启动生产 AttemptRecorder 或物化数据集。产物放 `outputs/diagnostics/panda-omron/<run>/`；报告只链接结果，不复制运行目录。

按 S1 整机、S2 控制、S3/S4 对齐、S5 pick 顺序交付；前一项失败先修当前问题，不越过到批量采集。完成后写 `reports/panda-omron/delivery/phase_01/` 实施结果，长期运行命令放 `reports/panda-omron/docs/`，关键坐标/接口决策只有在实际确定后才写 `reports/panda-omron/decisions/`。通过首期后，另定 station sampling → S0/S1 → A* → 连续 pick → recorder/dataset 的实施方案。
