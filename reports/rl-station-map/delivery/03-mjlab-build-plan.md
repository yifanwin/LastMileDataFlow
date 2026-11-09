# mjlab / DLS / SAC 双 Q：方案与计划交付

- 状态：done
- 日期：2026-11-09
- 替代：无
- 关联：../requirement/01-fixed-base-sac.md、../plan/01-implementation.md

## 执行过程

1. 保留工作区已有修改，复读需求、旧 CPU / Mink 草案、Panda 伺服、接触/抓取判定与相关测试。
2. 按用户确认采用 mjlab 并行环境、自定义 SAC 双 Q、DLS 与学习层面避障；不将轨迹碰撞预检列为要求。
3. 阅读用户给出的官方安装页，并核对主 checkout `reference_repos/mjlab` 的安装说明、依赖、DLS、step/reset、子步扩展和接触传感器源码。
4. 按最新指定将最终验收固定为 val103 / Cup_30，区分 Panda 与旧 RBY 配方，补充场景迁移、站位 split、实际 rollout 和 Q 排序验证。
5. 原位重写构建方案，同步核心需求与报告入口；历史评估保留并标注当前依据。

## 交付结果

- 一份完整的[构建方案与 P0–P6 计划](../plan/01-implementation.md)，覆盖依赖安装、GPU 环境、资产/reset、控制、接触/奖励、GAT、SAC/Replay、模块划分、训练预算、checkpoint、Q 图、验收与产物。
- 固定 Base Frame 7D 动作、每 policy step 一次目标、每子步 DLS、0.002 s × 25 的 20 Hz 时序、手动 reset 和真实 next observation 保存顺序。
- 真实碰撞用于奖励和统计；同时报告 strict-pick 抓取成功与无非法碰撞成功，保持既有任务判据语义。
- 最终验收使用 val103-cup30 独立站位，建议 20 点 × 10 次随机策略 rollout，并与初始化 Actor 比较；数值目标标为工程建议，未冒充实验结果。
- 建议工程量约 7–13 个工作日，训练耗时待实测吞吐确定；所有实施阶段待开始。

## 验证与限制

本轮仅改文档，没有安装依赖、创建训练脚本、修改上游/运行源码或启动训练。uv 可用，actor-critic 工作区尚无 .venv。用户已确认 CUDA 0–7 可用，方案要求启动时按实时利用率和空闲显存选卡；本会话 nvidia-smi 查询失败仅记录为设备访问尚未核对，P0 在实际训练会话完成查询与依赖检查。

完成本轮相关文档的链接和差异检查，核对当前需求不再要求 Mink 碰撞 QP；没有代码改动，未运行软件回归或真实 rollout。尚无新策略抓取、学习避障、训练吞吐或站位图有效性的验证结论。
