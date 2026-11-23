# cuRobo 方块固定站位 10 次 pick 实验

- 状态：done（10次试验完成，完整pick成功0/10）
- 日期：2026-11-17
- 替代：无
- 关联：[cube v2任务](20-cube-contact-lift-v2.md)、[训练启动记录](21-cube-lift-v2-training-launch.md)
- 执行准备时间：2026-11-17T09:10:19.638310+08:00
- 结果验收时间：2026-11-17T09:14:19.258085+08:00

## 目标与配置

在用户指定的 `.worktrees/rl-cube-contact` 中，固定运行10次独立重置试验，种子42–51，不以额外成功样本替换失败。使用val103、原Cup中心XY替换为4 cm、0.1 kg freejoint方块；S0246 XY=(6.613459459, 2.598811340)m，yaw=-0.620249486rad，升降0.2m、机器人缩放0.9。

主标准为现有 `strict-pick-v3`：两指实际承力、无支撑、目标相对初态抬升至少5cm，连续保持2s，且满足固定部件和相对滑移约束。附加标准为当前 `rl-pick-contact-v2`：同一物理子步两指法向力各>1e-6N且相对重置初态抬升至少8.6mm；不附加保持条件。

基于 `configs/station_map/panda_omron_curobo.yaml`，显式设置 `operation_base_mode=fixed`、`operation_retries=0`。保留16条最大候选、32个IK seed、4个TrajOpt seed、原生最多5次尝试和300s单次时限。现有流程会筛选实际8个方块包围盒候选；三阶段抓取之后，现有闭爪持物模型规划TCP向上10cm。候选来自方块中心，不读取原Cup标注。

## 实施与环境

- 源码commit：`551ac842913d8c91d9fc0735d5192ca443fe7a1f`。运行前已有19、21号报告修改，原样保留；没有修改训练源码、默认配置或公共API。
- 专用脚本保存在run内，复用 `replace_with_cube`、2m局部场景裁剪、`grasp_candidates`、`CuroboAttempt`、独立episode隔离、原生MuJoCo执行和记录器。仅继承试验外壳，在结束时保存逐物理子步证据；不改变规划或控制行为。
- 原始环境 `.venv` 无需安装依赖：MuJoCo 3.16.0、torch 2.7.1+cu128，固定cuRobo V2 0.8.0。`PYTHONPATH` 指向本worktree；CUDA与EGL通过现有绑定函数选择物理GPU0。
- 启动前GPU0–3利用率0%、占用0MiB；GPU4–7有其他任务，未干预。进程总时限4000s，实际正常退出。
- 方块准备阶段静置1s，共500物理步，目标稳定且无初态机器人碰撞。目标初始中心z=0.770270785411m。静置仅对目标检查稳定，不声称所有场景物体均已稳定。

## 真实结果

**完整pick成功0/10；cube v2达标0/10。10次均完成抓取规划、接近与闭爪，在 `lift_actual` 持物抬升规划阶段返回 `planning_no_solution`，没有执行抬升轨迹。**

| 次数 | Seed | 完整pick | cube v2 | 终止阶段 | 运行耗时(s) |
| --- | --- | --- | --- | --- | --- |
| 1 | 42 | 失败 | 否 | lift_actual | 20.79 |
| 2 | 43 | 失败 | 否 | lift_actual | 15.72 |
| 3 | 44 | 失败 | 否 | lift_actual | 15.70 |
| 4 | 45 | 失败 | 否 | lift_actual | 15.87 |
| 5 | 46 | 失败 | 否 | lift_actual | 15.63 |
| 6 | 47 | 失败 | 否 | lift_actual | 15.83 |
| 7 | 48 | 失败 | 否 | lift_actual | 15.88 |
| 8 | 49 | 失败 | 否 | lift_actual | 16.09 |
| 9 | 50 | 失败 | 否 | lift_actual | 15.77 |
| 10 | 51 | 失败 | 否 | lift_actual | 15.59 |

试验外壳累计耗时162.88s（不含初次场景构建和事后验收）；每次仿真推进8.2s，164个控制步，包含1s操作前静置。各次选中的程序抓取row均为4；更换seed未产生不同的已执行轨迹，因此这是同一固定初态与相同已选抓取的重复表现，不能解释为10个不同场景或抓取方案的覆盖率。

- 逐物理子步证据显示，每次确有双指承力，首例闭爪末两指约19.42N/19.49N；目标仍受桌面支撑。最大相对抬升仅约0.051239mm，不满足任一抬升标准。
- 抓取的goalset、pregrasp和grasp阶段均规划成功；抬升目标IK成功、TrajOpt失败。日志出现 `Start or End state in collision`。直接阻塞是抬升轨迹规划失败，具体碰撞对及模型误差根因尚未定位，不能把该提示直接解释为真实物理碰撞或任务不可行。
- 每次实际执行期间非法接触计数均为0，仿真健康标记均有效。此结论只覆盖已执行的接近和闭爪，不包含未执行的抬升。

## 验证与产物

- 10条结果、seed42–51均齐全；全部重置初态的qpos、qvel、ctrl逐值一致。
- 物理证据与20Hz轨迹记录合并核查：底盘最大平移0.062857mm，最大yaw偏移1.35501212e-05rad，升降最大偏移0.020892mm，均满足现有固定部件容差。
- 10次记录均完整，保存同版本MJB/XML、初始状态与轨迹；记录器标记 `replay_complete=true`。本次没有另外执行轨迹回放，也未保存逐帧原始图片。
- 全部30个MP4完成全帧解码：每次全景、腕部、第三人称各一个，均165帧、20fps、约8.25s。抽查首例末段第三人称帧符合夹住但未抬升的物理记录；现有第三人称中文叠字存在缺字，视频画面及数值仍可查看。
- 没有修改生产源码，因此不重复运行全仓软件测试；验收基于真实10次GPU规划、物理证据和视频。

原始run：[配置、脚本、日志与全部试验](../../../outputs/experiments/curobo-cube-pick-s0246/runs/20261117-091019-s0246-fixed-10-g0/)。每次 `rollouts/trial-*/` 保存result、初态、规划trace、持物球体拟合信息、压缩物理证据、轨迹和视频。启动信息见 `launch.json`，有效配置见 `config.json`；`experiment.py` 是本次实验入口，命令与环境已完整记录。

统计：[summary.json](../../../outputs/experiments/curobo-cube-pick-s0246/analysis/20261117-091019-s0246-fixed-10-g0/summary.json)、[逐次CSV](../../../outputs/experiments/curobo-cube-pick-s0246/analysis/20261117-091019-s0246-fixed-10-g0/trials.csv)。[首例第三人称视频](../../../outputs/experiments/curobo-cube-pick-s0246/runs/20261117-091019-s0246-fixed-10-g0/rollouts/trial-01-seed-42/videos/third_person_camera.mp4)。分析脚本位于run内 `analyze.py`，验收输出位于 `verification.log`。

## 结论与限制

本次完成了用户确认的固定10次实验，但未获得成功pick。需要后续定位持物抬升规划起终态碰撞的具体来源；本轮没有关闭碰撞、放宽成功门限或修改规划器来制造成功，也没有扩展为新的参数搜索实验。当前有限预算失败不能证明固定站位物理不可抓取，更不能用本结果推断RL策略表现。

## 后续对照：2026-11-17

[关闭cuRobo碰撞后重跑](23-curobo-cube-pick-collision-off-10-trials.md)：同初态、同种子，保留MuJoCo物理碰撞，完整pick成功10/10；详见新报告。本报告保留原碰撞开启基线结论。
