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

## 停止与合并（2026-11-19T10:15+08:00）

- 按用户要求，于约 10:05 停止 run 20261119-094428-cup30-contactclose-lateral3-8m-g0123，停在 930,048 条经验、7,109 次更新；此前非预热回合 256 个，成功 0，双指接触 35%。时间太短，不作结论。status.json 标为 stopped_by_user。
- 分支合并：codex/rl-cup-close-lateral（4c2c680，闭合需受力 + 下探横向加权 3）与 codex/rl-cup-symmetry（f21a476，两指互换 + 轴对称对齐）合并为 1805996。冲突仅为两边各自新增的内容，两边都保留；候选库改用对称版 cup30-panda09-rays21-ratio30-hand3mm-sym-fps128（128 条与上一版相同，另记录对称轴），已移到本工作区 outputs 下。codex/rl-cup-symmetry 工作区与分支已删除，其 outputs 移到 outputs/experiments/rl-cup-symmetry。
- 验证：RL 测试 140 项、抓取测试 11 项通过；oracle（先到预抓取位再下探）64/64 成功、0 碰撞，初始姿态误差中位数 0.14 rad。
- 正式训练：outputs/experiments/rl-cup-close-lateral/runs/20261119-101126-cup30-contactclose-lateral3-symalign-8m-g0123，GPU0–3，seed42，8M，结束后自动评估 best.pt。
