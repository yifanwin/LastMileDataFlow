# 固定底盘 mjlab / SAC 研究入口

- 状态：active
- 日期：2026-11-12
- 替代：无
- 关联：../plan/01-implementation.md、../delivery/04-implementation.md

从 actor-critic 工作区根目录运行。实际验证状态见[训练接手报告](05-training-handover.md)；脚本抓取已有正例，学习策略仍未成功。

算法和训练数据流见[算法设计与训练核心 Pipeline](algorithm-training-pipeline.md)。

## 安装

```bash
scripts/install/rl.sh /home/wenyifan/wenyifan/lmdf/LastMileDataFlow/reference_repos/mjlab cu130
.venv/bin/python scripts/station_map/rl/train.py --help
```

研究环境使用 Python 3.11、本地 editable mjlab 和源码 `.pth`。直接使用 `.venv/bin/python`；如使用 uv，传 `uv run --no-sync`。不要对研究环境执行根项目默认依赖同步。

镜像源可通过 `RL_INDEX_URL` 指定，已下载的 wheel 目录可通过 `RL_WHEEL_DIR` 指定。主机缺少 GLVND 用户态库时，`rl.sh` 调用 `scripts/install/egl.sh` 把 Ubuntu 包中的库解压至 `.venv/lib/native`，并在 `.venv/bin/activate` 里接上 `LD_LIBRARY_PATH`，不改系统驱动；需要 EGL 时先运行：

```bash
source .venv/bin/activate
```

不渲染的 smoke / 训练可设置 `MUJOCO_GL=disable`。缓存目录可设置在 `/tmp`：

```bash
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR=/tmp/lastmile-mpl
export WARP_CACHE_PATH=/tmp/lastmile-warp-cache
export CUDA_CACHE_PATH=/tmp/lastmile-cuda-cache
export XDG_CACHE_HOME=/tmp/lastmile-cache
```

## 选卡与真实 smoke

每次启动查询实时资源，选择低利用率且空闲显存足够的卡。下方的 4 只是本次空闲卡示例。`--gpu` 填物理卡号，入口在 CUDA 初始化前用 `CUDA_VISIBLE_DEVICES` 把进程限制在这张卡上（进程内逻辑卡号为 0），不会在其他卡上建立上下文。

```bash
nvidia-smi --query-gpu=index,utilization.gpu,memory.free --format=csv
MUJOCO_GL=disable .venv/bin/python tests/smoke/rl.py --upstream --gpu 4 --steps 5
MUJOCO_GL=disable .venv/bin/python tests/smoke/rl.py --gpu 4 --num-envs 1 --steps 5
MUJOCO_GL=disable .venv/bin/python tests/smoke/rl.py --gpu 4 --num-envs 32 --steps 5
MUJOCO_GL=disable .venv/bin/python tests/smoke/rl.py --gpu 4 --scripted --station-id <合法站位ID>
```

上游入口运行本地 mjlab 的 YAM lift-cube 环境，验证安装与 stepping，不验证 Panda 抓取。项目 smoke 使用真实 val103 / Cup_30；普通 smoke 检查有限观测与局部 reset，`--scripted` 通过同一套 7D / DLS / 伺服控制，不调用规划器。

## 配置与训练

配置为 `configs/station_map/rl/val103_cup30.yaml`，数据根为 `/data1/wenyifan/lmdf/molmospaces_data`，抓取变换来自该根下的 Cup_30 抓取库固定 row。修改抓取行、奖励、控制或 split 后，作为新的有效配置重新记录。

当前动作每轴2cm / 5°，回合60s（1200步），控制20Hz。γ已从0.995调整为0.999，属于待实测新配置；旧训练checkpoint均为1cm / 5° / 20s / γ=0.995。配置变化后从头训练，`--resume`要求有效配置完全一致。

随机预热条数直接取 `training.warmup_min`（当前 20000），不再与回合长度绑定；旧实现为 `max(warmup_min, 环境数×回合步数)`，128 环境 / 60 s 时达 153600 条，超过 Replay 容量。总量和每向量步更新次数需按实验显式设定，并记录实际更新数。配置了 `debug_station_ids` 时，每次评估另在这些训练站位上各跑一遍随机动作和均值动作（`tanh(mu)`），记录到达位姿、双指接触、抬升 1 cm、最长保持和完整成功，写入 `metrics.jsonl` 的 `debug_stations` 字段；空间 validation 照旧。

正式实验先按仓库规则提交源码并固定 revision，在实验单元 README 记录状态、源码、输入、报告；运行期间不改源码。产物单元示例为 `outputs/experiments/val103-cup30-sac-20261109/`，训练结果放其 `runs/train/`。

长期实验从固定 commit 的 worktree 启动，记录路径和启动时工作区状态；运行期间不修改该工作区，也不复制源码。

机器人尺寸由 `scene.robot_scale` 显式指定（当前 0.9，相对于原始 factory XML）。更改该值即可重新构建训练和评估模型；夹爪初态、动作上下限、升降位置归一化和底盘支撑半径从实际模型读取，不需同步修改尺寸常量。显式指定的 Home 升降高度仍以米计，需处于新模型范围内。

站位采样通过 `stations.robot_config` 读取 `configs/robots/panda_omron.yaml`：圆盘半径取 `arm_working_reach_m`，网格间隔取 `base_radius_m × 0.25`，不再在 RL 配置中填写固定米数。当前为 0.8469596623341055 m / 0.09861573150365006 m；圆心及 9 档朝向规则不变。两个机器人尺寸字段是实际尺寸，不重复乘 `scene.robot_scale`；改变模型比例时需同步更新机器人配置中的臂展与底盘半径。训练、评分和验收都解析同一配置，有效半径与间隔随 checkpoint 保存；机器人配置变化后，旧 checkpoint 的配置一致性检查会拒绝恢复或评分。新网格须重新生成，历史运行的站位数量不作为新网格的合法数量。

尺寸变化后使用新 run 从头训练；该字段随有效配置进入 checkpoint，resume / 评分 / 评估要求相同配置。缺少该字段的旧配置需明确补齐其原始实验尺寸，勿直接补成当前尺寸来复用旧结果。本研究入口不调用 cuRobo；主链规划 URDF 和碰撞球仍是 main 的 0.9 资产，不随 RL 实验比例改写。

```bash
MUJOCO_GL=egl .venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cup30.yaml --gpu 4 --seed 42 \
  --output outputs/experiments/val103-cup30-sac-20261109/runs/train \
  --max-transitions 200000
```

训练启动不评估、不录视频，也不保存 `initial.pt`，直接开始采样和随机预热。输出 `latest.pt`、`best.pt` 和 `metrics.jsonl`；首次日志 checkpoint 前没有权重文件。训练期间只保存 latest；结束时先保存最终 latest，再独立验证最终策略一次。best.pt 保留为已验证最终模型的同内容权重文件，不再比较历史周期模型；恢复训练也不沿用旧 best 的排名。checkpoint 包含有效配置、字段、运行版本、源码 revision、RNG、优化器和计数器；不保存 Replay / 环境内存。

恢复时增加 `--resume <latest.pt>`，`--max-transitions` 为累计目标；恢复参数 / 优化器后重新 reset 并 warmup Replay，不承诺逐步精确续跑。

## 只训练 S0246-y04

使用 `configs/station_map/rl/val103_cup30_s0246_y04.yaml`。该配置显式指定 `stations.fixed_station`，恢复历史世界位姿 `(6.613459458748234, 2.5988113404423916, -1.5707963267948966)`，不依赖当前网格是否存在这个编号。所有 Sampler 的 128 个环境均在同一位置和朝向重置，物体初态和 Home 沿用现有流程，保留物理合法性检查。

```bash
# 先查询实时资源；以下用当次空闲的物理 GPU 6 举例。
nvidia-smi --query-gpu=index,utilization.gpu,memory.free --format=csv
MUJOCO_GL=egl OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
LD_LIBRARY_PATH="$PWD/.venv/lib/native:${LD_LIBRARY_PATH:-}" \
.venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cup30_s0246_y04.yaml \
  --gpu 6 --seed 42 \
  --output outputs/experiments/fixed-station-sac/runs/s0246-y04
```

默认预算仍为 200000 条健康经验、随机预热 20000 条，每新增 128 条更新一次，预期 1406 次更新；batch 256、Replay 100000、动作、奖励、回合和 SAC 参数与当前默认配置相同。需要更多经验时显式传 `--max-transitions`。从头训练，不恢复其他站位/网格配置的 checkpoint。

仅在训练结束后评估该站位：1 个评估环境、16 次 stochastic rollout，另保留 stochastic / mean 视频。评估日志标记 `scope: fixed_station`、`split: train`，Q 文件也只含这一站。成功率反映该训练站位的表现，单站位 Spearman 为 null，不代表空间泛化或全图验收。未指定 `stations.fixed_station` 时仍使用下述空间 validation 流程。实现及验证见[单站位交付记录](14-fixed-station-training.md)。

## 六项监控与四组图

`metrics.jsonl` 保存 `learner`、`episode`、`evaluation` 三类记录：Actor Loss / Critic Loss / Entropy 为日志间隔内更新均值；Episode Return 是实际回合奖励和；独立评估记录成功数、健康 trial 数、总 trial 数、基础设施失败及 Value-Success Spearman。

从头训练及恢复训练均不执行启动或周期评估；不再读取 training.eval_every。连续采样和学习至预算结束，关闭全部 Sampler 后独立运行一次 Validation。固定抽取 8 个合法 validation 站位，每站位 2 次 stochastic rollout；使用独立向量环境，只计算这些固定验证点的 Q 和实际成功率。acceptance 集合保留到最终验收。评估 seeds 固定，评估前后的 Torch RNG 恢复，避免改变训练采样序列。

结束后的 Validation 在 `views/` 生成四组 PNG：

| 文件 | 内容 |
|---|---|
| `loss-curves.png` | Actor Loss、Critic Loss |
| `exploration.png` | tanh 策略动作的 `-E[logπ]`，单位 nats，可为负数 |
| `training-effect.png` | 独立 validation 成功率、每回合累计奖励 |
| `station-evaluation.png` | 固定色标的 max-yaw Critic Value Map、真实 Value-Success Scatter 与 Spearman |

每次评估原始数据保存在 `analysis/validation-<transition数>.json`，包含实际 trials、站位成功率与固定验证点原始 Q。全部成功率相同或数据不足时，相关性记录为 null、图中标 N/A；不填成 0。监控曲线本身不代表策略或站位图已通过最终验收。

独立检查监控链路可运行下列真实 smoke；它只做两次诊断更新，不作为训练结果：

```bash
MUJOCO_GL=disable .venv/bin/python tests/smoke/rl.py \
  --gpu 4 --num-envs 8 --steps 5 --random-actions --learner-updates 2 --monitor \
  --output <smoke单元>/runs/smoke.json
```

## 冻结评分与验收

```bash
MUJOCO_GL=disable .venv/bin/python scripts/station_map/rl/score.py \
  --gpu 4 --checkpoint <训练checkpoint> --output <实验单元>/analysis/q
MUJOCO_GL=disable .venv/bin/python scripts/station_map/rl/evaluate.py \
  --gpu 4 --checkpoint <同一训练checkpoint> --scores <实验单元>/analysis/q/station-q.json \
  --output <实验单元>/runs/evaluation
MUJOCO_GL=disable .venv/bin/python scripts/station_map/rl/evaluate.py \
  --gpu 4 --checkpoint <initial.pt> --baseline --scores <实验单元>/analysis/q/station-q.json \
  --output <实验单元>/runs/baseline
```

评分使用在线 Q1/Q2，K=16 是动作采样数。输出原始 Q、动作评分标准差、站位 split 和 invalid mask；图不代表成功概率。

评估先保存 `selection.json`，再保存 trial JSONL、状态 / 动作 NPZ 和统计。独立基线对照仍可使用已有的历史初始化 checkpoint；本训练入口不再自动生成初始化基线。抓取成功、无非法碰撞抓取成功、Q 排序分别判定；不能仅凭图有颜色或 Q 增长通过验收。

## 软件检查

```bash
MUJOCO_GL=disable .venv/bin/python -m unittest \
  tests.policies.test_sac tests.runtime.test_pick_state \
  tests.runtime.test_dls tests.scenes.test_rl_observation \
  tests.data.test_station_q tests.workflows.test_rl_evaluation -v
```

原主链全套软件回归仍使用主 checkout 的原环境，避免 MuJoCo / cuRobo 版本混装。真实 GPU rollout 和训练不随 unittest 自动运行。

## 阶段奖励与视频（2026-11-11）

新训练使用 `rl-pick-1cm-1s-v1`：双指承力、离开支撑并抬升 1 cm 连续保持 1 s；不限制物体相对 TCP 滑移。非 RL 默认 `strict-pick-v3` 保留。Actor 仍为 7D，DLS 联合控制臂和升降，升降命令限制 0.05 m/s。观测新增升降、实测 TCP 速度、接触事件标记与历史最高进度；旧 checkpoint 不直接 resume。

`reward_component_names` 对应 13 项实际奖励；新增 `views/reward-breakdown.png` 绘制指定 7 项已完成健康回合均值，日志保留接触事件次数。无已完成回合的区间不补零。

视频需启用 EGL 并设置上文 native 库路径。训练中不录评估视频；结束后的单次 Validation 保留固定 train / validation 各一站的 stochastic / mean 视频，并保留首次成功或碰撞代表轨迹；启动时没有评估、视频或视频专用 rollout。视频在 `views/`，状态在 `replays/`，引用、结果及渲染耗时在已有 validation JSON。视频源为实际 rollout 的状态回放；终止帧与 Home 都保留，编码时长比仿真时间多一个 0.05 s 初态帧。全景复用已有 `ThirdPersonCamera` 的分割可见性选角和机器人／目标联合取景，近景使用同一已有选角器对夹爪／目标联合取景（margin 4 cm）；同站位从相同 Home 选取相同初始角度。


## 分布式采样与集中学习

单卡继续用 `--gpu 0`；多卡用 `--gpu 0 5`（以实时空闲情况选卡）。每卡一个独立 Sampler，Learner 与首卡 Sampler 共享 GPU；CPU Replay 和有界队列集中汇总，启动后布局固定。

```bash
.venv/bin/python scripts/station_map/rl/train.py --gpu 0 5 --num-envs 128 \
  --output outputs/experiments/distributed-sampling/runs/train
```

`--num-envs` 是每个 Sampler 的环境数。配置 `transitions_per_update=128`，全局健康经验前 20000 条预热，此后每 128 条更新一次；默认 batch=256、CPU Replay=100000。每 16 次更新向各 Sampler 发布最新完整 Actor。恢复 checkpoint 时保留历史 transition 横轴，重建 Replay 并重新全局预热。

仅训练结束后的单次 Validation 评分固定 validation 子集；训练期间不暂停采样验证。之后使用同一个最终策略计算一次全合法站位 Q，写入训练目录 `station-q.json` / `station-q.png`；非法站位保留空值。该图不代表抓取验收通过，acceptance 仍使用独立 evaluate 入口。
