# RoboCasa365 原生 runtime：方案

- 状态：done
- 日期：2026-11-13
- 替代：无
- 关联：../requirement/01-native-pick.md、../delivery/01-native-pick.md、../../../docs/architecture.md

## 分层落点

| 层 | 新增/修改 | 作用 |
|---|---|---|
| robots | `robots/robocasa_config.py`（`RoboCasaRobotConfig`）；`registry.load_robot_config` 按 `runtime` 分派；Panda/RBY-1 配置加显式 `scale` | 机器人配置与场景配置分离；LMDF Panda 固定 0.9，RBY-1 1.0，RoboCasa 1.0 |
| sim | `runtime.py`（`ActionSpec`、`StepResult`、`RuntimeState`、`SceneRuntime`）；`lmdf_runtime.py`（`LmdfRuntime`）；`robocasa_runtime.py`（`RoboCasaRuntime`、冻结与重建） | 小接口，两个组合实现；不改 `Simulation` |
| operation | `outcomes/robocasa_pick.py`（`PickJudge`） | 判定只消费 runtime 事实 |
| scenes | `scenes/robocasa.py`（配方与导入）；`build.build_scenes` 按 `scene_source` 分派；`library` 不再要求站位产物，新增 `require_stations` | 下游站位入口（标定、benchmark、两个 smoke）显式检查 |

## 关键做法

- 冻结：`env.json`（`robosuite.make` 参数，含导入时解析好的默认控制器字典、版本和两个仓库 commit）、`ep_meta.json`、`model.xml`（`env.sim.model.get_xml()`）、`initial_state.npz`（`sim.get_state().flatten()`）。
- 重建与 reset：照搬 robocasa 回放路径 `set_ep_meta → reset → reset_from_xml_string(edit_model_xml(xml)) → sim.reset → set_state_from_flattened → forward → update_state`。原生 reset 会重建控制器，不再额外重置控制器目标（实测额外重置会改变原生控制结果，见交付记录）。
- 站位：先 reset，把三个底盘关节清零，按底盘 body 的世界位姿解出 forward/side/yaw，前向检查误差 < 1e-6；只动底盘关节，然后重置各部件控制器目标到实测状态。
- 事实：抓握用 `env._check_grasp(右夹爪, 物体)` 对全部物体求值，判定器只认指定目标；支撑为目标 geom 与非机器人、非目标子树 geom 的接触。
- 设备：RoboCasa 进程不用 CUDA，`make_env` 前调用 `utils.gpu.bind_cpu_renderer()`，保留绑定的 EGL 卡并隐藏 CUDA（robosuite 导入时要求 `MUJOCO_EGL_DEVICE_ID` 出现在 `CUDA_VISIBLE_DEVICES` 里，而 LMDF 的 EGL 序号与 CUDA 卡号分开映射）；robosuite 设备号固定 -1，按 `MUJOCO_EGL_DEVICE_ID` 渲染。
- 环境：`.venv-robocasa`（Python 3.11、mujoco 3.3.1、numpy 2.2.5），`pyproject.toml` 依赖组 `robocasa`；robosuite/robocasa 从 `reference_repos` 可编辑安装且 `--no-deps`，不装上游为数据集工具引入的 lerobot/tianshou/torch。
