# 局部碰撞表示 A 阶段：柱体与机械臂底座球

- 状态：done（A1/A2 完成并写入生产球表；A3 底盘按用户决定保持 51 球，S0008/S0012 记为未解决的表示误差）
- 日期：2026-10-29
- 替代：无
- 关联：[实现方案](../plan/04-local-collision-representation.md)、[局部碰撞需求](../requirement/02-local-collision-representation.md)、[历史交付（已归档）](../archive/04-calibration-repair.md)

**固定柱、升降柱和 link0 底座改为可证明覆盖实体的球集（共 69 球，原 17 球）后，val1 Bowl 五个近站位的柱体假碰撞全部消除，S0022 冻结查询由 0/3 变为 3/3 规划成功；同时补上了原 link0 球漏掉的 6 cm 底座实体。代价是同求解次数下慢约 1.2 倍；对照 S0014 有 1 条 lane 因新放行的抓取候选求解失败而 3/3→2/3，已核实被拒状态实体真实相交，属于求解/候选选择问题。S0008、S0012 仍被底盘侧向约 3.6 cm 的外扩误拒，按用户决定本轮不改底盘。**

工作目录 `.worktrees/collision-handling`（基于 `405cd75`，改动未提交）。冻结输入、原始 trial 取自 `.worktrees/no-edit-calibration/outputs/no-edit-full-20261028/runs/c000-c009/no-edit-full-20261028-c000`（只读）。新产物在本 worktree `outputs/experiments/collision-handling/`。

## 1. A1：实际形状与坐标（S0022、S0008 冻结状态，独立未 begin 的仿真）

| 部件 | MuJoCo geom / body | 形状（规划 link 系） | 原球 |
|---|---|---|---|
| 固定柱 | `mobilebase0_g0_col` / `mobilebase0_fixed_support` | 圆柱 R=0.12，轴 x=−0.05，`omron_base` z∈[0.30, 0.70] | 8 球 r=0.16，侧向外扩 14.8 cm，端面 6.2 cm |
| 升降柱 | `mobilebase0_g1_col` / `mobilebase0_support` | 圆柱 R=0.1125，轴 x=−0.05，`panda_link0` z∈[−0.40, 0] | 8 球 r=0.15，侧向 13.9 cm，端面 5.2 cm |
| 机械臂底座 | `robot0_link0_collision`（按凸包碰撞） | AABB x∈[−0.154, 0.072]、y∈±0.095、z∈[0, 0.14] | 1 球 r=0.10，**漏覆盖 6.0 cm** |

- 由现有球反解的 link 坐标系与 URDF 换算一致（残差 1e-8 m）：`panda_link0` = `omron_base` + z(0.70 + torso)。
- 升降行程 0 / 0.17 / 0.34 m 处，升降柱相对 link0 的位置不变，绑定在 `panda_link0` 正确；规划锁定的 torso 值等于实际 qpos（0）。
- 两处不需单独覆盖的实体：固定柱 z<0.382 位于底盘 box 内；升降柱在 link0 系 z<−0.34 的部分在整个行程中都在更粗的固定柱内。两条均在行程两端与中点用采样核实。

## 2. A2：覆盖方法与候选

`scripts/experiments/n6_column_spheres.py`：

- 圆柱切成“半径带 × 扇区 × 层”的环形扇区网格，候选为各维连续区间，球心放在扇区角平分线上（c ≥ 0）。这种块离球心最远的点一定是 8 个角之一，因此所选块的并等于暴露圆柱，即可证明覆盖。只接受侧向外扩 ≤ β_side、暴露端面外扩 ≤ β_end 的块。
- 底座凸包切成方格，与凸包可能相交的格子都必须覆盖，候选为格对齐子块的外接球（同底盘做法），外扩按凸包面法向计。
- 候选去掉“仍可再扩一格”的被支配块，再用 `n4_chassis_spheres.select_cover`（从原 `block_cover` 抽出）的集合覆盖 ILP 选球。结果只是这组候选下的最优，不是全局最少球数。写入时按 0.1 mm 取整并相应加宽半径。
- 官方 `fit_spheres_to_mesh` 对底座凸包：MORPHIT 15 球仍漏 1.7 cm，VOXEL 漏 6–15 cm，不能直接用。

外扩均含 2 mm buffer；覆盖缺口 ≤ 0 表示完整覆盖：

| 候选 | 固定柱：球数 / 侧 / 端 | 升降柱：球数 / 侧 / 端 | 底座：球数 / 外扩 | 合计 |
|---|---|---|---|---:|
| 原球 | 8 / 14.8 / 6.2 cm | 8 / 13.9 / 5.2 cm | 1 / 漏 6.0 cm | 17 |
| s10（β 1.0/1.5/2.0 cm） | 69 / 1.2 / 1.7 | 47 / 1.1 / 1.7 | 95 / 2.2 | 211 |
| s15（β 1.5/2.0/2.5 cm） | 34 / 1.6 / 2.2 | 28 / 1.5 / 2.0 | 54 / 2.7 | 116 |
| **s20（β 2.0/2.5/3.0 cm，采用）** | 19 / 2.1 / 2.5 | 15 / 2.1 / 2.5 | 35 / 3.2 | 69 |

## 3. 七站初始状态几何（`p2_collision_diagnosis.py`，cuRobo FK 球对各 geom）

各部件对场景的最小规划余量（cm），负值即规划判碰撞：

| 站位 | 原球：固定柱 / 升降柱 / link0 | s20：固定柱 / 升降柱 / link0 | 实体固定柱到 `collision_1` |
|---|---|---|---:|
| S0022 | −1.3 / +0.0 / +7.4 | +8.8 / +9.0 / +8.0 | 10.3 |
| S0011 | −5.7 / −4.3 / +3.5 | +4.2 / +4.5 / +3.8 | 5.8 |
| S0008 | −4.5 / −3.1 / +5.5 | +5.9 / +6.3 / +5.7 | 7.4 |
| S0012 | −3.6 / −2.0 / +7.5 | +7.3 / +7.6 / +7.4 | 8.9 |
| S0010 | −6.1 / −5.0 / +0.2 | +1.7 / +1.9 / +0.6 | 3.3 |
| S0014（对照） | +1.5 / +2.3 / +6.7 | +8.5 / +8.5 / +7.0 | 9.9 |
| S0007（对照） | +10.4 / +11.3 / +17.6 | +19.1 / +19.7 / +18.0 | 20.7 |

- s10、s15 同样全部转正，余量略大；四个部件在七站真实状态下的覆盖缺口均 ≤ 0（最大 −0.04 mm）。
- 底盘不变：S0008 −1.7 cm（实体到 `collision_5` 1.63 cm）、S0012 −1.7 cm（实体到 `collision_4` 1.42 cm）、S0010 −0.5 cm（实体 2.86 cm）。贴近方向在 z≈0.25 m 处水平，即底盘**侧面**。

## 4. 冻结查询重放（`n1_family_reuse_check.py`，原 seed，常驻服务 + fresh，各独占一张 GPU）

常驻服务耗时（s）/ 规划成功 lane 数（每站 3 条）；fresh 结果与常驻一致：

| 站位 | 原球 | 只换柱体 | 只换底座 | s20（柱体 + 底座） |
|---|---|---|---|---|
| S0014（对照，先行作热身） | 17.8 / 3 | 45.0 / 2 | 21.1 / 3 | 57.0 / 2 |
| S0022 | 8.3 / 0 | 13.5 / **3** | 11.4 / 0 | 17.1 / **3** |
| S0011 | 5.5 / 0 | 8.1 / 0 | 7.7 / 0 | 9.5 / 0 |
| S0008 | 8.3 / 0 | 10.1 / 0 | 12.6 / 0 | 14.1 / 0 |
| S0012 | 7.2 / 0 | 7.3 / 0 | 9.5 / 0 | 9.6 / 0 |
| S0010 | 2.4 / 0 | 2.5 / 0 | 2.5 / 0 | 2.6 / 0 |
| S0007（对照） | 21.6 / 3 | 29.3 / 3 | 27.0 / 3 | 36.6 / 3 |

- 原球重放与原始 trial 完全一致（五站 0/3、两对照 3/3），重放口径可信。
- **S0022 的恢复来自柱体**，属于起点假碰撞的直接修复。
- **单次求解代价**：只换底座时求解次数与原球相同（S0014 均 6 次），耗时约 1.2 倍，即多出球本身的开销。
- **S0014 退化来自柱体**：seed 88 原选抓取候选 8 成功；柱体收紧后 goalset 选中候选 7，pregrasp、grasp 各重试 5 次仍无解，求解次数 6 → 22，耗时 2.5 倍。`n3_rejection_states.py` 显示被拒状态中实体真实相交（底盘侵入 `collision_5` 16.9 cm，固定柱侵入 `collision_1` 2.7 cm，link0 1.3 cm），拒绝正确。原因是收紧后放行了一个走不通的候选且流程不回退，属于 IK/轨迹求解与候选选择问题，不是表示误差，另行处理。
- S0011 起点已不被柱体拒，仍无解，尚未归因（需求文档记录其目标 IK 本就很少）。S0008/S0012/S0010 仍被底盘挡住。
- 只是同一冻结查询的规划结果，未做真实 rollout，不代表任务成功。

## 5. A3：底盘为何本轮不改

用 `n4_chassis_spheres.py`（新增 `--max-span`，35×25×19 网格、跨度 ≤ 12 格）生成更紧的底盘覆盖：

| 侧向上限 | 球数 | 侧 / 顶外扩（含 buffer） | 能放行 |
|---|---:|---|---|
| 现行 | 51 | 3.6 / 6.2 cm | — |
| 1.5 cm | 208 | 1.7 / 3.2 cm | S0010 |
| 1.0 cm | 355 | 1.2 / 3.2 cm | S0010、S0008、S0012（余量 0.2–0.4 cm） |

平面贴面时每个球能覆盖的面积约 $\pi\cdot 2r\beta$，外扩上限 β 减小时球数近似反比增长，底盘面积大，代价远高于柱体。用户 2026-10-29 选择保持 51 球：S0008、S0012 记为“底盘近似外扩造成的误拒，未解决”，不能解释为站位物理不可操作；之后再评估更局部的覆盖布局。候选结果保留在 `analysis/a3-chassis-c-s1{0,5}t30-20261029/`。

## 6. 改动与验证

| 文件 | 改动 |
|---|---|
| `src/.../panda_omron/spheres.yml` | 固定柱 8 → 19 球；`panda_link0` 9 → 50 球（底座 35 + 升降柱 15）；底盘与臂 link 逐字不变 |
| `scripts/experiments/n6_column_spheres.py`（新） | 柱体环形扇区覆盖、底座凸包块覆盖、官方拟合对比、写出 YAML 片段 |
| `scripts/experiments/n6_assemble_candidate.py`、`n6_split_candidate.py`（新） | 把结果写入球表副本；拆成只换柱体 / 只换底座的对照 |
| `scripts/experiments/n4_chassis_spheres.py` | 抽出 `select_cover` 供复用；`--max-span` 限制候选跨度（剪枝同样受限） |
| `scripts/experiments/p2_collision_diagnosis.py` | `PARTS` 按球心高度分组，随球数变化；新增各部件实体覆盖检查 `parts_max_uncovered_m` |
| `scripts/experiments/n1_family_reuse_check.py` | 每次 `execute` 记录 `elapsed_s`；共用 `run_dataset()`：运行随 outputs 搬迁后旧绝对路径失效时，在新 outputs 根下解析（p2 同用） |
| `tests/planning/test_panda_omron.py` | 新增柱体覆盖与外扩 < 3 cm 测试；换回旧球表时该测试失败（阳性对照） |

- `tests/planning` 74/74 通过；全量 398 项中 9 项失败（`tests/workflows` 的并行/进程用例），在未改动的代码上同样是这 9 项，与本次改动无关，未处理。
- 本 worktree 的 `.venv` 原先可编辑安装指向已删除的 `curobo-time-optimization` worktree，已用 `pip install -e . --no-deps` 改指本地 `src`。

## 7. 限制与后续

- 未做真实 rollout 和 Open 任务；耗时只来自 7 个 Pick 冻结查询，长 Open 预算影响尚未测。
- 柱体余量最薄处为 S0010 link0 +0.6 cm；如需更大余量可换 s15（116 球）。
- S0014 的候选选择问题、S0011 剩余无解、底盘侧向误拒是三个独立的未完成项。
- 下一步按方案进入 B：结构化局部环境。

## 8. 复现入口

```bash
E=outputs/experiments/collision-handling
RUN=/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/no-edit-calibration/outputs/no-edit-full-20261028/runs/c000-c009/no-edit-full-20261028-c000
SCENE=val1-pick-bowl_847a24bfa9d8b1a1f26661ebbb850f56_1_0_2-no_edit-v000
# 柱体与底座球（约 10 分钟，CPU；去掉 --skip-curobo-fit 时同时跑官方拟合）
PYTHONPATH=scripts/experiments .venv/bin/python scripts/experiments/n6_column_spheres.py --run $RUN --scene $SCENE \
  --station S0022 --beta-side 0.02 --beta-end 0.025 --beta-base 0.03 --output $E/analysis/<new>
# 七站几何（候选源码目录用 git archive HEAD src 导出后替换 spheres.yml）
CUDA_VISIBLE_DEVICES=<gpu> PYTHONPATH=<src>:scripts/experiments .venv/bin/python scripts/experiments/p2_collision_diagnosis.py \
  $E/inputs/val1-bowl-near7.json --output $E/analysis/<new>/initial-states.json
# 冻结查询重放
PYTHONPATH=<src>:scripts/experiments .venv/bin/python scripts/experiments/n1_family_reuse_check.py \
  --queries $RUN:$SCENE:S0014 $RUN:$SCENE:S0022 ... --output-dir $E/runs/<new> --gpu <gpu> \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets
# 底盘候选
.venv/bin/python scripts/experiments/n4_chassis_spheres.py --grid 35x25x19 --max-span 12 --beta-side 0.015 \
  --beta-top 0.03 --skip-curobo-fit --output $E/analysis/<new>
```
