- 状态：done
- 日期：2026-11-11
- 替代：无
- 关联：../requirement/01-fixed-base-sac.md、../delivery/07-staged-reward-and-lift.md、../docs/01-running.md、../../../outputs/RULES.md

# main 两次合并对 SAC 训练与实验的影响检查

## 范围与结论

检查 actor-critic 当前提交 `769943f`，对照首次合并前 `05da48c`，核对 main 的 `c8f3c5a`（Panda 缩放）与 `94f533c`（实验产物整理）。检查前工作区干净；本次只新增本报告，未修改训练代码、配置和历史产物。

**当前 SAC 无法从默认真实资产启动。** 缩放后的准备阶段与 RL 中硬编码的 8 cm 夹爪初态冲突，已在 CPU 复现。另有独立的机器人装配尺寸不一致，以及缩放后夹爪动作仍沿用旧范围的问题。不能仅修正第一次报错后就开始正式训练。

产物整理提交未修改 SAC 算法、Replay、RL 配置、训练、评分、评估和视频实现；没有发现它对现有 SAC 输出路径的直接破坏。历史实验保留旧源码和旧尺寸含义，后续 0.9 尺寸实验需要使用新 run。

## 发现与影响

### 1. P1：默认真实场景初始化直接失败

位置：`src/lastmile_dataflow/scenes/loading/rl_scene.py:53-58`；触发变化在 `runtime/simulation.py:96-104`。

`prepare_rl_scene()` 显式构造 `gripper_width={'right': .08}`，覆盖了已经改为 `.072` 的 Panda 默认配置。随后 `Simulation.from_source()` 将机器人缩放至 0.9，手指单侧上限变成 0.036 m，初始化仍提交 0.04 m，触发：

```text
lastmile_dataflow.runtime.simulation.InitializationError:
joint limit: gripper0_right_finger_joint1
```

训练、站位评分、独立评估和项目 RL smoke 都通过 `StationEnv -> prepare_rl_scene`，因此都会受阻。与 Actor 权重、奖励参数或 GPU 负载无关。

另外，`scenes/stations/rl.py:28-36` 的站位准备也写死 `.08`。在后续统一缩放最终模型后，这里同样必须更新，否则会出现下一处初始化越界。

### 2. P1：RL 最终模型重新载入原尺寸机器人，未继承 0.9 缩放

位置：`scenes/loading/rl_scene.py:63-74`、`runtime/rl/environment.py:110-114`。

场景准备先通过主仿真获得缩放模型，但随后删除其中的机器人，再从 factory XML 重新载入 `robot_spec`。该路径仅调用伺服参数准备函数，没有应用几何缩放。`PreparedRLScene.copy_robot()` 将这份原尺寸 spec 提交给 mjlab；本地 mjlab 的 `scene/scene.py:243-244` 也只使用默认 frame 装配。

真实 factory 资产 CPU 对照结果：

| 项目 | RL 当前重新载入路径 | main 装配路径 |
| --- | ---: | ---: |
| body / geom 数 | 22 / 79 | 22 / 79 |
| 首臂关节 body 的 Z 偏移（m） | 0.333 | 0.2997 |
| 单侧手指上限（m） | 0.04 | 0.036 |
| 升降上限（m） | 0.34 | 0.306 |
| 机器人总质量（kg） | 240.35724 | 175.22043 |

质量比为 0.729，符合本次 geometry 缩放；两份模型的所有 body_pos 对照满足 0.9 比例。

只改第 1 项开口后，训练仍会使用原尺寸机器人，训练中的可达性、碰撞和动力学不会代表 main 的新机器人。应在共享的机器人准备过程统一尺寸，让 CPU 准备和 mjlab 最终模型一致，避免重复缩放源资产。

### 3. P2：最终模型正确缩放后，RL 夹爪动作及尺寸相关常量仍需适配

位置：`runtime/rl/environment.py:49-60`。

`Gripper.process_actions()` 将动作映射为单侧 `[0, 0.04]`，reset 目标也为 `[0.04, -0.04]`。新模型单侧上限为 0.036 m，动作大于 0.8 时目标超限，满开口指令仍为 8 cm。应从实际模型关节范围生成动作目标和 reset 目标。这是第 2 项修复后会暴露的控制语义问题，本次没有宣称已运行缩放后的 GPU 控制。

相关常量需一起检查：

- `runtime/rl/terms.py:235,243,301-302` 和 `scenes/rl_observation.py` 的开口、升降归一化仍为 `.08` / `.34`。固定参考尺度可以保留，但必须明确；其中开口同时用于阶段奖励，新模型完全张开时 opening 仅为 0.9，Approach 张开奖励幅度随之变化。
- `scenes/stations/rl.py:51` 的支撑采样半径仍为 `.4383`，没有使用新配置的约 `.39446`。这是较保守的旧半径，会影响地面边缘附近站位的有效性判定；未量化它在 val103 中改变的站位数量。
- 2 cm TCP 动作、0.05 m/s 升降速度、抓取库和成功门槛没有被这两次合并改写。它们是物理单位下的实验选择，不应机械地全部乘以 0.9；需要在正确尺寸的模型上重新验证抓取正例。

## 旧 checkpoint、结果与实验管理

### 旧结果保留历史含义

已有 `staged-reward-20261111`、`val103-cup30-sac-20261109` 等目录及其中源码副本仍存在。本次未搬迁、删除或改写。已有脚本成功、短训练回报和 Q 只能说明对应旧版本、旧机器人尺寸的表现，不能作为 0.9 机器人已验证的依据。

核对进程列表时未发现 `train_sac` / `evaluate_sac` / `score_stations` / 项目 RL smoke 在运行；该结论仅对应检查时刻。

### 缩放没有进入有效 RL 配置，旧模型兼容检查不能识别尺寸变化

`configs/rl/val103_cup30.json` 没有机器人缩放字段。`rl_training.py:134-137` 的 resume 和 `rl_evaluation.py` 的评分、评估检查主要比较保存配置；项目 revision 虽会记录，但没有据此拒绝尺寸变化。

因此，完成缩放适配后，同配置、同维度旧 checkpoint 仍可能被加载，旧的最佳回报和 Q 语义会跨模型沿用。新尺寸应显式记入有效配置，使用新 run 从头训练；若刻意做权重迁移，应作为独立实验记录，不拼接旧学习曲线或最佳模型比较。无需增加通用封存或文件指纹机制。

### 产物整理提交的实际作用

`94f533c` 主要调整五个主 CLI、no-edit 流水线、共享 create_run 和目录规则。SAC 使用自己的 `--output` 与路径创建逻辑，不调用此次改变的主 CLI 产物布局，也没有调用被调整的 create_run。因此本次未发现需要为已有 SAC run 改写路径的地方。

新规则要求长期实验使用固定 commit 的 worktree、不再复制源码；现有 RL 运行说明仍包含 `git archive` 操作，后续应同步文档。历史源码副本按旧实验保留，不为规则变化批量迁移。后续新实验将原始训练、评估、评分产物放入对应 run，跨 run 汇总放在单元 analysis。

## 执行与验证

1. 检查目标 worktree、干净状态、两次合并及差异，沿真实 SAC 装配和输出调用链检查。
2. 直接调用默认配置的 `prepare_rl_scene()`，读取真实 val103 / Panda / Cup_30 资产，CPU 复现第 1 项错误。
3. 使用真实 Panda factory 资产，分别按 RL 的重新载入方式和 main 的 0.9 frame 装配方式编译，得到上述尺寸、关节范围和质量对照。
4. 相关离线测试共 27 项全部通过，覆盖 SAC、Replay、抓取状态、DLS、奖励、机器人接口、规划尺寸、观测、站位统计、异步评估和代表视频选择。

测试命令（actor-critic 根目录）：

```bash
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl .venv/bin/python -m unittest \
  tests.policies.test_sac tests.runtime.test_pick_state tests.runtime.test_dls \
  tests.runtime.test_rl_rewards tests.runtime.test_robot_interface \
  tests.planning.test_panda_omron tests.scenes.test_rl_observation \
  tests.data.test_station_q tests.workflows.test_rl_evaluation tests.data.test_rl_video -v
```

研究环境实测为 MuJoCo / MuJoCo-Warp 3.16.0、mjlab 1.6.0、Torch 2.14.0+cu130、Warp 1.19.0；main 的 MuJoCo 依赖升级与当前研究环境已安装版本一致。

本次没有修改实现，所以没有追加全套离线回归。现有 27 项测试未覆盖默认真实资产装配，不能据其通过判断训练入口可用。没有启动 GPU rollout、新训练、渲染或独立 acceptance；无法据本次检查估计缩放后的成功率或收敛速度。

## 建议处理顺序

先统一两条机器人装配路径的 0.9 尺寸，再同步两处初始开口和 RL 夹爪动作范围；将尺寸写入有效配置。随后检查真实资产初始化、单步/reset 和原脚本站位抓取正例，通过后用新 run 开始训练。历史结果维持原样。
