# PandaOmron 低成功率：问题梳理与排查顺序

- 状态：done
- 日期：2026-10-27
- 替代：无；本报告补充既有统计，不改写历史实验
- 关联：[本体需求](../requirement/01-panda-omron.md)、[初步归因](../../../.worktrees/no-edit-calibration-val0-99/reports/no-edit-calibration/analyze/01-low-success-analysis.md)、[全量统计](../../../.worktrees/no-edit-calibration-val0-99/reports/no-edit-calibration/analyze/02-all-runs-trial-statistics.md)、[复用缺陷](../../../.worktrees/no-edit-calibration-val0-99/reports/no-edit-calibration/analyze/03-planner-reuse-target-binding.md)、[顺序对照](../../../.worktrees/no-edit-calibration-val0-99/reports/no-edit-calibration/analyze/02-cup30-reuse-order-experiment.md)

## 1. 本轮结论

当前至少有三个独立问题：**planner 跨任务目标绑定缺陷、规划球与物理本体几何不一致、采样范围及可达性口径问题**。另有执行跟踪问题需要在前两项修正后复核。

| 优先级 | 问题 | 当前证据强度 | 下一步要回答的问题 |
|---|---|---|---|
| P0 | planner 复用保留首次任务的 target/handle body id | 源码缺陷明确，已有顺序对照；本轮复核原始 trial | 修正绑定后，同一 Cup_30 队列是否恢复？ |
| P1 | `omron_base` 碰撞球包络明显大于 MJCF 物理底盘及支撑柱 | 本轮 CPU 几何核对确认包络差异；尚未逐失败站位建立因果 | goalset 拒绝中，有多少是球碰撞而真实几何不碰撞？ |
| P1 | 采样半径要求 2 m，但配方及加载器仍限定 1 m | 配方、加载器和既有 run 配置确认 | 扩圈后如何保持内圈可比较，并报告新增外圈不可达？ |
| P2 | 低把手/目标位姿不可达，升降被锁定 | 历史关联明确、锁定逻辑明确，尚无无碰撞 IK 消融 | 是本体真实工作空间限制，还是碰撞约束阻止接近？ |
| P2 | RoboCasa 执行参考跟踪超时 | 历史轨迹与错误记录已有证据 | 规划正确后，是实体碰撞、底盘跟踪还是参考转换问题？ |

不能把“最大约束代价落在底座”直接解释成“真实底盘一定撞家具”，也不能把所有低成功率归因于目标绑定。

## 2. 已明确的复用缺陷

`src/lastmile_dataflow/planning/curobo_v2.py` 的初始化会计算 `target_bodies`、`handle_bodies`；`rebind_resources()` 更换 sim/model 后没有重算这两组 id，随后 `refresh_world()` 用旧 id 筛选新 model 的几何。当前主工作区和实验 worktree 的该文件相同，此缺陷仍在。

本轮直接读取两个 run 的 `trials.jsonl`，确认最终口径：

| Cup_30 指标 | 单独运行 `exp-cup30-only-20261027-1542` | 队列末尾 `exp-cup30-last2-20261027-1600` |
|---|---:|---:|
| 站位 / trial | 20 / 60 | 20 / 60 |
| 成功 | 37 | 0 |
| 执行前无解 | 21 | 21 |
| pregrasp 跟踪超时 | 1 | 1 |
| lift 规划无解 | 1 | 0 |
| lift IndexError | 0 | 38 |

末尾组完整队列共 132 trial、2 成功；旧统计报告的 59 trial 是运行中快照，不能与最终 Cup_30 的 60 trial 混用。

这组对照尤其说明：**Cup_30 的执行前无解数量没有增加，变化集中在 lift。** 它支持复用缺陷导致执行成功崩塌，不能据此证明全量 goalset 失败也是该缺陷造成的。修复应保留 solver 分配复用，在绑定当前场景时刷新目标/把手身份；没有必要先全面关闭复用。

## 3. 本轮新增的几何证据

核对输入：

- 实际机器人资产：`/data1/wenyifan/lmdf/molmospaces_data/assets/robots/panda_omron/model.xml`。
- 规划模型：`src/lastmile_dataflow/planning/assets/panda_omron/model.urdf`、`spheres.yml`。
- 坐标适配：`runtime/control/panda_omron.py`、`planning/panda_omron.py`。

使用本仓库 `.venv` 编译该 MJCF，执行 CPU `mj_forward`，没有执行物理 rollout。以下均以底盘 yaw 枢轴 `base_site` 为原点、yaw=0，单位为米；球包络尚未加 0.002 m buffer。

### 3.1 底盘：视觉与物理接近，规划球明显更大

| 表示 | x 范围 | y 范围 | z 范围 |
|---|---|---|---|
| 编译后底盘视觉 mesh | [-0.338584, 0.358585] | [-0.248981, 0.248981] | [0.000720, 0.383280] |
| MJCF 底盘碰撞 box | [-0.340, 0.360] | [-0.250, 0.250] | [0.002, 0.382] |
| cuRobo 底盘部分 12 球 | [-0.420, 0.460] | [-0.340, 0.340] | [-0.110, 0.490] |

物理底盘约 0.70 × 0.50 × 0.38 m；规划底盘球的轴对齐包络约 0.88 × 0.68 × 0.60 m：后侧多 8 cm、前侧多 10 cm、左右各多 9 cm、顶部多 10.8 cm。

这提供了比“可能 mesh scale 错误”更具体的排查方向：**资产的视觉/物理底盘基本相符，差异主要出现在人工规划球近似。** 但包络大不代表球并集处处覆盖 box，也不等于每个邻近家具都发生假碰撞；必须在失败姿态处比较距离与接触点，不能直接整体缩球。

### 3.2 `omron_base` 还包含支撑柱，不只是地面底盘

`spheres.yml` 的 `omron_base` 共 20 球：12 个底盘球，另 8 个覆盖固定支撑柱。因此历史 trace 的 `omron_base` 主导也可能是柱体与台面/柜体冲突。

固定支撑柱 MJCF 为半径 0.12 m、半高 0.20 m 的圆柱，中心在枢轴坐标 [0.16, 0, 0.5]：

| 表示 | x 范围 | y 范围 | z 范围 |
|---|---|---|---|
| MJCF 固定柱 | [0.040, 0.280] | [-0.120, 0.120] | [0.300, 0.700] |
| 对应 8 球 | [-0.070, 0.390] | [-0.240, 0.240] | [0.240, 0.760] |

柱体球包络左右各多 12 cm，前后各多 11 cm。`panda_link0` 另有升降支撑球，也应在同一几何对齐检查中覆盖。重点检查窄柜前、桌边、低把手附近的具体 sphere index 和障碍 geom，而不只记录连杆名。

### 3.3 21 cm 原点差异目前有对应补偿

MJCF 的移动关节枢轴和 `base_site` 相对 `mobilebase0_base` 为 [-0.21, 0, 0]；导出时根平移 +0.21 m。规划使用 `base_site` 位置作为参考原点，URDF `omron_root` 再平移 +0.21 m。

CPU 核对：yaw=0 时，枢轴 [0,0,0]、base body [0.21,0,0]；yaw=π/2 时，枢轴仍为 [0,0,0]、base body 为 [0,0.21,0]。这与 URDF 枢轴后接偏移的约定一致，暂未发现简单的 21 cm 漏补偿。

现有 `check_fk()` 只比较当前 TCP，容差为 5 mm / 0.01 rad，不能证明所有碰撞球的原点、尺寸和移动轨迹都正确。后续需检查 base、支撑柱与球心在多个 yaw/底盘位移/升降高度下的一致性。

### 3.4 mesh scale 的当前判断与来源限制

实际资产的 71 个 mesh 均未声明非单位 scale；编译后视觉底盘和物理 box 尺度接近。URDF 主要提供运动链，机器人碰撞直接由 `spheres.yml` 给出，并非读取 URDF collision mesh。因此“URDF mesh scale 错”目前不是最直接的解释。

[robosuite 官方机器人文档](https://robosuite.ai/docs/modules/robots.html)说明机器人由机械臂、本体底座、夹爪和控制器组合，Omron 是移动底座；这只解释模型组合方式，不证明迁移后的资产已对齐。本地 RoboCasa `kitchen.py` 的 PandaOmron 初始关节和 torso=0 与当前 robocasa profile 一致。

本轮检查时 `reference_repos/robosuite` 未能作为完整源码读取，当前 `.venv` 也无可导入的 robosuite；未完成“上游原始 XML → 导出资产”的逐项比较。已完成的是实际使用资产与本仓库规划模型的对比，不将其扩大为真实硬件尺寸验收。

## 4. 安全膨胀需分层理解

| 项目 | 当前值/作用 | 判断 |
|---|---|---|
| 规划球形状 | 底盘球 r=0.21 m、柱体球 r=0.16 m | 已见厘米级包络差异，优先级最高 |
| `collision_sphere_buffer` | +0.002 m | 真正改变球半径，但明显小于当前形状差异 |
| `optimizer_collision_activation_distance` | 0.01 m | 优化代价激活距离，不能直接解释为 1 cm 硬碰撞门槛 |
| cuRobo metrics scene constraint | 本环境 `metrics_base.yml` activation=0、binary=true | 应结合成功标志/约束可行性判断，不把优化残余代价当穿透 |
| 导航软净空 | 0.02 m | 此标定 `require_navigation_clearance=false`，没有据此过滤站位 |
| 初始化物理检查 | 非自碰撞穿透超过 1 mm 会拒绝；地面另有规则 | 与 cuRobo 球检查使用不同几何，可能物理通过而球拒绝 |
| `clearance_base_m` | 栅格占据与底盘圆近似的净空 | 不是 MJCF 精确距离，也不是 cuRobo 球的距离 |

`robot_footprint()` 从几何计算包围圆，`task_geometry()` 又用场景占据栅格生成 clearance；whole-body 高度范围和支撑障碍投影也会影响该值。初步报告称该净空使用 0.35 m 半径，本轮源码并非统一固定该值；需按各 run 的有效几何核实，避免与 `local_base_radius_m=0.35`（规划位移限制）混淆。

场景碰撞导出使用逐 geom mesh/primitive，在测量底盘参考系下拼接，没有额外的家具整体 AABB 膨胀；底面被规划器排除。故底盘球伸到 z<0 本身不能直接解释地板碰撞失败，但过高/过宽部分仍可能碰柜体、台面等。

## 6. 建议的最小排查顺序

1. **修复目标/把手重绑定，并重跑现有 Cup_30 顺序对照。** 保持原站位与原参数，验证目标几何正确、空目标 lift 异常消失及真实成功恢复。不承诺修复后所有 lift 无解都消失。
2. **选少量代表性 goalset 失败站位做几何对照。** 包含柜前、桌边、低把手和已知成功站位；区分初始状态、IK 末态、轨迹中间状态。记录最近障碍 geom、球 index/位置/半径、MJCF 距离和球距离，确认是底盘、柱体还是机械臂。
3. **按实际几何修正规划球，再做局部消融。** 优先缩小无依据的形状外扩并检查覆盖；分别比较现有/修正球、2 mm buffer 的影响。若诊断临时关闭某碰撞项，只做离线规划归因，不执行该轨迹，也不记任务成功。
4. **落实 2 m / 0.25 m 新采样。** 保留覆盖范围和原始无解记录，分别报告采样通过、规划通过、已执行成功、基础设施错误；不要通过删掉低高度/困难样本制造成功率提升。
5. **最后复核残余不可达和跟踪超时。** 对低把手先做无场景碰撞 IK 与正常碰撞 IK 对比；对已规划轨迹检查底盘参考/实际位姿、yaw、关节误差及实体接触。历史高度阈值只是关联，不直接升级为全局硬过滤规则。

主 cohort 的 58,314 trial、2.88% 成功率和 73.55% 执行前无解沿用已有 16:20 报告快照，本轮未重新扫描全量。最大约束项的比例是排查线索，不是因果贡献比例；归因报告还注明其 trace 表采用较早扫描，不能与新快照逐行混算。

## 7. 本轮执行与交付

- 工作开始时主工作区和实验 worktree 均无已有 Git 改动。
- 阅读四份报告、相关采样/规划/控制代码及配置；核对两组 Cup_30 原始 trial。
- 确认核心 planner、碰撞导出、规划 URDF/球文件在主工作区与实验 worktree 一致。
- 使用仓库 `.venv` 完成实际 MJCF 编译、两种 yaw 的坐标核对、视觉 mesh/物理 box/规划球包络计算和格点计数。
- 交付本问题报告并更新报告入口；没有修改算法、实验配方、外部资产或既有结果，没有运行 GPU 规划或真实操作。仅文档变更，不运行无关全套回归。
