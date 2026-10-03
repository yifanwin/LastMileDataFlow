# 阶段二 case 共用层 + case1 / case1.5 真实交付报告

本报告对应
[阶段二四类 case 场景构建设计](../phase2/phase2-case-construction-design.md) 的落地，
完成 **case 共用层**、**2.1 距离困难（case1）** 与 **2.2 侧向差异（case1.5）**，
并交付两类 case 的**编辑前后图片**与**阶段三真实操作结果**。

分支：`worktree-phase2-case-shared-layer`（commit `4a17b9f`，基于 `main` 的 `1148af8`）。
按用户要求不直接改 `main`，需自行合并或挑选提交。

---

## 一、结论

| 项 | case1（距离困难） | case1.5（侧向差异） |
|---|---|---|
| 场景 | `procthor-10k-train/train_169`（实体场景，非 val_103） | `procthor-10k-val/val_103` |
| 目标 / 支撑 | `cup_05d16f89…_1_0_6` / `diningtable_a2b8eb55…_1_0_6` | `cup_ff2c6ab6…_1_0_2` / `diningtable_f113cf7f…_1_0_2` |
| 是否编辑 | 是（0.54 m 沿支撑局部轴，姿态保持） | 是（0.10 m 沿支撑局部轴） |
| 构建结论 | `scene_validity=valid`，`build_requirements=pass`，`case_condition=unknown` | 同左（11 项要求全 pass，其中 3 项为代理/测量标注） |
| 阶段三真实结果 | 1 次真实成功 + 3 次真实失败 | 近侧 2 次真实成功；远侧 6 条规划无解；窄侧 5 条几何过滤 |
| 阶段三判定 | `pass`（初始站位困难、其他站位物理成功） | `partial`（分侧差异已测，窄侧通行保持 `unknown`） |
| 反馈记录 | `case_verified` | `partially_verified` |

**两类的共同点**：全部是固定底盘独立试验，构建期**没有**任何"不可达/不可通行"的结论；
阶段三的未知部分保持 `unknown`，未被填成失败。

---

## 二、case 共用层

新增 `construction/cases/`：

- `base.py`：`Frame`（坐标系冻结进证据）、`make_requirement`、`requirement_summary`、
  `CaseConstructor` 四步接口（`preflight` / `generate` / `local_check` / `pending_hypotheses`）。
- `case1.py`、`case1_5.py`：两个新 case 的构造器。
- `legacy.py`：case2 / case3 按原规则行为搬入新接口，**要求顺序不变**（下游按 `requirements[0]` 读取）。
- `construction/generate.py`：case 中立的候选生成（支撑局部线取样 + 同量纲 `rank_evidence`）。
- `construction/legacy_candidates.py`：case2 / case3 的历史网格与惩罚排序原样保留。
- `construction/candidates.py`：按 case 分发；`templates.py` 只做兼容转发。

### 标注（设计 4.3 的落实）

每条要求都写 `strength` 与 `layer`：

- `strength`：`geometric_measurement` / `physical_evidence` / `geometric_proxy` / `model_semantic`
- `layer`：`scene_validity` / `case_intent`

`inspect_build` 改为 `valid = 无物理问题 且 所有**必需**要求 pass`，同时给出
`requirement_summary`（`engineering_failures` 与 `case_intent_failures` 分开计数）。
**失败 `case_intent` 不再被读成场景无效**——它正是编辑循环存在的原因。

### 坐标系规则

`Frame.of_support_region`（局部 x/y 在支撑面内）与 `Frame.of_body`（家具水平偏航 + 重力对齐 z）。
后者的必要性是实测出来的：THOR 餐桌 body 自身的 `xmat` 第三列并非世界 z，
直接拿家具原始旋转会让"侧向向量"的竖直分量变成倾角而不是高度；因此 `of_body` 显式取水平偏航并把 z 拉直，
`source` 记 `_gravity_aligned` 以免与原始 body 系混淆。证据里同时写 `side_vector_local` 与 `side_points_world`。

### 零编辑与编辑标记

`result.json` 与 `task_candidate.json` 都有 `edited` 布尔值。零编辑分支仍走完整验证、冻结与独立恢复，
`baseline` 只比较真正被编辑的对象。合成回归 `variant=1` 走的就是零编辑路径。

### 候选排序

去掉跨量纲 ×100 惩罚累加。新 case 用元组排序：`(越界量, 位移, 偏航, candidate_id)`，
量纲分离、可逐项核对。yaw 超出 ±3.14 的候选**过滤而不裁剪**。

### 观察包与 Agent 接口

- 观察包新增 `frames`（坐标系列表）与 case 专属示意图：
  case1 的候选分布图（支撑局部系，区分命中/越界候选、目标当前位置与冻结机器人底盘），
  case1.5 的家具局部系侧向图（三侧向量、位点、距离与净空标注）。
  两者都写 `diagnostic=true`、`schematic=true`、`vla_input=false`，并单独记 `method` 说明它是示意图不是相机观测。
- 新增 `rank` / `shortlist` 决策：只排序或取子集**程序已枚举**的候选，不给数值、不能引入新候选。
  `rank` 必须覆盖全部候选，`shortlist` 才允许子集。
- `VisionCallBudget` 按用途计数（`selection` / `ranking`），决策日志逐次写 `purpose` 与 `budget_remaining`。
- 规则降级显式记录：`degradation={path:'rule', reason:'vision_backend_unavailable', agent_calls_used:0}`，
  与 `budget_exhausted` 在数据中可区分。**本次没有外发授权，全部走 rule 路径。**

---

## 三、两个必测故障（设计 5.B）

| 故障 | 实现 | 回归 |
|---|---|---|
| case1 冻结初态被绕过，距离改用当前初态 | `FrozenInitialBase` 证据 + 必需要求 `distance_measured_from_frozen_initial_base`；不一致写 `measurement_source_inconsistent` | `test_frozen_initial_base_bypass_is_an_engineering_failure`（含"移动机器人后必须判 fail 且计入 engineering_failures"） |
| case1.5 侧向向量指向家具内部 | 逐侧三维前置检查（足印内外 / 高度 / 开放地面）；拒绝时不生成任何候选 | `test_case15_side_point_inside_furniture_rejects_before_any_candidate`（断言 `candidates==[]` 且 `preflight.json` 记 `candidates_generated=0`） |

另外新增 `test_every_requirement_is_annotated_with_strength_and_layer`、
`test_zero_edit_branch_is_explicit_and_reaches_full_validation`、
`test_edited_branch_is_marked_and_rank_is_same_dimension`、
`test_rule_fallback_and_budget_exhaustion_are_distinguishable`、
`test_rank_must_cover_all_candidates_shortlist_may_subset`、
`test_gateway_counts_ranking_and_selection_separately`、
`test_case15_per_side_verdict_never_promotes_partial_evidence`。

全量回归：**89 tests OK**（`PYTHONPATH=src python -m unittest discover -s tests`）。

---

## 四、case1：train_169 距离困难

### 布局（实测选定，不是估算）

- 冻结机器人初态 `[2.90, 3.10, 1.179]`（表西南侧，开阔，`initialize_robot` 直接判 valid）。
- 目标杯初始在桌面局部 `[-0.2216, -0.1968]`，到冻结底盘 **0.638 m**——对机器人是**从容可及**，不构成困难。
- 沿支撑局部 x 轴（世界 −y）移动 **0.54 m**（9 个 0.06 m 步距中的第 9 步），落点局部 `-0.7616`，
  到冻结底盘 **1.0965 m**，落入 `distance_range_m=[1.05,1.25]`。
- 支撑局部线在该方向可行走到 `-0.82`，`-0.88` 起越界（`placement_pose_keeping_orientation` 返回 `None`）。

### 阶段三（`case1-train169-v3`）

- 站位：`S000` = 冻结初态；`S003`/`S004`/`S005` 为粗采样；`S008`+ 为局部细化。
- **S000（冻结初态）3 条配置全部规划无解**（`finite_budget_dry_pregrasp`），构成难度见证。
- `S003`（细化后站位，h=0.369，grasp 700）**1 次真实成功**，`strict_pick` 通过严格力接触/抬升/保持验收；
  同站位 h=0.738 的两次真实执行为 `torso_protocol` 失败，`S011` 一次执行后规划无解失败。
- 结果：`successes=1, failures=3, planning_no_solution=16, geometry_filtered=36, not_tested=16`，
  `data_collection_complete=true`（`stopped=planning_budget`），20 次 attempt 全部通过审计。
- 判定 `pass: initial_configuration_difficult_other_station_physical_success`，
  反馈落 `outputs/feedback/05b53242….json`（`case_verified`，含成功见证与审计）。

**这是用户要求的"val_103 只能作为前置"的落地**：case1 的完整流程在同为 train 分区的
`train_169` 上跑通，`val_103` 只承担 case1.5。

---

## 五、case1.5：val_103 侧向差异

### 布局（实测选定）

家具系 = 餐桌 body 的水平偏航 + 重力对齐 z；足印局部 x ∈ [-0.736, 0.736]，y ∈ [-0.494, 0.494]。
三侧位点（floor level，局部 z = -0.3552 即世界 z ≈ 0.02）：

| 侧 | 局部向量 | 世界坐标 | 角色 | 施工期净空代理 | 到杯距离（编辑后） |
|---|---|---|---|---|---|
| `side_a` | (-1.397, -0.374, -0.3552) | (7.313, 2.949) | 近/开阔/可解 | 0.640 m | 0.750 m |
| `side_b` | (-0.547, 0.676, -0.3552) | (8.363, 2.099) | 窄 | 0.178 m | 1.055 m |
| `side_c` | (-0.547, -1.424, -0.3552) | (6.263, 2.099) | 远/开阔 | 0.900 m | 1.055 m |

前置检查 9 项全 pass（三侧各 3 项）；编辑前 `distances_m=[0.850, 1.050, 1.050]`，
`frame_bound_side_distance_difference` **fail**（差 0.20 < 0.30），符合"需要一次真实编辑"。
沿支撑局部 x 轴移动 **0.10 m** 后 `distances_m=[0.750, 1.055, 1.055]`，
距离差 **0.305 ≥ 0.30** 且净空差 **0.462 ≥ 0.35**，两条意图要求同时转 pass，事务提交。

> 净空度量换过三次：最初用 `geom_rbound` 包围圆，在厨房里被整面墙的包围球压成 0，
> 两侧都读 0 而无法分辨；改用"保守有向盒的水平面距离"仍被旋转几何的自身坐标系污染；
> 最终用**机器人站立体积内的水平射线扇**（0.05–0.80 m 六层、每层 24 方向取最小命中距离），
> 这才是"这一侧是否开阔"的合理几何代理。合成 fixture 也相应补了一面**地面高度的墙**
> （`barrier`），否则纯桌面场景下所有净空都退化为上限值、规则恒 fail，测试会掩盖真实行为。

### 阶段三（`case1_5-val103-v3`）

- `side_points` / `side_roles` 随站位配置冻结，`side_assignment.json` 记录每个站位归属哪一侧（半径 0.9 m）。
- 逐侧结果（分侧标签是划分输入，不是归因）：

| 侧 | 角色 | 状态 | trials | 成功 | 失败 | 规划无解 | 几何过滤 |
|---|---|---|---|---|---|---|---|
| `side_a` | 近/开阔/可解 | `succeeded` | 7 | **2** | 0 | 4 | 1 |
| `side_b` | 窄 | `geometry_filtered` | 5 | 0 | 0 | 0 | 5 |
| `side_c` | 远/开阔 | `no_solution` | 6 | 0 | 0 | 6 | 0 |

- 近侧两次 `strict_pick` 真实成功（`S006`、`S007`，h=0.738，grasp 794），12 次 attempt 全部通过审计。
- 整体判定 `partial: per_side_fixed_base_difference_measured_no_navigation_evidence`，
  `narrow_side_passage.status = unknown`（`navigation_not_available_in_phase_three`），
  反馈 `partially_verified`（`retain_per_side_evidence_without_overall_pass`）。
- **`case1_5_verdict` 不会把分侧的部分证据拼成 `pass`**：`record_feedback` 对 `partially_verified`
  显式拒绝在总判定为 pass 时使用，避免"部分见证被读成完整验证"。

---

## 六、编辑前后图片

均在 `outputs/builds/<id>/`（git 忽略，属运行产物）：

- case1：`observations/0000/`（编辑前）与 `observations/0002/`（编辑后静置），
  另加 `transaction_observations/0000-unsettled/` 与 `0000-settled/` 的成对图。
  含 `diagnostic_top` / `diagnostic_target` / `diagnostic_side` / `robot_head` /
  `diagnostic_distribution`（候选分布示意图：蓝点为命中距离区间的候选，红叉为越界候选，
  ★ 为目标当前位置，绿方块为冻结机器人底盘）。
- case1.5：同名结构，case 专属图为 `diagnostic_furniture_sides`（家具局部系三侧示意图）。

全部图像都标 `diagnostic=true`、`vla_input=false`，示意图额外标 `schematic=true`。

---

## 七、验证与已知限制

**已实际运行**

- 全量离线回归 89 tests OK。
- 两个真实构建（`train_169`、`val_103`）：真实加载、真实静置、真实事务、真实冻结与独立恢复
  （`independent_restore_exact=true`）。
- 两个真实阶段三 run：共 32 次 attempt，全部 `audit.valid=true`；真实 cuRobo 规划与真实 MuJoCo 执行，
  成功与失败证据同时保留（含交付视频）。

**限制（不得读成更强的结论）**

- **窄侧"能否通行"没有被验证**，也没有被否证：固定底盘试验不产生导航证据，阶段四之前保持 `unknown`。
  同理 `side_c` 只有"有限预算规划无解"，**不是物理不可解**。
- 构建期净空是几何代理（`horizontal_ray_fan_at_base_height_not_navigation`），
  射线扇仍是采样，不是任意形状的数学证明。
- 难度的"够不到"含义未被构建期断言：case1 只保证冻结初态距离落入配置区间，
  "原地困难、别处可行"由阶段三真实试验判定。
- 合成回归里的 `barrier` 与资产都是 fixture，不代表真实资产多样性；
  桌面案例只有两个房屋、两种杯。
- 本次**没有调用任何外部模型**（无外发授权），因此 `decision_source` 全程为 `rule`；
  Agent 侧新增的 `rank` / `shortlist` 只有合成回归覆盖，没有真实模型质量结论。
- case2 / case3 只做了接口搬迁与标注，规则阈值未改，**没有新的真实运行**。

**过程中遇到的问题（已解决）**

1. val_103 用 `--scene-xml` 会先 `resolve()` 软链到 NAS 缓存，而 `../../objects/thor/...` 只在本地资产树里存在，
   触发 `Error opening file ... .obj`。改用 `--house 103 --dataset-dir <本地 val 目录>` 即可。
   （`--scene-xml` 与 `--house` 互斥。）
2. 工程侧限制了机器人位姿只能在初始化阶段设置，因此 `index-dir`/`candidates` 等旧路径无法用来搬动机器人，
   只能通过配置冻结初态。
3. `4a17b9f` 里 `python3 -m unittest discover -s tests` 需 `PYTHONPATH=src`；单独跑某个测试文件需同时加 `tests` 到路径。

---

## 八、产物索引

| 类别 | 路径 |
|---|---|
| case1 构建 | `outputs/builds/case1-train169-v2/` |
| case1 阶段三 | `outputs/station_maps/case1-train169-v3/` |
| case1 冻结场景 | `outputs/scene_versions/57f1b373…` |
| case1.5 构建 | `outputs/builds/case1_5-val103-v2/` |
| case1.5 阶段三 | `outputs/station_maps/case1_5-val103-v3/` |
| case1.5 冻结场景 | `outputs/scene_versions/a84f154a…` |
| 反馈 | `outputs/feedback/{05b53242…, 46df5199…}.json` |
| 配置 | `configs/builds/case1-train169-distance.json`、`configs/builds/case1_5-val103-sides.json`、`configs/stations/case1-train169.json`、`configs/stations/case1_5-val103-sides.json` |
