# RoboCasa365 原生场景与 Pick 子任务接入：交付记录

- 状态：active
- 日期：2026-11-13
- 替代：无
- 关联：../requirement/01-native-pick.md、../plan/01-native-runtime.md、running.md

## 结论

- 三个 RoboCasa365 厨房已导入并冻结，经统一接口完成创建、渲染、关闭重载、冻结初态恢复、原生动作执行和重复设置站位；包装层与直接调用原生 Environment 在 40 步随机动作下逐步状态完全一致（最大差 0.0）。
- ProcTHOR 链路仍用原有 `Simulation`、`robocasa-v1` 控制 profile 和 0.9 比例；离线回归除基线已有的 3 个 RL 导入错误外全部通过。
- `robocasa-pick-1cm-1s-v1` 判定只有单元测试和随机动作下的事实读取，**没有真实抓取成功证据，不构成抓取验收**。

## 时间线（UTC+8）

| 时间 | 事项 |
|---|---|
| 2026-11-13 15:55 | 从 actor-critic `830a81c` 建 worktree `.worktrees/robocasa-native`、分支 `feat/robocasa-native`；actor-critic 原有未提交修改未动 |
| 17:16–17:27 | 建 `.venv-robocasa`（robosuite 1.5.2 @ `5ce6643`、robocasa 1.0.1 @ `2ce1aec`、mujoco 3.3.1、numpy 2.2.5）；下载 textures、fixtures_lw、objs_objaverse、objs_lw 四个官方资产包（压缩约 4 GB）到 `reference_repos/robocasa/robocasa/models/assets/` |
| 17:30 | `scripts/scenes/build.py` 导入三个实例，run `20261113-scenes-r01`，3/3 accepted |
| 17:33 | 验证 r01：脚本缓存了 reset 前的 `env.sim` 对象（reset 会换新对象），读数无效，保留作记录 |
| 17:36 | 验证 r02：修正读数后，唯一失败项为包装层与直接调用不一致 |
| 17:38 | 对照实验：差异全部来自 `reset()` 中额外重置控制器目标；去掉后两者一致 |
| 17:40 | 验证 r03：三个实例全部检查通过 |
| 17:45–18:00 | LMDF 环境全套离线回归、文档 |

## 导入的实例

| scene_id | 目标（native 名 → body） | 目标资产 | 其他物体 |
|---|---|---|---|
| `robocasa365/PickPlaceCounterToCabinet/layout1-style1-seed42` | `obj` → `obj_main` | `objaverse/cup/cup_3` | apple（台面）、pancake（柜内） |
| `.../layout2-style1-seed43` | `obj` → `obj_main` | `objaverse/cup/cup_2` | lime、reamer |
| `.../layout3-style1-seed44` | `obj` → `obj_main` | `objaverse/cup/cup_5` | lettuce、apple |

原始语言：`Pick the cup from the counter and place it in the cabinet.`；派生 pick 描述：`Pick up the cup from the counter.`。初态下目标都由台面支撑、无抓握。目标限定用原生参数 `obj_groups="cup"`（任务本身对目标要求 `graspable=True`）。

## 运行接口验证（`tests/smoke/robocasa_native.py`，run `20261113-validate-r03`）

| 检查 | 三个实例 |
|---|---|
| 创建后状态与冻结初态差 | 0.0 |
| 重载后渲染与导入预览逐像素差 | 0 |
| 原生动作 40 步（2.0 s）后状态变化 | 是（时间 0.5 → 2.5 s） |
| rollout 后 reset、关闭后重新加载：与冻结初态差 | 0.0；步数计数清零 |
| 直接调用原生 Environment（官方回放路径）初态差 / 40 步逐步最大差 | 0.0 / 0.0 |
| `set_station` 位姿误差 / 连续两次完整状态差 | < 4e-15 / 0.0 |
| `set_station` 后手臂、升降、夹爪关节与目标位姿相对初态 | 不变（差 0.0） |
| 机器人装配 | `len(env.robots)=1`，根 body 只有 `robot0_base`，无 `robot_0/` 前缀；`robot0_link1` z=0.333 m、指尖行程 0.04 m（出厂值，未缩放） |
| 动作规格 | 12 维：`right` 6（OSC_POSE delta，base 坐标系）、`right_gripper` 1、`base` 3（底盘速度）、`torso` 1（关节位置）、`base_mode` 1（>0 底盘模式） |

零动作保持 10 步（arm 模式）的观察，不作为通过条件：

| 情形 | 底盘漂移 | 机器人与环境接触数 |
|---|---|---|
| reset 后 | ≤ 3e-6 | 0 |
| 在初始位姿 `set_station` | ≤ 3e-6 | 0 |
| 在偏移位姿（+0.10, −0.05 m, +0.2 rad） | 布局 1：0.052；布局 2：3e-6；布局 3：0.277 | 12 / 0 / 34 |

偏移位姿在布局 1、3 与环境穿插，被物理推开；这符合 `set_station` 不检查可行性的约定。手臂零动作漂移（0.001–0.014 rad）在 reset 后与 set_station 后相同，是原生 OSC 在 `achieved` 目标模式下的行为。

## 判定验证

- 单元测试 `tests/operation/test_robocasa_pick.py`：满足条件（1.0 s 时成功）、抬升 0.009 m、保持 0.9 s、抓握中断后重新计时、有支撑、抓的是其他物体、状态目标不符时报错，全部通过。
- 真实随机动作 rollout 上判定器正常运行：最大抬升约 1e-5 m，抓握 0 步，pick 与原生任务均未成功。这只说明事实读取通路可用，不是抓取证据。

## 回归

- LMDF 环境 `python -m unittest discover -s tests -t .`：352 个测试，16 skipped，3 errors（`test_rl_best/grasp_reset/rewards` 导入 mjlab 失败）。基线 actor-critic 在同一环境下是同样 3 个错误（339 个测试），与本次改动无关。
- 新增测试：`tests/scenes/test_robocasa_config.py`、`tests/operation/test_robocasa_pick.py`、`tests/sim/test_lmdf_runtime.py`、`tests/scenes/test_build.py::SceneSourceDispatchTests`；`tests/test_layers.py` 通过。

## 产物

`outputs/experiments/robocasa-native-pick/`（不进 Git）：`runs/20261113-scenes-r01`（场景库：`scenes.jsonl`、每实例 `env.json/ep_meta.json/model.xml/initial_state.npz/initial_facts.json/preview_*.png`），`runs/20261113-validate-r0{1,2,3}`（r03 为有效验证），r03 日志在 run 内 `validate.log`。

## 限制与遗留

- 资产是对 `reference_repos/robocasa` 检出目录的外部引用（XML 内为绝对路径，重建时由 `edit_model_xml` 改写），场景目录不是独立资产包。
- 本次资产用一次性并行 curl 下载（URL、目标目录、解压方式与官方 `download_kitchen_assets.py` 相同）；`scripts/install/robocasa.sh` 带资产参数调用官方脚本的那条路径未实际运行。
- `.venv-robocasa` 的 EGL 库从主仓库 `.venv/lib/native` 复制（`egl.sh` 走 apt 时代理返回 502）。
- `scripts/scenes/build.py` 的 RoboCasa 分支只用 `--gpu` 的第一张卡、串行导入；首批 3 个实例不需要并行。
- 站位全图、cuRobo 抓取、RL 训练均未接入；`set_station` 不做可行性检查。
