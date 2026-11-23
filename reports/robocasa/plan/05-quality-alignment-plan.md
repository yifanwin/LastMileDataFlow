# 站位操作质量估计：目标对齐与分阶段构建计划

- 状态：active
- 日期：2026-11-23 23:55 +08:00
- 替代：无（补充 04）
- 关联：[04 首对实验构建计划](04-paired-station-experiment.md)、[Vahrenkamp 2013](../Vahrenkamp%20等%20-%202013%20-%20Robot%20placement%20based%20on%20reachability%20inversion.pdf)

## 1. 被估计量

$Q(q_b\mid s_t,\mathcal{G}_t,\mathcal{R})\in[0,1]$：固定底盘位姿 $q_b=(x,y,\theta)$ 下完成当前子目标的关节轨迹可解空间测度。假设：固定底盘操作、只评第一导航点；无视觉遮挡。

| 方法 | 对 Q 的近似 | 缺口 |
|---|---|---|
| B0 | 距离高斯 | 无运动学 |
| B1 | 抓取位姿单点扩展可操作度（ORM） | 无轨迹、无环境 |
| B2 | 接近/抓握/提起路点 ORM 取 min + IK/碰撞过滤 | 离散、无闭环 |
| Ours | 冻结 Actor 在扰动 σ 下的成功概率，σ 积分、抓法平均 | 受 Actor 能力限制 |

## 2. 性质与论证

- 性质 1（共同约束）：cuRobo 与 π0.5 两个异构评测器跑同一站表；far/left 配对差分检验 ΔQ 对 ΔS 的预测。
- 性质 2（秩相关）：ρ、τ_b。主论断 ρ(Q_ours,S) 显著高于 ρ(Q_B0/B1/B2,S)。
- 显著性预先固定：按站位配对 bootstrap（10,000 次）求 ρ_ours−ρ_B 的 95% 区间，不含 0 为显著；Steiger 检验复核。首对不显著则并入其余四个变体，不更换检验。
- 训练充分性：训练站位覆盖直方图、每站采样数、收敛曲线、开发站成功率分布（非常数）、质量头校准。

## 3. B1/B2 按论文复现

1. RD：链为升降 + 7 自由度臂；关节空间随机采样 + FK 落入 6D 体素；体素值取扩展可操作度（Yoshikawa × 关节限位惩罚 × 自距离惩罚），取体素内最大值。
2. 候选站位给定，按 Lazy ORM 逐站查询：$q_b^{-1}p_{grasp}$ 查 RD，等价于 IRD 切地平面。
3. B1：抓法池等权平均（另报 Max），叠加底盘无碰撞过滤。
4. B2：$\mathrm{ORM}_{tr}=\min_i \mathrm{ORM}_i$（论文式 3）；ORM>0 的站位按 Algorithm 1 逐路点 IK + 自/环境碰撞，未通过的抓法记 0。
5. 复现检验：FK 姿态可查回、报告采样数/分辨率/空体素率、ORM 地面切片定性对照论文 Fig.3/4。

## 4. 站位域调整

cuRobo 全域在每个 XY 上加偏航偏移 {0°, ±20°}（约 3 倍站位），检验 θ 维；π0.5 仍只跑 0° + FoV 门控子集。cuRobo K=15，π0.5 3 seed，B3 只作耗时参照。

## 5. 阶段与门槛

| 阶段 | 内容 | 门槛 | 可并行 |
|---|---|---|---|
| M0 | 合并 station-quality | 快照重载、PPO 一次真实更新 | — |
| M1 | 配对站表 + 锁底盘 | 漂移 ≤1 mm / ≤0.1° | 与 M3 并行 |
| M2 | cuRobo、π0.5 评测器 | 12 个小样回合、吞吐 | 依赖 M1 |
| M3 | B0/B1/B2 | 分数齐全、B0/B1 差分为 0 | 与 M1/M2 并行 |
| M4 | RoboCasa→mjlab、PPO、质量头 | 训练充分性报告 | 依赖 M1 |
| M5 | 正式评测与统计 | 指标、显著性、热图 | 依赖 M2–M4 |

## 6. M0 记录

- 2026-11-23 23:50：`76ae29f` 提交 plan 04；`8cd7f93` 合入 `feat/station-quality@468be71`（三处文档冲突，两侧保留）；`e8950c9` 去重 runbook 标题。
- 测试：规划层 62/62（主仓库 `.venv`，含 cuRobo）；RoboCasa 14/14（`robocasa-native/.venv-robocasa`）。
- 已知问题：`test_rl_rewards`/`test_rl_ppo` 12 个错误在来源 `468be71` 上同样出现，非合并引入，M4 前修复。工作树 `.venv` 无 curobo/rtree。
- 2026-11-24 00:01：M0 真实验证通过。原生快照重载 `runs/<ts>-m0-native-reload` all_passed（用 robocasa-native 原生构建 run；变体清单格式不被冒烟脚本接受，M1 处理）。PPO 正式入口 `train.py` 8 env、4096 transitions、16 次更新，写出 checkpoint（`runs/20261123-2358-m0-ppo-train`）。
- 运行须知：两环境都要 `LD_LIBRARY_PATH=<venv>/lib/native`（否则缺 libEGL）；抓法库从 station-quality 复制到同路径（运行产物，不提交）；`tests/smoke/rl.py` 与当前观测接口不符（`GraphObservation` 不收 `flat`），已过时。
- 2026-11-24 00:03：M1 锁底盘 lock_base/mask_base_action；kettle-far S0022 200 步强底盘命令下漂移 0.28 mm / 0.038°，臂与升降正常。
