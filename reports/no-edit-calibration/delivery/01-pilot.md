# P0 试点交付：5 房无编辑标定

- 状态：done（试点）
- 日期：2026-10-27
- 替代：无
- 关联：[需求](../requirement/01-requirement.md)、[实验方案](../plan/01-experiment-plan.md)、[计划表](../plan/02-schedule.md)

## 1. 范围与产物

- 来源：`val_0`–`val_4`，无编辑，PandaOmron，Pick/Open。
- 每任务最多 20 站，每站 3 次，8 卡，numerical 标定。
- 标定 run：`outputs/v1-no-edit/runs/no-edit-pilot-c000/`。
- 分析：`outputs/v1-no-edit/analysis/no-edit-pilot/`。
- 热图：`outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/`，209 张。
- 视频：`outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/`，19 条（11 个有效场景：8 个 S0+S1，3 个仅 S1）。
- state：`outputs/v1-no-edit/inputs/no-edit-pilot-state.json`。

## 2. 规模

| 项目 | 数值 |
|---|---:|
| house | 5 |
| accepted task | 209 |
| rejected task | 27 |
| station | 1,429 |
| 计划 trial | 4,287 |
| 有效 trial | 3,651 |
| 成功 | 115（3.15%） |

任务分类：全失败 163、全成功 0、[有效场景（有 S0+S1）11](#valid-scenes)、[有成功但无 S0/S1 配对 4](#success-no-pair)、不完整 31。

<a id="valid-scenes"></a>
### 2.1 有效场景（有 S0+S1）11

完整字段见 [failure_summary.json](../../../outputs/v1-no-edit/analysis/no-edit-pilot/failure_summary.json) 和 [target_success_rates.json](../../../outputs/v1-no-edit/analysis/no-edit-pilot/target_success_rates.json)；task_id 链到该场景的站位热图。

| task_id（链到热图） | 类别 | 操作 | 成功/有效 | 成功率 | S0 | S1 | 资格 |
|---|---|---|---:|---:|---:|---:|---|
| [`pick-bowl_847a24bfa9d8b1a1f26661ebbb850f56_1_0_2`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-pick-bowl_847a24bfa9d8b1a1f26661ebbb850f56_1_0_2-no_edit-v000/success_heatmap_stations.svg) | Bowl | pick | 23/51 | 45.1% | 7 | 5 | eligible |
| [`open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_8_3_DrawerMesh31_3`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_8_3_DrawerMesh31_3-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 3/21 | 14.3% | 5 | 1 | eligible |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_2_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_2_joint_0-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 6/33 | 18.2% | 6 | 1 | eligible |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 11/33 | 33.3% | 5 | 1 | eligible |
| [`open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_2_3_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_2_3_joint_0-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 3/45 | 6.7% | 13 | 1 | eligible |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_4_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val4-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_4_joint_0-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 7/27 | 25.9% | 3 | 1 | eligible |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_2_5_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_2_5_joint_0-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 11/45 | 24.4% | 8 | 1 | eligible |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_2_6_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_2_6_joint_0-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 11/48 | 22.9% | 7 | 2 | eligible |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_4_6_DrawerMesh011_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_4_6_DrawerMesh011_1-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 14/51 | 27.5% | 8 | 2 | eligible |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_4_5_DrawerMesh011_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_4_5_DrawerMesh011_1-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 14/39 | 35.9% | 5 | 2 | eligible |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_8_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val0-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_8_joint_0-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 5/27 | 18.5% | 5 | 1 | eligible |

<a id="valid-videos"></a>
#### 2.1.1 有效场景视频

- 每个 rollout 一条 `grid.mp4`，包含三路相机。
- S0 取该场景 S0 站位中第一条有真实执行步的失败 trial；若 S0 全部为 0 步规划失败，则没有轨迹，不生成视频，也不补造。
- S1 取该场景的成功 trial。
- 视频目录：`outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/`；11 个场景中 8 个有 S0+S1，3 个只有 S1。

| task_id | S0 | S1 |
|---|---|---|
| [`pick-bowl_847a24bfa9d8b1a1f26661ebbb850f56_1_0_2`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-pick-bowl_847a24bfa9d8b1a1f26661ebbb850f56_1_0_2-no_edit-v000/success_heatmap_stations.svg) | 无（0 步规划失败） | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000033/grid.mp4) |
| [`open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_8_3_DrawerMesh31_3`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_8_3_DrawerMesh31_3-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000278/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000275/grid.mp4) |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_2_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_2_joint_0-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000338/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000365/grid.mp4) |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000422/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000414/grid.mp4) |
| [`open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_2_3_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val1-open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_3-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_2_3_joint_0-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000437/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000441/grid.mp4) |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_4_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val4-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_4_joint_0-no_edit-v000/success_heatmap_stations.svg) | 无（0 步规划失败） | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r000791/grid.mp4) |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_2_5_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_2_5_joint_0-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001187/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001155/grid.mp4) |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_2_6_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_2_6_joint_0-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001200/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001218/grid.mp4) |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_4_6_DrawerMesh011_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_4_6_DrawerMesh011_1-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001398/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001381/grid.mp4) |
| [`open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_4_5_DrawerMesh011_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_0_5-chestofdrawers_6e3a4148a857a4222c764362660c370a_1_4_5_DrawerMesh011_1-no_edit-v000/success_heatmap_stations.svg) | [S0](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001432/grid.mp4) | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r001459/grid.mp4) |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_8_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val0-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_2_8_joint_0-no_edit-v000/success_heatmap_stations.svg) | 无（0 步规划失败） | [S1](../../../outputs/v1-no-edit/replays/no-edit-pilot-c000/valid-no-edit-pilot-c000-1793065557/r002983/grid.mp4) |

<a id="success-no-pair"></a>
### 2.2 有成功但无 S0/S1 配对 4

| task_id（链到热图） | 类别 | 操作 | 成功/有效 | 成功率 | S0 | S1 | 资格 |
|---|---|---|---:|---:|---:|---:|---|
| [`open-refrigerator_93040c07fdeb9b1b6ef21482e759a450_1_0_6-refrigerator_93040c07fdeb9b1b6ef21482e759a450_1_2_6_joint_0`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val4-open-refrigerator_93040c07fdeb9b1b6ef21482e759a450_1_0_6-refrigerator_93040c07fdeb9b1b6ef21482e759a450_1_2_6_joint_0-no_edit-v000/success_heatmap_stations.svg) | Fridge | open | 4/18 | 22.2% | 2 | 0 | no_high_rate_s1 |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_6_4_DrawerMesh031_2`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val4-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_6_4_DrawerMesh031_2-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 1/27 | 3.7% | 8 | 0 | no_high_rate_s1 |
| [`open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_4_DrawerMesh021_1`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val4-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_4-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_4_DrawerMesh021_1-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 1/27 | 3.7% | 8 | 0 | no_high_rate_s1 |
| [`open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_6-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_8_6_DrawerMesh31_3`](../../../outputs/v1-no-edit/runs/no-edit-pilot-c000/figures/val0-open-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_0_6-chestofdrawers_bcf35268e2027afed18909dcf850d56a_1_8_6_DrawerMesh31_3-no_edit-v000/success_heatmap_stations.svg) | Dresser | open | 1/39 | 2.6% | 12 | 0 | no_high_rate_s1 |

## 3. trial 级失败主表

| 阶段/原因 | pick | open | 合计 | 占有效 trial |
|---|---:|---:|---:|---:|
| IK (goalset) 位姿收敛但约束不可行 | 1,259 | 179 | 1,438 | 39.39% |
| IK (goalset) 目标位姿不可达 | 369 | 829 | 1,198 | 32.81% |
| TrajOpt (goalset) IK 有解、轨迹不可行 | 146 | 312 | 458 | 12.54% |
| `robocasa_reference_tracking_timeout` | 31 | 217 | 248 | 6.79% |
| success | 23 | 92 | 115 | 3.15% |
| 执行中途规划无解 (pose) | 80 | 0 | 80 | 2.19% |
| IK (approach) 位姿收敛但约束不可行 | 7 | 28 | 35 | 0.96% |
| TrajOpt (grasp) IK 有解、轨迹不可行 | 7 | 22 | 29 | 0.79% |
| 执行中途规划无解 (approach_contact) | 0 | 26 | 26 | 0.71% |
| `open_no_grip` | 0 | 10 | 10 | 0.27% |
| TrajOpt (approach) IK 有解、轨迹不可行 | 4 | 4 | 8 | 0.22% |
| `lift_contact_hold_or_slip` | 3 | 0 | 3 | 0.08% |
| `open_contact_lost` | 0 | 3 | 3 | 0.08% |

## 4. 目标类别成功率

- 有成功：Bowl 27/152（17.8%）、Dresser 90/1,543（5.83%）、Fridge 4/158（2.53%）、Vase 1/125（0.8%）。
- 零成功且达到排除阈值（≥30 trial、≥2 house）：AlarmClock、Apple、Book、CD、CellPhone、Cup、Egg、Mug、Newspaper、Pen、Pencil、Plate、RemoteControl、Safe、SprayBottle、Statue、TissueBox，共 17 类，已写入 state，后续 chunk 跳过。
- 其余零成功类别样本不足（<30 trial 或 <2 house），继续在全量中累积，达到阈值后再排除。
- 类别、task、station 三层明细见 `outputs/v1-no-edit/analysis/no-edit-pilot/target_success_rates.json`。

## 5. 异常与边界

- 53 条 `episode_error`：atomizer 类任务在 `lift_actual` 出现 `RuntimeError:IndexError:list index out of range`；按 unknown/基础设施单列，不计任务失败。
- 后续 run 的 trial 已增加 `exception_detail`、`attribution`、`retry_history`，全量可捕获完整 traceback。
- 规划无解、未执行和 unknown 保持 unknown；不补造 trial。
- 无编辑三相机可见性未测量；热图按站位成功率导出。

## 6. 全量交接

全量从 `val_5` 继续，复用本试点 state 与 17 个排除类别：

```bash
.venv/bin/python scripts/run_no_edit_pipeline.py \
  --recipe configs/scene_datasets/no_edit_val/remaining_005_999.json \
  --run-prefix no-edit-val-all \
  --state outputs/v1-no-edit/inputs/no-edit-pilot-state.json \
  --output-dir outputs \
  --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets \
  --gpu 0 1 2 3 4 5 6 7 \
  --first-chunk-houses 5 --chunk-houses 25 \
  --build-workers 32 --cpu-workers 160 --preparation-workers 32 --station-workers 8 \
  --planning-workers-per-gpu 2 --heatmap-workers 16 --min-trials 30 --min-houses 2
```
