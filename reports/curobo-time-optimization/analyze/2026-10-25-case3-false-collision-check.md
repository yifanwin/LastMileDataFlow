# Case3「IK 收敛但约束不可行」是否为假失败：接触过滤归因

- 状态：done
- 日期：2026-10-25
- 替代：无
- 关联：[规划失败原因分布](2026-10-25-planning-failure-analysis.md)、[修复方案](../plan/04-case3-contact-and-grasp-fix.md)、[val100 正式实验交付](../delivery/2026-10-24-val100-experiment.md)、[运行目录](../../../../curobo-time-optimization/outputs/panda-time-val100-calibration-20261024-r01/)

针对"Case3 的 `IK 收敛但约束不可行`（1,453 trial）是不是 `gripper` 与把手接触被误算成碰撞"的核查。核查对象是 Fridge/Safe/SideTable/Microwave 四类共 639 个 Case3 站位的真实场景几何、规划器自己的碰撞球体，以及全部 `solver_trace.json`。

## 1. 结论

**是假失败，但方向要改：不是手指，是掌部。**

| 猜测 | 核查结果 |
|---|---|
| 手指碰把手被算成碰撞 | **不成立**。`panda_finger1/2` 已在 goalset/grasp 阶段从 target 检查器屏蔽（半径置 −100）。1,457 个站位的 `target_collision.selected_link_costs` 中从未出现 finger 链节。 |
| 把手和门体没拆开，碰把手 = 碰家具 | **成立，但被计费的是掌部 `panda_gripper`**。掌部不在接触白名单，它在目标位姿与把手（或门板）重叠 1.8–5.4 cm。 |
| 抓取点落在门板里 | **不成立**。抓取点在门外 2.1–2.7 cm。门板只在部分站位被掌部撞到。 |
| 只是 1 cm 激活带的边界效应 | **不成立**。掌部重叠中位 1.8–5.4 cm，是 `optimizer_collision_activation_distance=.01` 的 2–5 倍。 |

一句话：**抓取点取的是把手 bbox 中心，而 Panda 的掌面只在 TCP 后方 3.1 cm、指尖只到 TCP，把手沿接近轴却有 6–9.5 cm 深（个别 20 cm）。于是无论怎么选候选位姿，掌部都会进入把手或它后面的门板；掌部又不在接触白名单，target collision 永远无法满足。**

## 2. 机理

接触白名单：`planning/panda_omron.py` 的 `contact_links = ["panda_finger1", "panda_finger2"]`；`planning/curobo_v2_costs.py` 只在 `goalset/grasp/approach_contact` 阶段把这两个链节的球体半径置为 −100 后送进 target 检查器。掌部与腕部始终按普通碰撞计费。实跑记录一致：`planning/*-batch/planner_identity.json` 中 `target_contact_links = ['panda_finger1','panda_finger2']`。

抓取位姿来自 `scenes/tasks/discovery.py:grasp_candidates` 的程序化 pinch：`pose[:3,3] = 把手 bbox 中心`，再沿接近轴加 `approach_offset_m=0.01`。注意该偏移是 `goal += approach * offset`，而接近轴 +z 指向物体（`candidate_pool` 的 `score` 就是这么用的），所以当前 +1 cm 是**往里推**，等于把掌部再压深 1 cm。把 URDF 换算到 TCP 系：

| 量 | 数值（TCP 系 z） |
|---|---|
| 掌部球心 / 球面最前缘 | −0.077 / **−0.031** |
| 指尖球心 | **−0.001**（几乎就是 TCP） |
| 手指可用夹持带 | −0.031 至 −0.001，约 **3 cm** |
| 把手沿接近轴深度 | 6–9.5 cm（40 个站位抽样 p10/p50/p90），bbox 中心距前表面 3–4.8 cm |

即：把 TCP 放到把手几何中心，掌面（TCP 后方 3.1 cm）已经落在把手后段甚至门板上，而指尖只到把手的中截面——手指只能咬住把手后段。掌部球体对掌宽还有保守外扩（球模型横向覆盖 ±12.6 cm，实际掌宽 ±4.5 cm），所以球体模型给出的侵入比实际掌面更深。这就是为什么在合理接近方向下所有站位的掌部都是负余量（第 3 节）。

## 3. 量化（639 个可达站位）

候选筛选：只保留"接近轴水平（|z|≤0.5）且背离底盘"的候选（与 `candidate_pool` 的 `score` 同一语义）。对每个候选取"掌部∩把手"与"掌部∩门板"中较差的一个，再在候选间取最好——即"这个站位有没有任何一个合理候选能让掌部不碰东西"。下面的数字是最乐观的一侧。

| 类别 | 站位 | IK 到达位姿（每站位首条 trial） | 无可行候选（掌部必碰） | 该值 > 0.01 的站位 |
|---|---:|---:|---:|---:|
| Fridge | 410 | 314 | **410（100%）** | 0 |
| Safe | 100 | 34 | **100（100%）** | 0 |
| SideTable | 100 | 28 | **100（100%）** | 0 |
| Microwave | 20 | 18 | **20（100%）** | 0 |

"无可行候选"= 最优候选的 `min(掌部∩把手, 掌部∩门板)` 仍为负；"该值 > 0.01"= 掌部对把手和门板都超过 1 cm 激活距离。第二列为 0，说明没有任何站位存在这样的候选——规划器连激活带边界都够不到。

重叠深度（m，负数=侵入，取上述最优候选）：

| 类别 | min(掌部∩把手, 掌部∩门板) p10 / 中位 / p90 |
|---|---|
| Fridge | −0.051 / **−0.029** / −0.016 |
| Safe | −0.065 / **−0.054** / −0.050 |
| SideTable | −0.052 / **−0.025** / −0.020 |
| Microwave | −0.034 / **−0.034** / −0.034 |

碰撞对象拆分（改取"掌部对把手最优"的候选，仅用来看主要撞谁）：掌部∩把手<0 的站位为 Fridge 299/410、Safe 100/100、SideTable 80/100、Microwave 20/20；同一候选下掌部∩门板<0 的为 Fridge 215/410、SideTable 20/100，其余类别没有独立门体几何。也就是说**主力是把手，门板是次要但并集补齐到 100%**。

**在首条 trial 就有 seed 到达目标位姿的站位里，掌部与把手或门板重叠的比例是 314/314、34/34、28/28、18/18——100%。** 也就是说"位姿收敛但约束不可行"的每一个站位，在它实际到达的那个位姿上，掌部都处于碰撞状态；这不是搜索没搜到好 seed，而是目标位姿本身不可行。

（Safe 共 109 个站位，其中 100 个存在可用的水平背离接近候选；其余 9 个只有竖直或朝门内接近，未计入本表。）

## 4. 与旧归因的差异

[规划失败原因分布](2026-10-25-planning-failure-analysis.md) 的碰撞链（Fridge `gripper 100%、link7 98%、link6 97%`）统计的是 `selected_link_costs`，即 top-12 备选解的并集，其中包含没有到达目标位姿的 seed。按实际到达的目标位姿、并取最有利的合理接近方向复算，link7 与门板在 52% 的 Fridge 站位重叠（中位 −0.8 cm），掌部与把手或门板在 100% 的站位重叠。旧表把腕部的作用说重了，也把"掌部"和"把手/门板"混在一起了。

`omron_base` 在 Fridge 的 361/410 站位出现在 `target_collision` 里，但站位初始位姿下底盘与门板重叠的只有 74/410，其余是 32 个 seed 采样底盘平移时自己撞上去的搜索行为，不是目标位姿的固有障碍。

## 5. 修复方向

| 方案 | 做法 | 代价 / 风险 |
|---|---|---|
| A（先验证） | 把 `approach_offset_m` 改成沿接近轴**回退** 2–3 cm（当前 `+0.01` 是往里推 1 cm，方向与目标相反） | 一处符号/配置改动。抽样 3 个站位上每回退 1 cm、掌部余量约增加 1 cm，回退 2–3 cm 后掌部变为净空；代价是手指咬合变浅，需实测闭爪是否仍夹住把手 |
| B（推荐长期） | target 世界拆成"把手"和"门体"两份 checker，只对掌部放宽"掌部↔把手" | 需要第三份 `collision_world` 导出和第二个 target cost，改动集中但涉及成本管理器 |
| C（不推荐） | 直接把 `panda_gripper` 加进 `contact_links` | 掌部同时对门板免疫，可能穿门板；违反"只对把手放宽"的语义 |

A 与 B 可以叠加：A 解决"抓取点太深"，B 解决"掌部被当成不该接触的部件"。两者都必须用真实 rollout 验证，不能只靠本次几何复算宣布任务可成功。

> 2026-10-25 已按 A+B 实现，并追加收敛掌部球体（原球覆盖到 ±12.6 cm，实测 40/40 的掌部碰撞由最外侧球触发），几何复核见[修复方案](../plan/04-case3-contact-and-grasp-fix.md)第 8 节。

## 6. 核查方法与口径

- 数据：`../../../../curobo-time-optimization/outputs/panda-time-val100-calibration-20261024-r01/` 的 `trials.jsonl`、`planning/*-batch/solver_trace.json`、`planner_identity.json`；场景模型取 `../../../../curobo-time-optimization/outputs/scene_datasets/panda-time-val100-build-20261024-r01/scene-files/*/model.mjb`；站位状态取 `preparations/*/state.npz`。
- 抓取位姿按 `grasp_candidates` + `manipulation.grasp_goals` 的同一套构造复算（含 `approach_offset_m=0.01`、`grasp_roll_rad=π/2`、8 个候选）。
- URDF 换算：`T_tcp_gripper = Trans(0,0,−0.097)`（纯平移），因此手指闭合轴就是 TCP 的 x 轴，`grasp_roll_rad=π/2` 已把 x 轴对齐到把手窄轴，不存在姿态轴错位。
- 候选筛选：只保留"接近轴水平（|z|≤0.5）且背离底盘"的候选，排除从门内侧/竖直方向的不可达接近。竖直接近的候选掌部侵入更深（抽样 3 个站位，全部为负）。判据用"每个候选取掌部对把手/门板中较差者，再在候选间取最好"。
- 内外判定用每个碰撞 geom 的凸包半空间（与 MuJoCo 对 mesh 的凸化语义一致），按 `contype/conaffinity` 过滤；把手取 `handle_body` 子树，门体取 `target_body` 子树减把手；box/capsule 按 8 角点/采样点建凸包。不直接用 trimesh 的 `signed_distance`——这些碰撞网格是凸分解碎块、不封闭，符号不可靠。
- 口径限制：球体模型对掌部宽度有保守外扩；"MuJoCo 实际执行能否夹住并开门"未做真实 rollout，不能据此宣布这些站位可成功。Dresser（796）与 Desk（22）全部是位姿不可达，不在本节范围。

## 7. 复现

本次脚本放在 job 临时目录 `/home/wenyifan/.claude/jobs/3fff15e2/tmp/`（`pop.py`、`pop2.py` 全量站位几何；`reach_all.py` / `reach_min.py` 可选接近方向筛选与严格可行性判据；`allcand2.py` 8 候选抽样对比），随 job 删除而清理。第 6 节的方法描述足以直接重写；需要长期保留时把 `pop.py` 与 `reach_all.py` 复制到 `scripts/`。只用 MuJoCo + trimesh + scipy，不需要 GPU。
