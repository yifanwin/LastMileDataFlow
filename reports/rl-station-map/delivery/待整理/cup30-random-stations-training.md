# Cup_30真实物体随机站位接触抬升训练

- 状态：active
- 日期：2026-11-18
- 时间戳：2026-11-18T21:33:54.954607+08:00
- 替代：无
- 关联：[预处理](cup30-grasp-preprocessing.md)、[运行指南](../../../docs/runbook.md)
- 工作区：/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cup-random-stations
- 分支：codex/rl-cup-random-stations
- 基线提交：69208c02ff8b0741f3f840eed75b5198767ce186

## 目标与改动

恢复val103/Cup_30真实模型，沿用当前方块的接触抬升协议、奖励、69维输入布局和7维动作。每回合随机有效站位，朝向目标，底盘物理固定，机械臂从Home开始；另从共享16条子集中均匀随机选择一条抓取，回合内固定。物体及其初始摆放不另加随机扰动。

新配置 `configs/station_map/rl/val103_cup30_random_stations_facing.yaml` 显式引用源工作区的128条预处理库，以sample_count=16、sample_seed=42均匀无放回抽样。复用现有加载、抽样和回合重置，局部Z +π/2只应用一次。真实物体接触训练不允许回退到原始抓取库或二次FPS；旧graph任务与方块解析抓取保留原行为。

物体局部尺寸来自碰撞网格包围范围，最低点使用目标子树所有碰撞网格顶点的实际刚体变换，包括几何偏移；本次Cup_30全为mesh。桌面高度来自原始parent支撑面的几何与射线检查，保持固定。观测及抬升奖励、成功判定共用最低点。方块保持原计算结果和既有离线评分；真实杯子暂不扩展无仿真Q图入口。

成功为同一物理子步左右手指法向力各>0.5 N、最低点离原桌面≥1 cm、无支撑接触，连续保持0.25 s，且无控制失败或仿真异常。保留当前七项奖励和权重，碰撞与无碰撞成功率单独记录。checkpoint和grasp-subset.json保存16条原始row、来源及未叠加roll的局部矩阵；episode和评估视频记录row。独立评估的候选与动作使用每trial独立随机流。

## 软件验证

相关55项测试通过；补充物体姿态变换测试后，RL目录134项全部通过，抓取重置与分层最终检查7项通过。覆盖预处理加载与roll一次、16条共享子集、均匀抽样与部分reset、trial分批不改变候选、偏心mesh/子body/倾斜最低点、视觉几何排除、69维输入和保持条件。

全仓离线回归执行416项：403通过、3跳过、9错误及1失败。未通过项全部位于planning，当前研究环境缺curobo（8项错误和1项预期异常文本失败）与rtree（1项错误），不声明全套回归通过。日志在短验证run/regression.log。首次EGL环境缺动态库路径，补充研究环境.venv/lib/native后已能运行真实GPU和完整软件回归，不改共享环境。

## 真实GPU短验证

运行产物：`outputs/experiments/rl-cup-random-stations/runs/20261118-smoke/`。16环境覆盖全部16候选，检查实际源码路径、有限69维观测、局部reset不改变其他环境候选、GPU最低点与CPU MuJoCo几何一致，随后运行完整随机未训练Actor回合并保存一个代表视频。已通过，完成时间为2026-11-18T21:33:18.575341+08:00。16条各完成1000步（50 s）回合，无基础设施异常，最大底盘漂移0；未训练Actor成功0/16。CPU/GPU最低点误差<1e-5 m，局部reset保持其他环境候选不变；已生成并目视检查1001帧、50.05 s代表视频。目标碰撞几何去重后1292顶点，局部尺寸约[0.124412, 0.098028, 0.124470] m，原桌面高度0.7503371 m。

## 正式训练与独立评估

已确认从零SAC训练800万条健康经验，4卡各128环境，batch512、replay100,000、全局预热20,000、每128条更新一次，预计62,343次更新。seed42；TensorBoard每30秒汇总；关闭周期评估。启动前再次检查卡负载，固定训练提交，训练期间不修改源码。

正式run：`outputs/experiments/rl-cup-random-stations/runs/20261118-213047-cup30-8m-g0123/`。launch.json保存精确命令和环境，run.py顺序运行训练及独立best.pt评估，status.json记录当前阶段；训练结束自动加载以完整100个健康非预热回合窗口成功率选出的best.pt，最多20环境执行20个随机有效站位回合，每个trial独立seed，失败不重试，保存全部视频与回放。结果见evaluation/summary.json与RESULTS.md。

正式启动、代码提交和初期采样/更新核查记录在上述run/launch.json与startup-verification.json，当前进度以status.json和metrics.jsonl为准。短验证不证明Actor学会抓取；训练期成功率与独立评估必须分开报告。
