# Cup_30 闭合需受力与下探横向加权训练

- 状态：active
- 日期：2026-11-19
- 时间戳：2026-11-19T09:44+08:00
- 替代：无
- 关联：../../../../rl-cup-diagnosis/reports/rl-station-map/delivery/cup30-training-diagnosis.md
- 工作区：.worktrees/rl-cup-close-lateral（分支 codex/rl-cup-close-lateral，基于 7c5c332 加上 rl-cup-diagnosis 的未提交改动；未提交）

## 改动（相对 hand3mm + 预抓取两段目标的基线 run 20261119-011659）

- 闭合奖励：配置 reward.closing_requires_contact=true 时，只在 near（3 cm、20°）且至少一指法向力 > success.finger_force_min_n（0.5 N）时发放；空夹不再得分。
- 下探横向加权：到达预抓取位后，位置势函数距离改为 sqrt(轴向² + (k·横向)²)，k = reward.descent_lateral_weight = 3.0；轴向为抓取位姿 TCP 局部 Z 在底座坐标系中的方向（TaskState.approach_axis）。观测与 near 判定不变。
- 两项均由配置开关控制，方块配置不受影响。

## 验证

- RL 测试 137 项通过（新增闭合需受力、下探横向加权各一项）。
- GPU 上 oracle（先到预抓取位再下探）64/64 成功、0 碰撞。

## 训练

run：outputs/experiments/rl-cup-close-lateral/runs/20261119-094428-cup30-contactclose-lateral3-8m-g0123/，GPU0–3，从零开始，seed42，8M，其余与基线相同；训练结束后 run.py 自动对 best.pt 做独立评估。source.patch 保存完整未提交改动。
