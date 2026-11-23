# 最新 SAC 完整训练启动与设置

- 状态：active
- 日期：2026-11-12
- 替代：无
- 关联：[运行指南](running.md)、[分布式采样实施](09-distributed-sampling.md)
- 记录时间：2026-11-12 13:21:50 +08:00

## 本轮目标与运行版本

在 actor-critic 当前版本上从头训练 val103 / Cup_30 固定底盘抓取，使用 GPU 0–3。工作区 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/actor-critic`，commit `e7f873557f2b0714275606b385ebde3f5b7cb06c`，启动时 clean。未修改训练代码或默认配置；运行输入单独将总预算从 200000 改为 1000000。运行期间保持训练代码固定。

## 实际流程

1. 从真实 XML 准备 2 m 局部场景与 0.9 尺寸 Panda-Omron；生成站位并检查 Home 碰撞和地面支撑，不按 IK / 规划成功过滤。XY 半径 1 m、间距 0.1 m，朝向为面向目标加 -60° 到 60° 的 9 档偏角；按 0.3 m 空间块分 train / validation / acceptance，配置比例 0.7 / 0.15 / 0.15，seed 42。实际合法数量以本轮 checkpoint 元数据为准。
2. 首卡创建独立验证环境、共享 Actor 和独立双 Q，先保存 initial.pt 并运行初始验证。
3. 每卡一个独立 spawn Sampler；首卡同时承载 Learner。Sampler 通过 7D Base Frame TCP/夹爪动作、DLS 和真实伺服推进 mjlab。仅健康经验写入集中 CPU Replay，保存 reset 前的真实 next observation，故障行不入库。
4. 全局随机预热 20000 条；之后每 128 条健康经验更新一次。每 16 次更新发布完整 Actor 参数。多卡增加采样并行度，不增加每条经验的更新比。
5. 每 100000 条经验暂停所有采样器，固定 8 个 validation 站位、每站 2 次 stochastic rollout；validation 成功率优先、同率比较回报选择 best。初始、周期、末次评估保存实际状态回放视频；acceptance 不参与训练选择。
6. 到 1000000 条健康经验结束采样，预期更新 floor((1000000-20000)/128)=7656 次。最终载入 best.pt，生成全合法站位 Q 图。独立 acceptance 需另行运行 evaluate.py，本次启动不代表验收通过。

## 设置

| 项目 | 本轮设置 |
| --- | --- |
| GPU / 环境数 | 物理 0、1、2、3；各 128 环境，共 512；Learner 在 0 |
| 场景 | val103 / Cup_30，固定抓取库 row=0，robot_scale=0.9 |
| 控制 | 20 Hz；physics_dt=0.002 s × decimation=25；回合 60 s / 1200 步 |
| 动作 | 每轴平移 0.02 m、旋转分量 5°，连续夹爪；DLS damping=0.05，升降速度限制 0.05 m/s |
| 观测与网络 | 四类几何图节点 + 机器人/任务状态；Actor/Q1/Q2 各自独立 GAT 128 维 + 两层 256 维 MLP；tanh Gaussian Actor |
| Replay / batch | CPU 100000 / 256 |
| γ / τ | 0.999 / 0.005 |
| Actor / Critic / α 学习率 | 3e-4 / 3e-4 / 1e-4 |
| 温度 | 初始 α=0.1，自动学习，目标熵 -7 |
| 随机性 | 从头训练，seed=42；各 sampler seed=42+worker；无旧 checkpoint resume |
| 总预算 / 预期更新数 | 1000000 健康 transitions / 7656 |
| 日志 / 评估 | 每 10000 条日志与 latest；每 100000 条评估与视频，另含初始和结束 |
| 成功协议 | rl-pick-1cm-1s-v1：双指有效接触、脱离支撑、抬升至少 1 cm、连续保持 1 s；不限制相对 TCP 滑移 |

13 项奖励按实际代码：位置进度×10、姿态进度×0.5、每步碰撞 -2、限位权重 0、控制失败 -5、成功 +100；远处张开 0.02、近处位姿 0.05、减速 0.02、对准闭爪 0.05（这四项乘 policy dt）；首次近目标双指接触 +2、保持接触下历史最高抬升进度×5、双指离开支撑的持物时间×0.5。非法碰撞保留物理接触、惩罚和统计，不增加动作避碰投影。

## 运行产物与命令

单元 `outputs/experiments/distributed-sampling/`，本轮 run 为 `runs/train-20261112-132150-g0123-1m/`。有效输入 `input-config.yaml`，启动事实 `launch.json`（PID 2604257），日志 `stdout.log` / `stderr.log` / `metrics.jsonl`，权重 initial/latest/best.pt，视频与曲线 views/，逐次验证 analysis/，最终 station-q.json/png。

```bash
MUJOCO_GL=egl OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
LD_LIBRARY_PATH="$PWD/.venv/lib/native:${LD_LIBRARY_PATH:-}" \
.venv/bin/python -u scripts/station_map/rl/train.py \
  --config outputs/experiments/distributed-sampling/runs/train-20261112-132150-g0123-1m/input-config.yaml \
  --gpu 0 1 2 3 --seed 42 \
  --output outputs/experiments/distributed-sampling/runs/train-20261112-132150-g0123-1m
```

进程通过独立 session 在后台启动，启动环境和缓存路径完整保存于 launch.json。启动前 GPU 0–3 均 0% 利用率、约 72828 MiB 空闲；4–7 均 100%，未用于本轮。CPU available 约 680 GiB。

## 验证与边界

本次核对实际训练、SAC、Replay、奖励、站位和评估代码，确认研究解释器导入当前 actor-critic 源码。没有修改算法，无需重跑整套软件回归；真实启动与持续日志用于确认运行状态。

首次输入副本误用 JSON，入口仅接受 YAML，立即退出且未仿真；保留 `train-20261112-132054-g0123/` 原始报错。随后默认 20 万条启动 `train-20261112-132120-g0123/` 在用户明确完整训练后终止，原因记录 stopped.json；完整训练使用上述独立新 run，未复用或覆盖已有产物。

运行状态在下方随本次启动检查补充。尚无本轮训练效果或 Q 排序验收结论。

### 本次启动检查（2026-11-12T13:25:53.619835+08:00）

PID 2604257 存活，真实场景初始化及 initial.pt 保存完成，正在初始验证；stderr 仅有 TorchScript 弃用警告，未见 traceback。metrics.jsonl 尚为空，四个 Sampler 依代码顺序要在初始验证完成后才创建。因此当前确认的是后台训练任务已启动，不把采样、预热或参数更新描述为已完成。完整预算与后续流程已配置，无需再次手动启动。进程从独立 session 运行，结束本次对话不终止任务。

本轮初始 checkpoint 核对：2845 个候选、909 个合法站位（train 668 / validation 91 / acceptance 150），node_capacity=435、state_dim=75。当前默认 9 个朝向，不沿用旧实验的 16 档站位分母。运行版本：Torch 2.14.0+cu130、PyG 2.7.0、mjlab 1.6.0、MuJoCo/MuJoCo-Warp 3.16.0、Warp 1.19.0、NumPy 2.4.6。状态事实另存 run/startup-check.json。

未修改训练源码与仓库默认参数；新增本报告，实验单元 README 追加本轮版本、命令引用和三个启动 run 的真实状态。git diff --check 通过。后续评估、采样和学习结果以持续更新的 metrics.jsonl 为准。
