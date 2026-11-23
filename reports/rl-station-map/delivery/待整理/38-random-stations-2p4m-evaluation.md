# 随机站位240万checkpoint四卡评测

- 状态：done
- 日期：2026-11-17
- 时间戳：2026-11-17T23:06:35.684778+08:00
- 替代：无
- 关联：[800万训练](37-random-stations-8m-training-launch.md)

用户指定仅评测240万checkpoint，并使用CUDA0–3。原240万/280万顺序评测已取消，旧目录保留；280万未评测。原训练继续运行，未修改运行源码。

## 评测与结果

Checkpoint：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/ckpts/step-002400000.pt`，2,400,000条采样、18,593次SAC更新。配置与checkpoint一致；69维输入、固定物理底盘、随机正对目标站位。四卡分别使用seed 20261109、20262109、20263109、20264109；每卡从有效池无放回抽20站，每站一次，stochastic Actor，共80次，跨种子覆盖59个不同站位。成功要求沿用双指接触、相对抬升至少1cm、保持0.25秒；collision_free另行统计，历史发生碰撞仍可能达到成功条件。

成功80/80，无碰撞成功67/80，基础设施失败0。平均原始回合回报44.009705；各试验最大抬升范围[0.022001683712005615, 0.05843055248260498]m，最大保持时间范围[0.25, 0.2980000078678131]秒。四卡各20/20成功。该结论仅覆盖本次固定种子站位，不代表全站位或无限重复成功率。

## 视频与验证

产物：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/videos/20261117-230223-eval-2p4m-g0123`。80个MP4均存在且非空，每个视频并排展示全景和夹爪近景；保存全部逐站统计、selection、replay及有效启动参数。按时长选最短、中位、最长三个代表视频，均完整解码通过，并提取中位视频预览。视频文件名前缀18593是优化更新数，不是采样条数。

渲染启动首次因libEGL.so.1缺失加载路径失败；仅为评测进程增加已有`/data1/wenyifan/opt/lib`，实际查询EGL映射GPU0–3对应设备7、6、5、4后重启。四卡评测均退出0。未修改源码或依赖安装。

代表视频：

- `/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/videos/20261117-230223-eval-2p4m-g0123/gpu1-seed20262109/views/actor-000000018593-train-S0059-y00-stochastic-t009.mp4`，2.10秒。
- `/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/videos/20261117-230223-eval-2p4m-g0123/gpu1-seed20262109/views/actor-000000018593-train-S0075-y00-stochastic-t006.mp4`，2.80秒。
- `/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/videos/20261117-230223-eval-2p4m-g0123/gpu3-seed20264109/views/actor-000000018593-train-S0226-y00-stochastic-t005.mp4`，5.35秒。
