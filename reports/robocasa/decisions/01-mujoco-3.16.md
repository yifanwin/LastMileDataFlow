# 统一 MuJoCo 3.16

- 状态：active
- 日期：2026-11-24 23:45 +08:00
- 关联：[05 跨仿真回放](../delivery/05-cross-sim-replay.md) 结果三、[plan 06](../plan/06-protocol-unification.md)

## 规定

所有 RoboCasa 评测（原生 robosuite/robocasa：cuRobo、π0.5、IRM/B2 基线导出、回放；mjlab：RL 训练与质量标签）一律使用 MuJoCo 3.16.0。

- 依赖：`pyproject.toml` 的 `robocasa` 组改为 `mujoco==3.16.0`，与主环境一致。
- 原生入口：`sim/robocasa_runtime.py::_robosuite()` 经 `sim/mujoco316_compat.py::import_robocasa()` 导入，只补三处 API 差异（robocasa 导入期版本断言、`jnt_type` 整数与枚举比较、`mj_fullM(m, d, dst)` 新签名与 `MjData.qM`），不改物理、控制器和判据。
- 2026-11-24 之前在 3.3.1 上得到的原生结果（含 04 号逐抓法扫描、π0.5、cuRobo 成功率）只作历史参考，不与 3.16 结果混合比较；需要时在 3.16 上重跑。

## 原因

同一批 114 条抓取，只换版本（3.3.1→3.16）原生成败翻转 18%，站位成功率改变最多 0.14；调 mjlab 参数消除不了这一半差异。统一版本后，跨仿真差异只剩 Warp/mjlab 实现的部分（原生 3.16 vs mjlab 一致 75%）。

## 遗留

- 环境内 mujoco 升级后，用 `tests/station_map/test_rl_native_pick.py`、`test_robocasa_pick` 和一次原生回放冒烟确认。
- 原生环境已升级（2026-11-24 23:43 +08:00），冒烟：S0022 g091 回放与叠加包装 3.16 结果完全一致；叠加包装脚本已删除。
