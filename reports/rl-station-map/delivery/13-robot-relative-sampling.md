# RL 站位采样读取机器人尺寸

- 状态：done
- 日期：2026-11-12
- 时间：2026-11-12 16:58 +08:00
- 替代：RL 配置中的固定采样半径 1 m、间隔 0.1 m
- 关联：[运行说明](running.md)、[机器人配置](../../../configs/robots/panda_omron.yaml)

## 目标与规则

按用户确认的规则，采样半径使用机器人配置的手臂伸展长度 `arm_working_reach_m`，采样间隔使用 `base_radius_m × 0.25`。两者均读取 `configs/robots/panda_omron.yaml`，不在 RL 配置里硬编码米数。沿用现有手臂自由空间数值臂展字段；本轮未重新测量或证明其全局最大值。

当前半径为 0.8469596623341055 m，间隔为 0.09861573150365006 m。配置中的两个尺寸已经是实际尺寸，不重复乘 `scene.robot_scale`；模型缩放后须同步维护机器人配置中的尺寸。

## 修改

- RL 配置使用 `stations.robot_config` 引用机器人 YAML，删除固定 `radius_m` / `spacing_m` 输入。
- 增加轻量配置解析函数，训练、评分、验收及真实 smoke 共用；解析后的底盘半径、采样半径和间隔进入训练 checkpoint 的有效配置，沿用现有恢复和评分的一致性检查。
- 圆心、9 档朝向、Home 碰撞与地面支撑过滤、空间划分保持原有规则。
- 同步配置说明与运行指南，保留工作区原有的训练末次验证等修改。

## 验证

执行：

```bash
MUJOCO_GL=disable .venv/bin/python -m unittest \
  tests.station_map.test_rl_scene \
  tests.station_map.test_rl_evaluation \
  tests.station_map.test_rl_sampling -v
```

11 项测试通过，覆盖机器人配置修改后网格同步缩放、当前配置解析、训练恢复及采样器相关流程。第一次扩展测试因子进程 EGL 用户态库未加载而失败；按运行指南关闭本次无需使用的渲染后通过。

实际执行圆盘网格生成：233 个 XY 点，232 × 9 + 1 = 2089 条候选记录（中心点保留一条 `no_heading` 无效记录）。该数量尚未经过真实模型的碰撞和支撑过滤。本轮未启动 GPU rollout 或训练，未更新历史实验的合法站位统计。
