# 站位质量图：场景 04 跨场景检验与选站对照

- 状态：done
- 日期：2026-11-23
- 时间戳：2026-11-23T14:10:08+08:00
- 关联：[方案 04](../plan/04-fixed-base-station-quality.md)、[构建与训练记录](2026-11-23-station-quality-build.md)、[场景编辑结果](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/val103-cup30-edits/reports/scene-edits/delivery/2026-11-23-val103-cup30-edits.md)

## 1. 场景与接入

场景 04 = `val103-cup30-edits` 的 seed-048：桌面加 Kettle_1（杯子东侧约 0.22 m），地面加 bin_27（杯子南侧约 0.31 m）。原定的场景 03 按用户指示改为 04，03 未采集。

- 接入：`scene.edit_scene: <场景构建 run 目录>`（`env/scene.py: add_edit_obstacles`）。从编辑快照的 `model.xml` 只取 `procedural_obstacle_*` 及其网格、材质、贴图，按编辑记录的放置位姿挂进原场景，再迁移状态。机器人 0.9 缩放、观测和策略不变。
- 图编码：Kettle 进入 tabletop 节点，bin 进入 ground 节点；每个合法站位 9–11 个节点（原场景 7–10）。
- 合法性重新筛选：1161 个名义位姿中合法 463 个（原场景 526）；新增 63 个 `home_penetration`，由 bin 占据底盘或与 Home 臂姿相交造成。

## 2. 零样本：原场景五头模型直接预测场景 04

冻结 s2r1 均值动作，在场景 04 采严格标签：463 位姿 × 16 抓法 × 5 个 σ 分层 × 1 次 = 37040 回合（GPU 4–7）。质量网络不重训，用原场景 `20261123-s3-fit-from-s2r1-rep2/quality.pt`。

| 指标 | 场景 04 全部站位 | 原场景 test（参照） |
|---|---|---|
| Brier / NLL | 0.186 / 0.553（基率 0.561） | 0.105 / 0.338 |
| 站位 Spearman（95% 站位重采样区间） | 0.71（0.64–0.76） | 0.92 |
| 站位平均偏差（预测−实测） | −0.13 | — |
| 成员标准差均值 | 0.184 | 0.045 |
| 站位绝对误差与 U 的 Spearman | 0.69 | — |

分箱可靠性（预测均值→实测）：0.02→0.08、0.15→0.50、0.25→0.63、0.35→0.75、0.45→0.77、0.55→0.77、0.65→0.74、0.75→0.77、0.85→0.82、0.93→0.89。结论：排序部分迁移，概率在中低段明显悲观；不可达站位仍判对。新障碍使输入偏离训练分布，U 整体放大约 4 倍且集中在预测出错的站位，可用于标未知区。

图：`runs/20261123-scene04-labels-from-s2r1-rep1-g4-7/station-quality.png`（按航向 Q_phys[Max]、U、实测；实测栏为全部抓法、σ 平均，与 Max 口径不同）。

## 3. 补入场景 04 的训练块后重拟合

原场景全部 84155 个标签 + 场景 04 训练空间块（XY 0.3 m 块，70/15/15）22878 个标签，五成员 bootstrap 重新拟合；在场景 04 的留出块上与零样本对比。

| 场景 04 留出块 | Brier | 站位 Spearman | 站位偏差 | 成员标准差 |
|---|---|---|---|---|
| calibration（59 站）零样本 | 0.142 | 0.85 | −0.107 | 0.177 |
| calibration 重拟合 | 0.097 | 0.96 | +0.004 | 0.044 |
| test（118 站）零样本 | 0.188 | 0.67 | −0.154 | 0.175 |
| test 重拟合 | 0.110 | 0.93 | −0.011 | 0.051 |

加入同场景部分数据后，留出块的校准与排序恢复到原场景水平。说明编码器能表示新障碍，缺的是训练场景覆盖。输出：`runs/20261123-scene04-refit-source-plus-train-blocks/`（`quality.pt`、`station-quality.json/png`、`refit.json`）。

## 4. 选站对照（方案 §8 ③）

- 地图：零样本 Q（第 2 节），即真正的跨场景选站。
- 排序分：**Max 汇总的 Q_phys − β·U，β=1**（按选抓为 Max 的 r_l 及对应 u_l）。滚动前保存 `selection.json`：前 8；中段三分位、末段三分位各随机 8；均匀随机 8；组间不重复。
- 执行：每站 20 回合，σ∼U(0,1)，20 个 σ 在各站共用（配对）；冻结 s2r1 均值动作、严格终止。均匀组另以均匀随机抓法跑同样 20 回合。

| 组（抓法） | 站数 × 回合 | 实测严格成功（站位等权，95% 站位重采样） | 模型预测 | 组平均 U |
|---|---|---|---|---|
| 前 8（Max） | 8 × 20 | **1.00**（1.00–1.00） | 0.97 | 0.012 |
| 中段（Max） | 8 × 20 | 0.78（0.54–0.97） | 0.67 | 0.384 |
| 均匀（Max） | 8 × 20 | 0.46（0.12–0.79） | 0.47 | 0.163 |
| 末段（Max） | 8 × 20 | 0.44（0.16–0.72） | 0.17 | 0.193 |
| 均匀（均匀抓法） | 8 × 20 | 0.46（0.12–0.80） | 0.36 | — |

| 差值 | 估计（95% 区间） |
|---|---|
| 前 8 − 均匀 | **+0.54（+0.20, +0.88）** |
| 中段 − 均匀 | +0.31（−0.08, +0.71） |
| 末段 − 均匀 | −0.02（−0.45, +0.40） |
| 均匀站位上 Max 抓法 − 均匀抓法 | 0.00（−0.04, +0.03） |

结论：Q 选出的站位在新场景实测 160/160 成功，比均匀选站高 54 个百分点，区间不跨零。低分端区分不足：末段组实测与均匀组持平，模型对它们预测 0.17，与第 2 节的中低段悲观一致。选抓：在均匀站位上 Max 与随机抓法没有差别，这 8 个站位的成败主要由站位决定。

**排序规则对照（离线补算，未另跑回合）**：按「均匀汇总 Q、β=0，U_uniform 高于 90% 分位（0.386，排除 47 站）不参选」，前 8 与本次前 8 重合 6 站；换出 S0188-y02（0.76 m）、S0189-y02（0.85 m，臂展边缘），换入 S0164-y02（0.50 m）、S0146-y01（0.53 m）。换出两站在采集标签中实测（全部抓法平均）0.91、0.94，前 8 组实测已达 1.00，未见边界点拖累，因此未追加均匀 Q 前 8 组。

## 5. 产物与命令

- 代码：`env/scene.py`（`edit_scene`、`add_edit_obstacles`）、`scripts/station_map/rl/quality_transfer.py`（`stations` / `collect` / `evaluate` / `refit` / `validate`）。
- 运行目录（`outputs/experiments/rl-station-quality/runs/`）：`20261123-scene04-labels-from-s2r1-rep1-g4-7`、`20261123-scene04-refit-source-plus-train-blocks`、`20261123-scene04-validate-zeroshot-g4-7`。

```bash
cd .worktrees/station-quality
export PYTHONPATH=$PWD/src LD_LIBRARY_PATH=/data1/wenyifan/opt/lib
R=outputs/experiments/rl-station-quality/runs
CK=$R/20261123-1009-s2r1-sigma-curriculum-12m-g2-7/latest.pt
E=/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/val103-cup30-edits/outputs/experiments/val103-cup30-edits/runs/seed-048
L=$R/20261123-scene04-labels-from-s2r1-rep1-g4-7
.venv/bin/python scripts/station_map/rl/quality_transfer.py collect --checkpoint $CK --edit-scene $E --gpu 4 5 6 7 --repeats 1 --output $L
.venv/bin/python scripts/station_map/rl/quality_transfer.py evaluate --checkpoint $CK --edit-scene $E --quality $R/20261123-s3-fit-from-s2r1-rep2/quality.pt --gpu 4 --output $L
.venv/bin/python scripts/station_map/rl/quality_transfer.py refit --checkpoint $CK --edit-scene $E --quality $R/20261123-s3-fit-from-s2r1-rep2/quality.pt --source-labels $R/20261123-s3-labels-from-s2r1-rep2-g2-7 --target-labels $L --gpu 7 --output $R/20261123-scene04-refit-source-plus-train-blocks
.venv/bin/python scripts/station_map/rl/quality_transfer.py validate --checkpoint $CK --edit-scene $E --maps $L/station-quality.json --beta 1.0 --gpu 4 5 6 7 --output $R/20261123-scene04-validate-zeroshot-g4-7
```

## 6. 遗留

- 只有一个新场景；跨场景泛化需在更多编辑场景上做留一场景检验（编辑库还有 01、02、05–10）。
- 零样本中低段悲观：多场景训练质量网络（或以 U 标未知区）是直接对策；控制策略仍只在原场景训练。
- 本节选站用 Max−1·U；首版建议规则（均匀 Q、β=0 + U 90% 阈值）在新场景的实测对照未单独跑。
