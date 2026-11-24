# RoboCasa 抓取库与跨仿真回放合入

- 状态：done
- 日期：2026-11-25
- 关联：[cup_3 逐抓法扫描](04-cup3-grasp-sweep.md)、[跨仿真回放](05-cross-sim-replay.md)、[MuJoCo 3.16 决策](../decisions/01-mujoco-3.16.md)

## 两个来源的更新

| 来源 | 合入内容 | 已有运行结果 |
| --- | --- | --- |
| `graspdatagen-grasp-library` · `2674a7b` | 从碰撞表面与指垫几何生成候选，检查夹爪和末端连杆的间隙及接近通道，用 MuJoCo 焊接到 mocap 的夹爪做名义与扰动抓取，再做对称规范化与 FPS 选 16 个。输出 RL 和 cuRobo 两种 NPZ。 | cup_3 和 cup_5 均已生成 16 个抓法。cup_3 历史原生三站扫描抓起 26/48、无环境接触抓起 22/48。cup_5 已完成代理物理验证，原生扫描待验证。 |
| `robocasa-physics-align` · `80747bc`，此前由 `f43ea81` 合入 | 原生 RoboCasa 回放已保存的 cuRobo 计划并记录逐控制步状态。mjlab 用相同的臂目标和指控制量回放，以 PickJudge 口径比较成败和接触时序。 | 历史 114 条回放中，原生 MuJoCo 3.16 与 mjlab 成败一致 85/114。3.16 的原生兼容入口曾对 S0022 g091 做回放，保存的数值轨迹与先前 3.16 包装入口一致。 |

抓取库探索方案和历史产物的原始记录保存在来源 worktree。常规回放已去掉强制写入臂状态及接触参数覆盖，按评测配置运行。原生使用现有 MuJoCo 3.16 兼容层。抓取库继续由显式入口生成和选用，默认评测配置仍指向已有抓法池。

## 使用入口

- `scripts/operation/generate_grasps.py` 从导出的场景生成候选和接近通道记录。
- `scripts/operation/validate_grasps.py` 对候选执行名义和扰动条件下的代理抓取。
- `scripts/operation/select_grasp_library.py` 输出 RL 的 `grasps.npz` 与原生扫描的 `grasps-native.npz`。分别传给 RL 的 `grasp.preprocessed_path` 和 `scripts/lastmile_benchmark/robocasa_curobo.py --grasps`。
- `scripts/operation/robocasa_replay_native.py` 读取原生 cuRobo 运行目录，输出逐控制步轨迹。`scripts/station_map/rl/robocasa_replay_mjlab.py --native` 读取这些轨迹，配合相应的 RL 场景、站位表、抓取池和评测配置生成比较记录。

历史抓取库及其命令见来源 worktree 的 `outputs/experiments/graspdatagen-grasp-library/README.md`。

## MuJoCo 3.16 核查

- 抓取库使用主环境的 MuJoCo 3.16.0。核对了模型编辑、射线和姿态接口，并读取了两套已有 NPZ。两套库各有 16 个物体系 TCP 位姿，RL 的 `source_rows` 与原生的 `grasp_id` 一致。历史 cup_3 原生扫描发生在原生环境切到 3.16 前。
- pro5000 上的 mjlab 1.6.0 使用 MuJoCo/MuJoCo Warp 3.16.0。原生侧是 RoboCasa 1.0.1、robosuite 1.5.2 和 MuJoCo 3.16.0。修正了版本查询及安装器中可选资产下载入口的冷导入顺序。兼容补丁在每个进程只安装一次。
- 本轮核查限于软件回归、命令行入口、导入和既有产物。抓取库在统一版本下的原生批量验收和 cup_5 原生扫描仍待验证。
- 相关 28 项软件测试通过。全量软件回归在合入前后均为 473 项、1 项断言失败、21 项错误及 3 项跳过。失败和错误涉及 cuRobo 依赖、PPO worker 和奖励测试接口。五个 CLI 的帮助入口、原生冷导入及上游资产下载器帮助入口均通过。
