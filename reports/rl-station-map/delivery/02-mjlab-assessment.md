# mjlab 用于固定底盘 SAC 训练的适配评估

- 状态：done
- 日期：2026-11-09
- 替代：无
- 关联：../requirement/01-fixed-base-sac.md、../plan/01-implementation.md

> 本文保留选型确认前的评估记录。用户随后已确认 mjlab + SAC 双 Q + DLS、学习避障与 val103-cup30 验收，并指定本地 `reference_repos/mjlab`；当前执行依据为[构建方案](../plan/01-implementation.md)，不再将 Mink 或发布 tag 作为默认路线。

## 结论

可以考虑，建议将 **mjlab GPU 并行环境 + 自定义 GAT / SAC** 列为优先验证路线。它有望减少并行环境、reset、随机化与训练观测的基础设施工作；是否比现有 CPU 仿真方案更合适，要由 Panda 抓取和采样吞吐实测决定。目前没有安装 mjlab、修改训练代码或运行新实验，原需求中的双 Critic 站位评分仍保留。

不能把采用 mjlab 理解为直接运行官方 train 命令即可满足本项目需求。官方集成走 RSL-RL on-policy / PPO，本项目要求的是动作价值 Q(s,a)、Replay 和双 Q 的 SAC；应使用环境层，另接 SAC 训练循环。[官方训练说明](https://mujocolab.github.io/mjlab/main/source/training/rsl_rl.html)、[训练入口源码](https://github.com/mujocolab/mjlab/blob/main/src/mjlab/scripts/train.py)。

## 核对过程与主要发现

本轮保留工作区已有的报告修改，阅读现有 RL 需求、实施方案、依赖、Panda 位置控制、Simulation 和 strict-pick-v3 判定；再核对 mjlab 官方文档、版本依赖与环境 step 源码。

| 部分 | 可采用的能力 | 本项目仍需完成的工作 |
|---|---|---|
| 并行仿真 | MuJoCo Warp 批量 GPU stepping，manager 组织观测、动作、奖励和 reset | Panda-Omron 资产、位置伺服、底盘/升降保持与真实物理验证 |
| 控制 | DifferentialIKAction 支持相对模式的 6D TCP 动作，每物理子步求解 | Base Frame 语义、连续夹爪宽度、限制和失败记录；构成完整 7D 动作 |
| 学习 | 可从环境读取批量状态、奖励和终止信息 | GAT、Gaussian Actor、双 Q / target、Replay、温度更新与评分接口 |
| 任务判定 | 接触传感器可按物体对取力并保存子步历史 | 双指承力、脱离支撑、5 cm 抬升、2 s 保持、滑移与固定部件漂移的统一判定 |
| 随机场景 | 同构场景可随机化，VariantEntityCfg 支持不同网格 | 任意房间快照需按结构分组；不能逐 episode 随意替换整套模型 |

能力依据：[场景层](https://mujocolab.github.io/mjlab/v1.6.0/source/scene.html)、[动作层](https://mujocolab.github.io/mjlab/v1.6.0/source/actions.html)、[传感器](https://mujocolab.github.io/mjlab/v1.6.0/source/sensors/index.html)。表中“仍需完成”是本项目适配判断，不是上游已经实现的能力。

### 控制器取舍

原方案的 Mink QP 提供位置、速度和指定碰撞对约束；mjlab 内置控制器采用 DLS，关节限位和姿态以目标项加入，不能等同于带硬碰撞不等式的 Mink QP。逐环境调用 CPU Mink 会引入数据传输和串行 QP 成本，不能据此预期 GPU 并行加速。

如果必须保留 Mink 的控制约束，先保留原 CPU 控制方案并测瓶颈。若接受批量 DLS，需要明确修订控制器方案，并固定训练、出图和独立 rollout 的同一控制器语义；碰撞保持物理启用，合法抓握与非法接触仍按任务规则区分。不能只替换训练控制器而把原控制器下的 Q 当作同一个量。[DLS 动作说明](https://mujocolab.github.io/mjlab/v1.6.0/source/actions.html)。

### 场景与版本边界

mjlab 的异构网格功能仍要求共享 body tree、joint topology 等结构，物体数量与关节结构不能任意变化；variant 的分配在仿真初始化时固定。首轮采用一张桌面、一个目标和固定数量障碍物，随机化站位、姿态及受支持的参数。已有场景后续按拓扑分组加载，首轮不构建通用模型转换层。[异构场景限制](https://mujocolab.github.io/mjlab/v1.6.0/source/entity/per_world_mesh.html)。

必须区分正式 tag 与滚动 main，两者虽然版本字段同为 1.6.0，依赖已经不同：

| 依赖 | 本项目声明 | mjlab v1.6.0 tag | 本次读取的 mjlab main |
|---|---|---|---|
| MuJoCo | 3.5.0 | ~=3.11.0 | ~=3.16.0 |
| Warp | 1.11.1 | >=1.14.0 | >=1.15.0 |
| PyTorch | 2.7.1+cu128 | >=2.7.0 | >=2.14.0 |

建议先评估正式 tag，在独立研究环境维护依赖；不能直接向主环境安装，也不能把项目整包按现有依赖安装到该环境后期待自动兼容。后续实现需在项目 pyproject.toml 中明确研究依赖与安装方式。当前 MJB 不能跨 MuJoCo 版本直接恢复；应由 XML / MjSpec 和必要初态重建并验证，而不是放宽现有快照版本检查。[tag 依赖](https://github.com/mujocolab/mjlab/blob/v1.6.0/pyproject.toml)、[main 依赖](https://github.com/mujocolab/mjlab/blob/main/pyproject.toml)。

## 建议的最小验证顺序

1. 在独立环境加载同一 Panda-Omron 资产、简单桌面和目标；先以单环境检查位置伺服、连续夹爪、底盘与升降保持及不同 yaw 下的 TCP 动作。用脚本动作完成真实抓取、抬升和保持。
2. 用 mjlab 环境接自定义 SAC，保留已有 GAT、双 Q 与 K=16 评分需求。先让简单任务学出真实成功，再扩大场景随机化。PPO 可作为单独算法备选，但其 V(s) 不能直接替代当前 min(Q1,Q2) 评分。
3. 单卡依次试 32、128、256 个环境，分别记录有效 transitions/s、环境 stepping、控制、网络更新、显存与任务成功率，再决定是否采用和扩大规模。这些数量是试验建议，不是吞吐承诺；官方多 GPU PPO 启动能力也不自动构成 SAC 多 GPU 实现。
4. 冻结策略、控制器和归一化状态，在留出站位生成 Q 图并做真实 rollout。先在训练后端验证评分关系，再在既有 CPU 执行链核对代表性站位；有后端或控制语义差异时分别报告。

实施前需处理几个直接影响正确性的接口：

- mjlab main 当前默认 auto-reset 会返回重置后观测。自定义 SAC 可先使用手动 reset，保存真实 next observation 后再重置完成环境，避免 replay 跨 episode 串接；正式 tag 对应接口需按固定版本核对。
- 保留“20 s 有限时域且剩余时间入观测”的定义，任务超时作 terminal；外部中断才 bootstrap。明确奖励是否按 dt 缩放，保持训练和评分一致。[环境源码](https://github.com/mujocolab/mjlab/blob/main/src/mjlab/envs/manager_based_rl_env.py)。
- mjlab 固定 decimation 要求 policy 周期为整数个物理步；20 Hz 与 0.004 s 的比值为 12.5。旧 Simulation 用余数交替子步，不能直接照搬为固定 decimation。若采用 0.002 s × 25 达到 20 Hz，应重新验证接触与控制；若保留 0.004 s，则明确选用可整除的实际控制频率。
- 接触 history 不能替代整个 strict-pick 协议。保持计时和滑移等应按物理子步更新，任务阈值和判定核心共享；CPU 与 GPU 仅适配事实采样，避免维护两套成功定义。
- mjlab 将承担训练物理执行，现有 Simulation.step 不能直接包一层就变成 GPU 执行器。这属于原方案执行后端的调整；代码实施时应明确研究入口边界，复用动作约定、资产准备与任务判定，避免复制既有五命令链路。

## 本轮结果与限制

形成了可采用能力、算法与控制器差异、依赖隔离及最小验证顺序；建议优先做 mjlab + SAC 小规模适配验证。尚未改变已确认的 SAC 目标或批准 DLS 替换 Mink，也未安装依赖、修改运行代码、运行回归、GPU stepping 或训练。当前不能宣称资产兼容、抓取成功或训练加速已经通过验证。
