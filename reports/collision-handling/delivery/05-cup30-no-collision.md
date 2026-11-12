- 状态：done
- 日期：2026-11-04
- 替代：无
- 关联：[近站位失败诊断](03-cup30-near-station-failures.md)、[碰撞体与查询梳理](04-collider-source-and-query-review.md)、[原始默认方案结果](02-structured-environment.md)

# val103 / Cup_30：关闭规划碰撞标定与视频

## 结果

按用户要求，在 `.worktrees/collision-handling` 中关闭规划碰撞，重跑 S0022、S0030、S0038，各 3 次。保留 MuJoCo 物理碰撞与抓取接触。最终 **1/9 成功、7/9 执行跟踪超时、1/9 执行前规划无解**；8 次进入实际执行，8 段实测回放视频全部完成并通过解码检查。

| 站位 | 原默认方案 | 关闭规划碰撞 | 进入执行 | 本轮失败 |
|---|---:|---:|---:|---|
| S0022 | 0/3 | 0/3 | 3/3 | 3 次 pregrasp 跟踪超时 |
| S0030 | 0/3 | 0/3 | 2/3 | 2 次 approach 跟踪超时；seed 82 在 grasp 阶段 TrajOpt 无解 |
| S0038 | 0/3 | **1/3** | 3/3 | seed 88 成功；其余 2 次 pregrasp 跟踪超时 |

原默认方案三站均在起点碰撞约束下无解。关闭检查后，三站所有 lane 的 goalset 和 pregrasp 规划均通过；S0030 seed 82 在后续 grasp 轨迹优化失败，其余 8 条进入物理执行。结果说明解除起点拒绝可使这些站位进入执行，但不能保证控制跟踪或任务成功。

## 设置与实际改动

- 基于工作区提交 `ab00659` 导出实验源码；补丁只作用于实验副本的 `planning/curobo/costs.py` 与 `planner.py`，生产源码和默认配置未改。
- 关闭环境、目标、把手、自碰撞的规划代价与约束，覆盖 IK、TrajOpt、metrics 和 PRM 图搜索，以及查询时新建的 ContactManager。保留位姿、关节运动限制、局部工作区和原有限求解预算。
- MuJoCo 物理碰撞、接触与任务判定保持开启；没有强制执行被判无解的候选轨迹。
- 站位、抓取候选、预算沿用 b6 merged_hull 基线；原 seeds 为 66–68 / 81–83 / 87–89。三份 preparation 的完整积分状态与基线逐值相同，最大差均为 0。
- 标定 GPU 4，单 planning worker、CPU worker、preparation worker；视频 GPU 5，数值记录后离线渲染。

最终实验单元：`outputs/experiments/cup30-no-planning-collision-20261104/`。

| 位置 | 内容 |
|---|---|
| `src/diagnostic.patch` | 实验补丁；其余源码从上述提交导出 |
| `inputs/s0022、s0030、s0038/` | 原站位、各自执行 seed 与配置，场景文件只读引用原冻结 MJB |
| `runs/nocollision-cup30-<站位>-20261104/` | 原始 trial、完整轨迹、准备状态与规划证据 |
| `replays/manifest.json` | 9 条 trial 的视频去向；seed 82 无执行步骤，无动作视频 |
| `analysis/summary.json` | 逐 trial 结果、同状态比较和控制帧接触检查 |
| `analysis/video-checks.json` | 8 段视频解码结果 |
| `logs/run.sh、render.py、summarize.py` | 本轮启动、渲染和汇总入口 |

## 逐次结果与视频

视频为三相机加俯视底盘轨迹的 2×2 布局，20 fps，来自实际保存的场景状态；离线回放不推进物理。

| 站位 | seed | 结果 / 停止阶段 | 实际控制步数 | 视频 |
|---|---:|---|---:|---|
| S0022 | 66 | 跟踪超时 / pregrasp | 157 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0022/r000000/grid.mp4`） |
| S0022 | 67 | 跟踪超时 / pregrasp | 157 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0022/r000001/grid.mp4`） |
| S0022 | 68 | 跟踪超时 / pregrasp | 157 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0022/r000002/grid.mp4`） |
| S0030 | 81 | 跟踪超时 / approach | 258 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0030/r000000/grid.mp4`） |
| S0030 | 82 | grasp 规划无解，未执行 | 0 | 无动作轨迹 |
| S0030 | 83 | 跟踪超时 / approach | 349 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0030/r000002/grid.mp4`） |
| S0038 | 87 | 跟踪超时 / pregrasp | 145 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0038/r000000/grid.mp4`） |
| S0038 | 88 | **成功 / hold** | 301 | 成功回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0038/r000001/grid.mp4`） |
| S0038 | 89 | 跟踪超时 / pregrasp | 143 | 回放（`outputs/experiments/cup30-no-planning-collision-20261104/replays/s0038/r000002/grid.mp4`） |

## 验证与限制

- 三个最终 run 均正常完成，9 条 trial、原 seeds 和初始状态完整保留。初始化检查器导出 `diagnostic_collision_costs.json`；ContactManager 初始化同时关闭查询中新建实例的碰撞项，避免仅修改初始实例的遗漏。
- 8 段视频共 1,675 帧，完整解码且帧数与回放记录一致；抽看 S0022 的中间帧确认三相机与俯视布局正常。不是只检查文件存在。
- 调用已有控制帧接触检查：S0022 三条、S0030 seed 81、S0038 seeds 87/89 检出桌子与底座 `mobilebase0_pedestal_feet_col` 接触；最大几何穿透分别约 0.06 / 0.03 / 0.32–0.36 mm。S0030 seed 83 和 S0038 成功 seed 88 在已记录控制帧未检出机器人–环境接触。该结果不覆盖控制帧之间的物理子步，也不能单凭浅接触认定跟踪超时的原因。
- 这是三站有限消融实验，不代表碰撞表示已修复，也不构成跨场景成功率结论；S0030 seed 82 的剩余轨迹无解和各条跟踪超时未进一步修复。
- 未修改生产代码，因此未重复离线全套回归；验证依据为本轮真实 GPU 标定、物理执行记录及视频解码。

## 执行过程中的修正

首次试跑只关闭了部分优化器项，遗漏 PRM 独立检查，已中止，保留在 `outputs/experiments/cup30-no-collision-20261104/`。第二次仅在构建时禁用检查器，后续诊断发现 TrajOpt 查询中新建的检查器仍有碰撞项，其结果和静态预览保留在 `outputs/experiments/cup30-collision-off-20261104/`。两者均标记 superseded，不计入上面的最终 9 条结果。

最终副本把碰撞关闭逻辑放入 ContactManager 初始化，并关闭图搜索检查器，再从原输入完整重跑。以上结果与动作视频全部来自第三个、已修正的实验单元。没有删除或覆盖既有运行结果。

## 后续失败原因诊断（2026-11-05）

七条跟踪失败已按实际轨迹定位：六条为参考底座姿态进入桌子、物理执行在接触边界滞留；S0030 seed 83 为末端 10.38 mm 的稳态残差。单点 120 步（6 秒）仍未满足收敛条件触发失败。详见[跟踪超时诊断](06-cup30-tracking-timeout.md)。没有修改控制参数或重新标定。
