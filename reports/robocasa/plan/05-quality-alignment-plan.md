# 站位操作质量估计：目标对齐与分阶段构建计划

- 状态：active
- 日期：2026-11-23 23:55 +08:00
- 替代：无（补充 04）；§1 σ 的停靠/初始臂姿成分与 §4 偏航扩展已被 [06](06-protocol-unification.md) 替代
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
- 2026-11-24 00:06：cup_3 解析抓法池 56 条（侧抓 48：16 方位×3 高度，开口 5.9–7.3 cm；杯沿捏抓 8），TCP=grip_site（+z 接近、x 闭合、开口 ≤8 cm）。原 Cup_30 库只用于 M0 val103 冒烟，不用于 RoboCasa。原生夹起验证待 M2。
- 2026-11-24 00:12：1.0 规划资产 assets_robocasa（0.9 版除以 0.9，141 球）。S0022 同关节下 link0→TCP FK 与原生误差 0.0 mm / 0.028°。URDF world 在 link0 后方 0.21 m，导出碰撞世界以原生 robot0_link0 减该偏移为基座。
- 2026-11-24 09:20：M2 cuRobo 评测器 B 接通（`planning/robocasa_curobo.py` 常驻规划进程 + `operation/robocasa_curobo_trial.py` 回合 + `scripts/lastmile_benchmark/robocasa_curobo.py` 批量）。全池 152 抓法 goalset 选抓，后段失败屏蔽该抓法重规划（≤3 轮）；手臂换原生 JOINT_POSITION 绝对输入（kp 200）跟踪，π0.5 仍用 OSC。cuRobo v0.8.0 两处坑：轨迹末端 JointState 喂回 plan_pose 会污染 IK 缓冲（之后全部 None），改为每次新建只含位置的状态；update_world 复用同名网格会沿用旧几何，每次换名。
- 2026-11-24 09:20：M2 小样 6/6 成功（far/left × S0019/S0022/S0025，名义停靠）：抬升 0.09 m、保持 1 s、无机器人–环境接触、跟踪误差 ≤0.0021 rad、壶位移 ≤0.03 mm；goalset 均选杯沿捏抓（148–150）。单 GPU 3 进程时每回合 18–64 s（规划 11–51 s，单进程 3.7 s），已限规划进程 4 线程。`runs/20261124-0915-m2-curobo-smoke`。按方法冻结前不开封的约定，全量 K=15 留到 M5。

- 2026-11-24 09:35：偏航扩展站表 `outputs/experiments/robocasa-station-eval/station-table/kettle-far__kettle-left-125mm/stations.jsonl`（`scripts/scenes/robocasa_station_table.py`）：40 XY × {0°, ±20°} = 120 候选，两场景初态均无接触的 118 个合法（0° 40、−20° 39、+20° 39），π0.5 子集 39（仅 0°）。cuRobo、B0–B2、Ours 统一用这 118 站；停靠误差按表行号取种子，两场景一致。驱动改读此表，S0022 ±20° × K=2 冒烟 4/4 成功。
- 2026-11-24 09:55：M5 统计与出图先行（`station_map/robocasa_metrics.py`、`scripts/station_map/robocasa_report.py`、`tests/test_robocasa_metrics.py` 4/4）。输入约定：方法分数 jsonl `{method, scene, station_id, score}`（Ours 的方法名固定为 `Ours`）；评测器 jsonl `{scene, station_id, success_rate}`（cuRobo 驱动的 stations.jsonl 即此格式）。输出 ρ、τ_b、Top-1/5、Regret、AUROC（S≥0.5）、Ours 对各基线的配对 bootstrap（10,000）与 Steiger z、far−left 配对差分、各偏航切片热图。合成数据自检通过（Ours/B1/B0 的 ρ≈0.95/0.43/0）。
- 2026-11-24 10:10：M2 π0.5 评测器 A 接通：`run_episode` 落位后 `lock_base()`（屏蔽平面命令、强制 arm mode），记录原始底盘命令峰值、底盘模式步数和漂移；新增固定站表入口 `robocasa_pi05.py --evidence <evidence> --scene-names ... [--stations ...]`（取各场景都合法且 π0.5 可见的站），汇总写 `stations.jsonl` 每行 `{scene, station_id, success_rate, n}`；`--free-base` 仅作对照。工作树需 `.venv-pi05 -> ../../.venv-pi05` 链接（未提交）。
- 2026-11-24 10:10：M2 π0.5 小样（far/left × S0019/S0022/S0025，1000 步）：seed 42 为 1/6，far 三站均未成功，按 plan 04 复核设置。不锁底盘对照（far，seed 42）2/3；但锁底盘下补 seed 43/44 后合计 9/18（far 5/9，left 4/9），S0025 两场景 1/6 且全部回合有机器人–环境接触。seed 42 的 far 0/3 属 seed 方差。另试“只清零底盘速度、保留模式位”为 8/18，与强制 arm mode 无差别且漂移更大（≤0.2 mm 对 ≤0.03 mm），未采用。π0.5 原始底盘命令峰值 0.28–0.74，S0025 底盘模式步数过半，执行时均被屏蔽。吞吐约 40 s/回合/GPU，正式 234 回合在 4 张卡上约 40 min。`runs/20261124-0919-m2-pi05-smoke`、`-0924-m2-pi05-forcearm-s4344`、`-0924-m2-pi05-keepmode`、`-0922-m2-pi05-freebase-ref`。

- 2026-11-24 10:55：按用户要求，成功判据改为参照 station-quality 的 `target_lift_height`：`robocasa-pick-bottom2cm-0.5s-v2`，目标最低碰撞顶点高出原始桌面（冻结静置态的最低点）≥2 cm，与原生双指抓握、脱离环境支撑同时连续保持 ≥0.5 s，20 Hz 判定。替代 v1（原点抬升 1 cm、保持 1 s）。cuRobo 与 π0.5 评测器、场景配置同步；此前 M2 小样按 v1 判，不进入正式结果。S0022 cuRobo 回合在 v2 下成功（静置最低点 0.920 m，最大抬升 0.0855 m）。RL 训练端需同步 lift_m 0.02、hold_s 0.5（最低点口径）。
- 2026-11-24 17:00：评测协议统一见 [06](06-protocol-unification.md)：全部方法用原生 PickJudge、7 DoF（torso 0.00 m）、40 个正对站位；RL 改用 rl-ppo 分档碰撞扣分与卡住终止；σ 只含感知与动作噪声；原生底盘按阶段锁定（新增 `unlock_base`），cuRobo 保留停靠扰动。此前的 M2 小样与 M4 checkpoint 不进正式结果。
