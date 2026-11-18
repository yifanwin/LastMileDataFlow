# 全量抓取 FPS 与 Cup_30 可视化

- 状态：done
- 日期：2026-11-18
- 替代：15-shared-grasps-best.md 中的旧抓取筛选流程；其他历史结论保留
- 关联：../../../docs/runbook.md
- 完成时间：2026-11-18T12:40:22+08:00

## 目标与改动

作用于 `rl-cube-random-stations`、`rl-cube-contact`、`rl-ppo` 三个工作区，保留各自已有修改。共享 `load_grasps` 默认读取全部有效行，显式 `max_rows` 仍可用于场景存在性检查等小规模调用。共享 `select_grasps` 移除 Home TCP 参数，使用固定种子随机首点、全量位置/SO(3) FPS，时间 O(NK)、临时空间 O(N)，不建立 N×N 距离矩阵。尺度维持 0.10 m 等价 π/2；候选不足不重复补齐。

RL 新增 `grasp.fps_count`（默认16）、`grasp.fps_seed`（默认42），解析后记录到有效配置。只对物体计算一次 FPS，所有站位共用；候选表宽度为实际池大小。每回合独立均匀抽取目标且回合内固定。cuRobo 使用相同函数，`grasp_candidate_order: fps` 替换原 `tcp_distance`，新增 `grasp_fps_seed`，数量沿用 `max_grasp_candidates`；指定原始抓取重放与 facing 模式保留。Cube 专用解析抓取分支保持原行为。

新增静态可视化入口 `scripts/operation/visualize_grasps.py`。图中杯子使用 val103 的真实 visual mesh，16 个位姿采用与 RL 相同的 +π/2 roll；RGB 表示 TCP 三轴，U 形为示意夹爪，编号对应原 NPZ 行号。运行步骤见 runbook。

## 验证

三个工作区首轮定向测试各15项通过，覆盖全量候选、种子、旋转差异、刚体坐标变换、数量1/5/23及候选不足、显式加载上限、原行号、cuRobo接入、指定重放、RL均匀采样及部分reset、默认配置记录、分层。

补充的新可视化脚本及其他公开入口的工作区外 `--help` 检查每处1项通过，三处 `git diff --check` 均通过。

全套离线回归实际执行结果：

| 工作区 | 总数 | 通过 | 失败 | 错误 | 跳过 |
| --- | ---: | ---: | ---: | ---: | ---: |
| rl-cube-random-stations | 403 | 390 | 1 | 9 | 3 |
| rl-cube-contact | 386 | 373 | 1 | 9 | 3 |
| rl-ppo | 398 | 385 | 1 | 9 | 3 |

每处8个错误由缺少 curobo 导致，1个错误由缺少 rtree 导致；1个失败是缺少 curobo 先触发依赖异常，未到测试预期的 CUDA 异常。未将全套回归表述为通过，未为此安装或改动环境。首次使用 egl 在导入时遇到缺少 libEGL；静态检查与出图改用 `MUJOCO_GL=disable`。检查显式设置当前工作区 `PYTHONPATH`，避免现有环境的 .pth 指向其他工作区；contact 没有独立 .venv，复用 random-stations 的解释器并加载 contact 源码。

真实 Cup_30 文件共有1000个有效位姿。分别加载三个工作区源码与 val103 模型，核对 RL 局部位姿池、cuRobo 世界位姿池和图片保存的最终位姿，选取顺序与矩阵全部一致。原始行号依次为：

`89, 860, 757, 444, 615, 998, 717, 856, 119, 182, 645, 621, 833, 932, 556, 683`

## 产物与限制

唯一产物单元位于 `rl-cube-random-stations/outputs/experiments/grasp-fps-all/runs/val103-cup30/`，包含图片、精确位姿、输入/配置/seed、三处真实数据比对记录与回归日志；不复制大型资产或源码。其单元 README 记录三处 commit 与 dirty 状态。

本轮验证候选筛选、消费接口与静态几何可视化；没有启动训练、GPU rollout 或 cuRobo 真实轨迹规划，不证明这16个抓取可达、无碰撞或策略抓取成功。

## 杯子与位姿统一映射（2026-11-18T12:47:53+08:00）

按用户补充要求，三处可视化同步使用 `T_world_grasp = T_world_object @ T_object_grasp`，visual mesh 使用相同变换；显示时两者共同减去杯子原点，保留场景世界轴朝向与实际比例，使杯子按 val103 的原始姿态竖直显示。去掉大夹爪线框与外围长引线，改为直接在杯子上叠加16个TCP位置和RGB姿态轴，短标注区分编号。筛选结果和RL训练位姿未改动。

新图及映射检查位于同一产物单元的 `runs/val103-cup30-aligned/`，旧run保留。额外导出 `world_transforms` 与 `object_world`。真实模型核对：映射后的全部mesh三角顶点与MuJoCo世界坐标一致（误差低于1e-14 m）；16个局部位姿及原始行号与旧run完全一致，逆变换恢复局部位姿通过。三处抓取局部测试各6项通过，包含非单位旋转和平移下mesh/TCP共享变换测试；未重复全套回归或启动训练。

## 原生夹爪与杯子叠加（2026-11-18T12:52:21+08:00）

按用户澄清，展示对象改为实际 Panda Hand 掌部和两根手指的 visual mesh，不再只显示位姿坐标轴。使用机器人配置 `configs/robots/panda_omron.yaml`、与RL一致的0.9几何缩放、全开手指；从模型测得 `T_root_tcp`，按 `T_world_root = T_world_tcp @ inverse(T_root_tcp)` 放置每个夹爪。候选和TCP最终roll保持不变。三个工作区同步新增 `--robot-config` 必填参数及 `--robot-scale`（默认0.9）。

产物run为 `val103-cup30-grippers`：`grasps.png` 在同一个杯子上半透明叠加全部16个真实夹爪，`individual-grasps.png` 为4×4逐个夹爪与杯子的同框图，解决叠加遮挡。每个夹爪包含8326个原生mesh三角面，未用示意U形或重设TCP来贴合杯子。真实模型检查的原生世界mesh顶点误差为2.22e-16 m，16个root-to-TCP映射误差为1.11e-16；原始行号和局部TCP矩阵与上一run完全一致。检查记录为 `gripper-mapping-check.json`。静态放置不代表接触或规划成功。

本轮三处相关抓取测试各6项通过，diff格式检查通过；原生夹爪图实际生成并目视核对，逐个图保留模型表面明暗以辨认手指和杯口。未重跑全套或启动训练。

## row0单独可视化（2026-11-18T13:36:28+08:00）

按用户要求直接读取原始抓取库row0，沿用局部Z+90°、原生Panda Hand 0.9缩放、双指全开与原生TCP偏移补偿。新run `val103-cup30-row0` 的 `row0.png` 显示同一夹爪/杯子映射的两个视角；`row0.npz` 保存原始和最终矩阵，放置映射误差为0。无位置移动、无需重跑FPS、训练或整套测试；未修改源码。
