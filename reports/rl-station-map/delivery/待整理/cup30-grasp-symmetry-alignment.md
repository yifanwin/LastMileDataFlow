# Cup_30 候选抓取按起始 TCP 对齐（两指互换 + 绕对称轴旋转）

- 状态：done（未训练）
- 日期：2026-11-19
- 关联：../../../.worktrees/rl-cup-diagnosis/reports/rl-station-map/delivery/cup30-training-diagnosis.md
- 工作区：.worktrees/rl-cup-symmetry（分支 codex/rl-cup-symmetry，基于 7c5c332 + 手掌余量/两段势函数补丁，即 run 20261119-011659 的 source.patch）；不含"闭合奖励/横向惩罚"改动；未提交

## 改动

- `operation/grasp_preprocessing.py`：新增 `object_symmetry`。在目标碰撞几何上，按 12 个高度 × 36 个方位从外向竖直轴打水平射线，测外表面半径；各高度上半径极差都 ≤ `--symmetry-tolerance-m`（默认 2 mm）才判为轴对称。库中保存 `axisymmetric`、`symmetry_axis_local`、`symmetry_center_local`、`symmetry_deviation_m`。
- `operation/grasps.py`：新增 `load_symmetry`，非对称或旧库返回 None。
- `rl/env/terms.py`：配置 `grasp.align_to_tcp: true` 时，每回合的第一次观测里，对抽到的候选枚举等价变体：两指互换（右乘 Rz(π)）× 绕对称轴旋转（`axis_steps`=72，即 5°一档，仅轴对称物体），取与当前 TCP 姿态夹角最小的那个，写入本回合的抓取目标，回合内固定。非对称物体只有两指互换这 2 个变体。episode 记录 `grasp_variant`（旋转角、是否互换）。
- 配置：新库路径，`align_to_tcp: true`，`axis_steps: 72`。

## 验证

- Cup_30 判为轴对称，半径极差 1.2 mm；对称轴为物体局部 y。新库 369/1000 通过，128 条与原手掌余量库完全相同，seed42 的 16 条不变。
- 测试：RL 138 项通过（新增：轴对称候选转到起始 TCP、非对称只互换两指、射线法拒绝带把手的物体），抓取相关 11 项通过。
- GPU oracle（4 站位 × 16 候选，先到预抓取位再下探）：64/64 成功、0 碰撞；从 Home 出发需要的旋转中位数 0.144 rad（8.3°），对齐前为 1.636 rad（93.7°）。

## 限制

- 绕轴旋转后候选落在杯沿新的方位，对场景里的其他物体（旁边的瓶子、桌面）是否会碰，没有检查。
- 对齐只看姿态夹角，不看位置和可达性。
