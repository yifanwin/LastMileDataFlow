# Cup_30 移动底盘：合并与奖励统一

- 状态：active
- 日期：2026-11-19
- 时间戳：2026-11-19T22:20:47+08:00
- 关联：[移动底盘失败排查与修复](48-mobile-base-failure-diagnosis.md)、[移动底盘2M短训](49-mobile-delta-fail-2m-short.md)、[Cup_30分类碰撞](cup30-typed-collision-training.md)、[best窗口](2026-11-19-best-checkpoint-window.md)
- 工作区：.worktrees/rl-mobile，分支 codex/rl-mobile

## 来源

- 基底：codex/rl-cup e389498（真实物体Cup_30、分类碰撞、卡死终止、掉落罚分、对称对齐、best窗口）。
- 合入：codex/rl-cube-random-stations 63a9e9e（底盘相对实测位姿增量、超时bootstrap、failure罚分与连续碰撞上限），以及该工作区未提交的评测一致性检查改动（排除 `evaluation` 段）和报告49。原工作区未改动。
- 冲突只在 `env/terms.py`：掉落判定与连续碰撞上限并存；reset列表两边字段都保留。超时bootstrap两边是同一修复，自动合并。
- Cup_30候选库 `outputs/experiments/grasp-fps-all/runs/cup30-panda09-rays21-ratio30-hand3mm-sym-fps128` 从rl-cup复制（不进git）。

## 奖励统一

合并后失败有四种来源：卡死（Cup，−10）、掉落（Cup，−20）、越界（移动）、连续碰撞超限（移动，可选）。原cube的 `reward.failure` 对所有失败都罚，直接合并会让卡死、掉落叠加两次。

改为：`failure` 只罚没有专门罚分的失败——卡死照付 `stuck.penalty`，配置了 `drop_penalty` 的掉落照付掉落罚分，二者都不再付 `failure`。cube移动配置没有 `drop_penalty`、不用分类碰撞，行为不变；Cup固定底盘配置没有 `failure`，行为不变。

新配置 `configs/station_map/rl/val103_cup30_random_stations_mobile.yaml` 相对Cup固定底盘配置只改四处：

| 项 | 值 | 理由 |
| --- | --- | --- |
| `scene.fixed_base` / `action.mobile_base` | false / true，0.03 m、0.15 rad | 沿用cube移动已验证的增量尺度 |
| `sac.target_entropy` | −10 | 10D动作，与cube移动一致 |
| `reward.failure` | 20 | 越界一次性罚分，与掉落−20持平；卡死约−10加窗口内碰撞约−10。各类失败都在−20附近，越界不会比退开更划算 |
| `collision_limit_s` | 不设 | 持续碰撞已按每步0.5计价并由卡死终止兜底；分类碰撞下的“碰撞”含手指外侧轻碰杯，按时长终止会误伤正常抓取 |

碰撞：底盘或车身碰桌归入 `robot_scene`（大档，首次1.0、持续每步0.5），取代cube移动的每步−1。

## best.pt

沿用Cup配置：满200个非预热回合、累计60万条健康经验后才比较，严格提高才覆盖。移动底盘最长回合也是1000步，门槛计算（20000+512×1000）同样适用。

限制：报告49发现移动底盘的训练期窗口滞后（回合460–1000步，回合结束时Actor已更新1800–4000次），best.pt 2/80，latest 64/80。窗口加长到200回合不能消除这一滞后。正式训练后应同时独立评测 latest.pt 和 `ckpts/` 中的周期checkpoint（每40万条），再定最终模型。

## 验证

- `tests/station_map` 149项通过（新增：已有专门罚分的失败不再付 failure）。
- 控制冒烟 `outputs/experiments/rl-cup-mobile/runs/20261119-control-smoke/`：passed；有效站位56/233；底盘前后/横移/转向、升降方向正确，命令单步增量不超上限；局部reset隔离；越界当步终止。
- 奖励核对（临时脚本，GPU0、2环境、离目标最近有效站位，底盘满速前进顶桌）：碰撞全部归 `robot_scene`；75步内一条环境触发卡死终止，另一条持续计碰撞约−0.5/步；注入越界当步终止、failure=−20。
- 训练冒烟 `outputs/experiments/rl-cup-mobile/runs/20261119-train-smoke-g0/`：GPU0、16环境、4万条，正常结束（156次更新、33回合，约42条/s）；checkpoint为75D观测、10D动作；回合日志同时含移动字段（越界、底盘速度、位移）和Cup字段（分类碰撞时长、卡死、掉落）；未满best窗口，best.pt为最终模型兜底。只验证流程，不说明学习效果。

## 遗留

- 尚未正式训练。建议沿用两边的设置：GPU0–3、每卡128环境、seed42、5M（Cup预算；cube移动2M时成功率仍在上升）。
- 底盘满速顶桌时卡死判定不总是触发（TCP随底盘抖动可能超过2 cm路程阈值），此时按每步0.5计碰撞直到退开或超时。

## 正式训练启动（2026-11-19T22:35:36+08:00）

用户要求：GPU0–3、每卡128环境、seed42、预算8,000,000条。Run：`outputs/experiments/rl-cup-mobile/runs/20261119-223536-cup30-mobile-8m-g0123/`，提交f278328（工作区干净），配置副本input.yaml，命令见launch.json（timeout 24 h）。启动时GPU0–3各有他人约33 GB显存、利用率约2%。启动后约1分钟开始更新，无报错，约700条/s，预计约3.2小时跑完。不自动评测best.pt；结束后对latest.pt与 `ckpts/` 周期checkpoint独立评测再定最终模型。
