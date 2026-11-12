# 单站位 S0246-y04 的 SAC 训练选项

- 状态：done
- 日期：2026-11-12
- 时间：2026-11-12 17:13:53 +08:00
- 替代：无
- 关联：[运行指南](running.md)、[新配置](../../../configs/station_map/rl/val103_cup30_s0246_y04.yaml)

## 目标与实际位姿

在 actor-critic 工作区提供只训练 S0246-y04 的选项和独立配置，不启动完整训练。

历史原始记录 `outputs/experiments/distributed-sampling/runs/multi4-sync-long/station-q.json` 中，该站位的世界位姿为 `x=6.613459458748234 m, y=2.5988113404423916 m, yaw=-1.5707963267948966 rad`，当时 valid=true，属于 validation。多个历史分布式 smoke 的站位记录与其一致。

当前机器人尺寸采样生成 233 个 XY 点，只有 S0000–S0232，不存在 S0246。新配置固定实际世界位姿，避免使用新网格同号、最近邻或重新推算的位置替换用户指定位置。

## 修改

- 新增可选 `stations.fixed_station`，包含 `station_id` 与 `base_xyyaw`。指定时生成唯一 train 站位，替代圆盘网格和空间划分；未指定时保留原网格、朝向、split 和 no_heading 规则。
- 候选生成与物理检查分别处理；两种模式共用 Home 机器人接触和保守底盘地面支撑检查。指定站位不合法时报告站位 ID 和原因，不回退到其他站位。
- 复用现有训练池和 reset，所有 Sampler 的所有环境及局部 reset 均只使用该位姿。物体初态、Home、DLS、7D 动作和奖励逻辑沿用已有实现。
- 结束后的单次评估只在这一站进行，日志标记 `scope=fixed_station, split=train`，单站位 Spearman 为 null。图中成功率标题随模式改变；最终 Q 文件也只包含此站，不生成其他站位的 Q。
- 独立配置 `configs/station_map/rl/val103_cup30_s0246_y04.yaml`：每卡 128 环境、200000 健康 transitions、随机预热 20000、batch 256、Replay 100000、每新增 128 条更新一次，预期 1406 次更新；其余 SAC、动作、奖励参数沿用当前默认配置。末次评估为 1 环境 × 16 次 stochastic rollout，另保留 stochastic / mean 视频。
- 同步配置说明和运行指南。保留工作区开始时已有的全部未提交改动，未提交本次修改。

## 验证

局部软件测试命令：

```bash
MUJOCO_GL=disable .venv/bin/python -m unittest \
  tests.station_map.test_rl_scene tests.station_map.test_rl_evaluation \
  tests.station_map.test_rl_sampling tests.station_map.test_rl_station_q -v
```

17 项通过，覆盖显式历史位姿、默认网格及朝向、分组、单站位评估范围与视频选点、结束后评估、CPU Replay 和分布式传输。新增评估测试初次因模拟 agent 缺少模型对象而失败，补全测试 fixture 后通过。

使用真实 val103 / Cup_30 资产，在 CPU 上构建当前 0.9 机器人模型、静置场景，并执行同一 `prepare_stations`：唯一 S0246-y04 为 valid=true，Home 未检出非法接触，地面支撑通过。

GPU 短检查命令：

```bash
MUJOCO_GL=disable OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python tests/smoke/rl.py \
  --config configs/station_map/rl/val103_cup30_s0246_y04.yaml \
  --gpu 6 --num-envs 4 --steps 5 --random-actions --learner-updates 2 \
  --output outputs/experiments/scratch/20261112-sac-fixed-s0246-y04/runs/smoke/result.json
```

启动前查询实时 GPU：6、7 卡利用率为 0%，各空闲 72831 MiB，选物理 GPU 6。进程退出码 0，20 transitions、2 次实际 SAC 更新，4 环境的 selected_stations 全部为指定站位；合法站位仅 1 个且为 train。局部 reset 保留其余 3 环境的 qpos，overflow_bits 全部为 0，loss 有限。

原始结果和 stdout/stderr 位于上述 scratch run，记录时间 2026-11-12 17:13:53 +08:00，源码基础提交 `987d1a212085408b05b633e2faf309017b600c4c`，使用当前未提交修改。短检查的 4 个环境均未成功抓取；这只验证配置、物理 stepping、reset 和学习更新链，不作为策略学习效果。

## 边界

未启动 200000 条完整训练，未验证新配置的最终成功率、末次真实评估视频或长期收敛。单站位评估反映训练位置表现，不证明空间泛化；常规 acceptance 留出站位验收仍适用于网格训练模式。恢复训练仍要求有效配置一致，应为本配置新建 run 并从头训练。
