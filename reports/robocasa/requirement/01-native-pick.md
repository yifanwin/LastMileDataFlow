# RoboCasa365 原生场景与 Pick 子任务接入：需求

- 状态：done
- 日期：2026-11-13
- 替代：无
- 关联：../plan/01-native-runtime.md、../delivery/01-native-pick.md

## 目标与范围

在 `feat/robocasa-native`（worktree `.worktrees/robocasa-native`，基线 actor-critic `830a81c`）导入三个不同的 RoboCasa365 厨房，每个厨房一个固定目标物体和 pick 子任务，完成场景加载、重置、原生控制、状态读取、站位设置、渲染和任务判定。

本轮不做：站位全图、cuRobo 抓取试验、RL 训练。

## 场景与配置

- 沿用场景构建入口 `scripts/scenes/build.py`，按 `scene_source` 分派 ProcTHOR 与 RoboCasa；RoboCasa 不经过 ProcTHOR 的房屋解析、机器人装配、抓取资产筛选和场景编辑。
- 场景配置保存来源、runtime、机器人配置引用和实例选择；机器人配置单独保存 embodiment、比例和控制配置。
- ProcTHOR 保持 `procthor_10k / lmdf / 0.9`；RoboCasa 用 `robocasa365 / robocasa / 1.0`，直接加载官方 PandaOmron 和默认原生控制器（机械臂 OSC、升降关节位置、底盘速度）。
- 首批：`PickPlaceCounterToCabinet` 的台面取物部分，布局 1/2/3，style 1，seed 42/43/44，目标限定可抓取杯类；导入后冻结实际物体实例和初态，重载不重新抽样。
- 场景记录保存来源标识、原生环境类、布局/style/seed、目标对象名及 body/geom 引用、物体资产引用、原始语言描述、派生 pick 描述和判据版本。
- 保存环境参数、episode metadata、原生 XML 和初始物理状态，用原生 Environment 重建；资产保留外部引用，不宣称导出目录是独立资产包。
- 场景加载不再要求 `stations.jsonl`；需要站位的下游入口继续显式检查。

## 统一运行接口（`sim` 层）

| 接口 | 约定 |
|---|---|
| `reset()` | 恢复导入时的冻结初态，清空控制历史和任务计时 |
| `step(action)` | 推进一个控制周期，返回观测、时间和原生结束信息 |
| `get_state()` | 机器人、目标位姿和抓握/支撑事实；不承诺任意中途断点恢复 |
| `set_station(x, y, yaw)` | 先 reset，再设置底盘世界位姿，保持手臂和升降初始关节，同步控制器目标 |
| `render(camera)`、`close()` | 读取图像，释放资源 |

- RoboCasa 动作直接交给 `env.step()`，不经过 LMDF 机器人适配器，不自行推进物理。
- `action_spec` 说明动作维度、顺序和控制语义；统一调用不意味着两种 runtime 的动作数值可互换。
- `set_station()` 只是试验初始化，不代表导航完成或站位可行；不做自动站位搜索。
- LMDF 与 RoboCasa 使用隔离的 Python 环境，依赖统一在 `pyproject.toml` 声明；不加 RPC 服务；RoboCasa 模块延迟加载。

## Pick 判定 `robocasa-pick-1cm-1s-v1`（`operation` 层）

每个控制步检查：原生双指抓握对指定目标成立；目标没有环境支撑接触；目标 body 原点相对冻结初态抬升至少 0.01 m；以上连续成立至少 1.0 s，中断后重新计时。记录当前抬升、保持时间和各条件。不声称等同于逐物理步的 `strict-pick-v3`。原生任务结果单独记录，派生 pick 成功不代表完成放置。

## 验收

- 单元测试：来源分派、配置分离、目标映射；判定覆盖抬升不足、保持不足、抓握中断、有支撑、错误目标、满足条件。
- 三个真实实例：创建、渲染、关闭重载、冻结初态恢复、原生动作执行、重复设置站位；不重复装配机器人，比例 1.0。
- 统一接口与直接调用原生 Environment 在相同初态和动作序列下结果一致。
- 相关回归与分层检查；ProcTHOR 仍用原有 Simulation、控制器和 0.9 比例。
- 判据测试与运行接口测试分别报告；没有真实抓取成功证据时，不宣称抓取验收。
