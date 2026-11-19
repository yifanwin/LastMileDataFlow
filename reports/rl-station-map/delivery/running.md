# 固定底盘 mjlab / SAC / PPO 研究入口

- 状态：active
- 日期：2026-11-17
- 替代：无
- 关联：[方块接触抬升v2](20-cube-contact-lift-v2.md)、[旧方块v1](19-cube-contact-single-station.md)、../plan/01-implementation.md、../delivery/04-implementation.md

从包含本文版本的 RL 工作区根目录运行。实际验证状态见[训练接手报告](05-training-handover.md)；脚本抓取已有正例，学习策略仍未成功。

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

## PPO 训练与评估

算法优先级为命令行 `--algorithm`、配置 `algorithm`、默认 `sac`。SAC 沿用原异步采样；PPO 每卡一个同步采样进程，全部采完当前策略版本的 rollout 后，由首卡集中更新 Actor / V，再同步下一轮。每卡环境数由 `--num-envs` 覆盖。

从当前工作区根目录执行，先查询实时 GPU 资源，下方卡号仅为示例。Cup_30 随机站位、物理固定底盘（与 rl-cup SAC 同一任务和奖励）：

```bash
PYTHONPATH=$PWD/src MUJOCO_GL=disable OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cup30_random_stations_facing_ppo.yaml \
  --gpu 0 1 2 3 --seed 42 --output outputs/experiments/ppo-cup/runs/<run>
```

`PYTHONPATH` 不能省：本工作区 `.venv` 链接到 actor-critic 环境，默认会导入那边的源码。该配置的 `grasp.preprocessed_path` 是相对路径，工作区需有 `outputs/experiments/grasp-fps-all`（本工作区为指向 rl-cup 同名目录的软链接）。与 SAC 配置的差别只有 `algorithm: ppo`、`ppo: {}` 和 `episode_s: 20`。cube 旧配置 `val103_cube_s0246_facing_ppo.yaml` 仍可用。原 SAC 配置也可直接加 `--algorithm ppo`。PPO 不使用 Replay、warmup、alpha、SAC batch_size 或 transitions_per_update。

PPO 参数默认值（2026-11-20 按[失败排查](../analyze/ppo-cube-s0246-failure-diagnosis.md)修改）：

| 字段 | 默认值 |
| --- | --- |
| `rollout_steps` / `epochs` / `minibatch_size` | 32 / 5 / 4096 |
| `actor_lr`（初值）/ `value_lr` | 0.0003 / 0.001 |
| `desired_kl` / `lr_min` / `lr_max` | 0.01 / 1e-5 / 0.01 |
| `gamma` / `gae_lambda` | 0.99 / 0.95 |
| `clip_coef` / `value_coef` / `entropy_coef` | 0.2 / 1.0 / 0.005 |
| `actor_max_grad_norm` / `value_max_grad_norm` | 1.0 / 1.0 |
| `init_std` / `normalize_observations` | 1.0 / true |

每轮仅使用当前版本策略产生的健康样本；先在全局样本上统一标准化优势，再每个 epoch 重新打乱、分 mini-batch 更新，尾批保留，epoch 不提前停止。每个 mini-batch 更新前计算新旧策略的精确高斯 KL（tanh 是双射，等于 squashed 策略 KL）：大于 2×`desired_kl` 时 Actor 学习率 /1.5，小于一半时 ×1.5，限制在 [`lr_min`, `lr_max`]。Actor 是 tanh 高斯，log std 为与状态无关的可学习参数（初值 `init_std`），输出层正交初始化增益 0.01；熵奖励用 tanh 前高斯熵。flat 输入经 Actor/V 共享的运行均值/方差归一化（std 下限 0.01、裁剪 ±10），统计量在每轮 epoch 结束后用本轮样本更新，随 checkpoint 保存并下发给 Sampler。GAE 不跨成功、失败或异常样本传播；episode timeout 结束回合但保留 V 引导；rollout 分段边界用下一状态 V 引导。Actor 与 V 分别裁剪梯度。

run 保存展开默认参数后的 `config.json`、`metrics.jsonl`、TensorBoard、`latest.pt`、`best.pt`、`grasp-subset.json` 与每40万样本的 `ckpts/`。best 规则与 SAC 相同（配置了 `training.best_checkpoint` 时按其窗口和起始门槛）。PPO 恢复参数、两个优化器、当前 Actor 学习率、归一化统计、RNG 与更新计数，重新 reset 环境和 rollout；使用 `--resume <latest.pt>`，`--max-transitions` 为累计目标。

PPO 曲线位于 `ppo/`：actor_loss、value_loss、entropy、minibatch_kl、global_kl、clip_fraction、explained_variance、actor_grad_norm、value_grad_norm、minibatches、actor_lr、policy_std。梯度范数为裁剪前范数，窗口内取各轮均值。成功率等回合指标按最近100回合写入 `train/`，底盘漂移写入 `mobile/`，奖励分项写入 `reward/`，运行指标在 `runtime/`，不生成 SAC 的 alpha 曲线。`updates` 表示 rollout 更新轮数，`optimizer_steps` 表示 Actor/V 成对 mini-batch 更新次数。

flat 使用已有固定初态20次评估及视频流程：

```bash
MUJOCO_GL=egl LD_LIBRARY_PATH="$PWD/.venv/lib/native:${LD_LIBRARY_PATH:-}" \
.venv/bin/python scripts/station_map/rl/evaluate.py \
  --config outputs/experiments/cube-ppo/runs/train/config.json \
  --checkpoint outputs/experiments/cube-ppo/runs/train/best.pt \
  --gpu 0 --output outputs/experiments/cube-ppo/runs/train/evaluation
```

graph 使用 `val103_cup30_s0246_facing_ppo.yaml`，训练结束加载 best 做评估并生成 `station-v.json/png`。也可用 `score.py` 对冻结模型独立打分，再向 `evaluate.py` 传 `--scores <station-v.json>`。PPO 的 `v_mean/v_std` 是各 grasp 初态 V 的等权均值与离散程度，不进行动作采样；评分、排名、相关性和图表均标明 V。SAC 保留原 Q 输出，旧 checkpoint 自动识别为 SAC。独立评估根据 checkpoint 算法选择模型，配置须与 checkpoint 的有效配置一致；训练时仅由 CLI 覆盖算法时，PPO 评估直接使用保存的 `config.json`。

本次实现与短验证见 [PPO 交付报告](36-ppo-integration.md)，不代表正式训练收敛。

## SAC 配置与训练

配置为 `configs/station_map/rl/val103_cup30.yaml`，数据根为 `/data1/wenyifan/lmdf/molmospaces_data`，抓取变换来自该根下的 Cup_30 抓取库固定 row。修改抓取行、奖励、控制或 split 后，作为新的有效配置重新记录。

当前动作每轴2cm / 5°，回合60s（1200步），控制20Hz。γ已从0.995调整为0.999，属于待实测新配置；旧训练checkpoint均为1cm / 5° / 20s / γ=0.995。配置变化后从头训练，`--resume`要求有效配置完全一致。

随机预热条数直接取 `training.warmup_min`（当前 20000），不再与回合长度绑定；旧实现为 `max(warmup_min, 环境数×回合步数)`，128 环境 / 60 s 时达 153600 条，超过 Replay 容量。总量和每向量步更新次数需按实验显式设定，并记录实际更新数。配置了 `debug_station_ids` 时，每次评估另在这些训练站位上各跑一遍随机动作和均值动作（`tanh(mu)`），记录到达位姿、双指接触、抬升 1 cm、最长保持和完整成功，写入 `metrics.jsonl` 的 `debug_stations` 字段；空间 validation 照旧。

正式实验先按仓库规则提交源码并固定 revision，在实验单元 README 记录状态、源码、输入、报告；运行期间不改源码。产物单元示例为 `outputs/experiments/sac-history/val103-cup30-sac-20261109/`，训练结果放其 `runs/train/`。

长期实验从固定 commit 的 worktree 启动，记录路径和启动时工作区状态；运行期间不修改该工作区，也不复制源码。

机器人尺寸由 `scene.robot_scale` 显式指定（当前 0.9，相对于原始 factory XML）。更改该值即可重新构建训练和评估模型；夹爪初态、动作上下限、升降位置归一化和底盘支撑半径从实际模型读取，不需同步修改尺寸常量。显式指定的 Home 升降高度仍以米计，需处于新模型范围内。

站位采样通过 `stations.robot_config` 读取 `configs/robots/panda_omron.yaml`：圆盘半径取 `arm_working_reach_m`，网格间隔取 `base_radius_m × 0.25`，不再在 RL 配置中填写固定米数。当前为 0.8469596623341055 m / 0.09861573150365006 m；圆心及 9 档朝向规则不变。两个机器人尺寸字段是实际尺寸，不重复乘 `scene.robot_scale`；改变模型比例时需同步更新机器人配置中的臂展与底盘半径。训练、评分和验收都解析同一配置，有效半径与间隔随 checkpoint 保存；机器人配置变化后，旧 checkpoint 的配置一致性检查会拒绝恢复或评分。新网格须重新生成，历史运行的站位数量不作为新网格的合法数量。

尺寸变化后使用新 run 从头训练；该字段随有效配置进入 checkpoint，resume / 评分 / 评估要求相同配置。缺少该字段的旧配置需明确补齐其原始实验尺寸，勿直接补成当前尺寸来复用旧结果。本研究入口不调用 cuRobo；主链规划 URDF 和碰撞球仍是 main 的 0.9 资产，不随 RL 实验比例改写。

```bash
MUJOCO_GL=egl .venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cup30.yaml --gpu 4 --seed 42 \
  --output outputs/experiments/sac-history/val103-cup30-sac-20261109/runs/train \
  --max-transitions 200000
```

训练启动不评估、不录视频，也不保存 `initial.pt`，直接开始采样和随机预热。输出 `latest.pt`、`best.pt`、`ckpts/step-<实际累计步数>.pt` 和 `metrics.jsonl`。每次日志 checkpoint 保存 latest；每次监控刷新有新合格训练回合时，按与 `train/success` 相同的最近至多 100 回合成功率选择 best，严格提高才替换，同分保留，不足 100 回合使用实际数量。结束尾段参与比较；全程没有合格回合才以最终模型兜底，再独立加载 best 评估一次（方块 contact 协议不执行终点评估）。恢复训练重新建立回合窗口，仅继承 `recent_100_episode_success_rate` 口径的 best 排名；旧 return 或区间成功率重新选模。checkpoint 包含有效配置、字段、运行版本、源码 revision、RNG、优化器和计数器；不保存 Replay / 环境内存。

每跨过累计 400000 条健康 transition，独立保存一次完整 checkpoint 到 `ckpts/`，全部保留；以接收该批次并完成对应更新后的实际步数命名，例如 `ckpts/step-000400000.pt`。批量采样可能略超阈值，后续阈值仍固定为 800000、1200000 等。恢复训练按累计步数定位下一个 40 万阈值。恢复 latest 时仅保留 recent_100_episode_success_rate 口径排名；恢复 ckpts 历史快照时重新选模，避免引用已被后来训练覆盖的根目录 best。历史 run 的权重不自动改写。
Cup 随机站位配置的 best 规则（2026-11-19）：`training.best_checkpoint` 显式设置 `window_episodes: 200`、`start_transitions: 600000`、`metric: success_rate`，满200个有效回合且达到门槛后才比较。`metric: return` 可切换为平均未折扣回报。TensorBoard 仍用100回合；未配置保持上述旧行为。恢复窗口清空后重新满窗，仅继承相同选择配置的合格排名。训练结束无合格窗口时以最终模型兜底并标记 `rate=null`。字段与预算说明见[配置说明](../../../configs/README.md)。

每跨过累计 400000 条健康 transition，独立保存一次完整 checkpoint 到 `ckpts/`，全部保留；以接收该批次并完成对应更新后的实际步数命名，例如 `ckpts/step-000400000.pt`。批量采样可能略超阈值，后续阈值仍固定为 800000、1200000 等。恢复训练按累计步数定位下一个 40 万阈值。恢复 latest 时仅保留与当前选模配置一致的排名；恢复 ckpts 历史快照时重新选模，避免引用已被后来训练覆盖的根目录 best。历史 run 的权重不自动改写。

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

仅在训练结束后评估该站位：16 个评估环境并行完成 16 次 stochastic rollout，另保留 stochastic / mean 视频。评估日志标记 `scope: fixed_station`、`split: train`，Q 文件也只含这一站。成功率反映该训练站位的表现，单站位 Spearman 为 null，不代表空间泛化或全图验收。未指定 `stations.fixed_station` 时仍使用下述空间 validation 流程。实现及验证见[单站位交付记录](14-fixed-station-training.md)。

## TensorBoard 实时监控

训练默认每 30 秒汇总一次指标并刷新 `<run>/tensorboard/`，横轴为累计健康 transitions。首次刷新无需等待 checkpoint 或评估。检查发生在接收采样批次和每次梯度更新后；单次计算或等待采样耗时超过 30 秒时，下一个检查点再刷新。

```bash
.venv/bin/tensorboard --logdir outputs/experiments/fixed-station-sac/runs --port 6006 --reload_interval 5
```

打开本机 `http://localhost:6006`；远程训练时先将服务器 6006 端口转发至本机。也可将 `--logdir` 指向单次训练的 `tensorboard/`。使用 Runs 选择器比较不同 run。

可选配置 `training.tensorboard_every_seconds` 必须大于零；省略时使用 30 秒。旧配置和 checkpoint 无需添加字段；恢复训练保持累计 transitions。同一输出目录恢复时隐藏恢复点及其之后的旧 TensorBoard 事件，新输出目录只记录恢复后的数据。`metrics.jsonl` 原始历史不清理。

| TensorBoard 分组 | 统计口径 |
|---|---|
| `loss/actor`、`loss/critic`、`loss/alpha`、`policy/entropy` | 当前时间区间内全部梯度更新的均值；Entropy 为 `-E[logπ]`，单位 nats，可为负数 |
| `policy/alpha` | 区间内最新 Alpha |
| `train/` | 全部 Sampler 按 Learner 收到事件的顺序合并，最近至多 100 个健康完整、无随机预热动作的回合：success、return、steps、collision_free、contact_bonus_count 均值；episode_count 为窗口实际数量 |
| `mobile/` | 同一窗口的底盘指标均值：max_drift_m、max_yaw_drift_rad、max_lift_drift_m，移动底盘另有 out_of_bounds、max_base_speed_m_s、max_base_yaw_speed_rad_s |
| `reward/` | 同一窗口的各项累计奖励均值；分项按 episode 的 reward_component_names 写入 |
| `warmup/`、`warmup/reward/` | 含随机预热动作的健康完整回合，维持区间均值，不进入 train 窗口 |
| `runtime/` | 最新吞吐、累计更新次数、Replay 大小、版本滞后、队列阻塞及内存；吞吐为本次进程启动后的累计健康经验 / 已用时间 |
| `evaluation/<scope>/` | 实际评估的成功率、未折扣回报、trial 数、相关性及耗时；scope 使用实际的 train 或 fixed_station，debug 阶段统计保留策略分组 |

train、mobile、reward 每次刷新按已有窗口继续展示；区间内没有新合格回合时不重复参与选模。没有更新时不补 loss，首次合格回合之前不写 train 均值；不可计算的相关性不写入。训练结束和评估前补写剩余区间，评估完成立即刷新评估指标，退出时关闭 writer。

`metrics.jsonl` 仍保存 learner、episode、evaluation 等原始记录；`training.log_every` 仍按 transitions 决定 learner JSON 和 checkpoint 的频率。JSON 中 learner 的 loss 保持原有的最后一次更新值，TensorBoard 展示区间内全部更新的均值。

不再生成 `loss-curves.png`、`exploration.png`、`training-effect.png`、`reward-breakdown.png`。站位 Q 热图、`views/station-evaluation.png`、视频和原始评估 JSON 保留。已有 run 的图片不删除，也不自动转换历史日志。

默认仍只在训练结束后评估选出的 best 模型，不因监控增加评估、视频或采样暂停。每次实际评估的原始数据仍保存于 `analysis/validation-<transition数>.json`；监控曲线不代表策略或站位图已通过最终验收。

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

### 固定Home方块的离线软价值

正式评分直接读取已有任务YAML中的固定初态和checkpoint，完全绕过仿真导入、模型/场景加载及reset，不要求初态模板文件。共享训练时的69维观测构造。旧默认`--value-mode q`保留graph任务原始min-Q评分。

```bash
PYTHONPATH="$PWD/src" .venv/bin/python scripts/station_map/rl/score.py \
  --value-mode soft --gpu 0 --checkpoint <checkpoint.pt> \
  --output <实验单元>/runs/<run> \
  --plot <实验单元>/analysis/<run>/station-value.png
```

GPU按实时空闲情况选择。soft默认配置为`configs/station_map/rl/val103_cube_random_stations_facing.yaml`，可用`--config`指定；固定Home和升降读取scene，固定方块位姿、TCP局部位姿、夹爪及范围直接读取observation中的数值。这些初态已与真实reset三站逐维核对，来源见[720万离线价值图报告](39-offline-soft-station-value.md)。训练环境仍直接读取真实物理状态，不使用这些静态观测数值替代动态观测。

默认K=128、batch size=2048、seed=20261109，可用`--action-samples`、`--batch-size`、`--seed`覆盖。输出`station-value.json`和`samples.npz`，包含[有效站位,样本]排列的action/log_prob/Q1/Q2、[有效站位,69]初始观测、station_ids和alpha。复算：`v=(minimum(q1,q2)-alpha*log_prob).mean(axis=1)`；`v_std`为ddof=1标准差，`v_sem=v_std/sqrt(K)`。无效站位分数为空，图是策略条件软价值。

## 软件检查

```bash
MUJOCO_GL=disable .venv/bin/python -m unittest \
  tests.station_map.test_rl_monitor tests.station_map.test_rl_evaluation \
  tests.station_map.test_rl_sac tests.station_map.test_rl_sampling -v
```

原主链全套软件回归仍使用主 checkout 的原环境，避免 MuJoCo / cuRobo 版本混装。真实 GPU rollout 和训练不随 unittest 自动运行。

## 阶段奖励与视频（2026-11-11）

新训练使用 `rl-pick-1cm-1s-v1`：双指承力、离开支撑并抬升 1 cm 连续保持 1 s；不限制物体相对 TCP 滑移。非 RL 默认 `strict-pick-v3` 保留。Actor 仍为 7D，DLS 联合控制臂和升降，升降命令限制 0.05 m/s。观测新增升降、实测 TCP 速度、接触事件标记与历史最高进度；旧 checkpoint 不直接 resume。

`reward_component_names` 对应 13 项实际奖励；TensorBoard 展示全部分项的健康完整回合均值，并单独展示接触事件次数。无已完成回合的区间不补零。

视频需启用 EGL 并设置上文 native 库路径。训练中不录评估视频；结束后的单次 Validation 保留固定 train / validation 各一站的 stochastic / mean 视频，并保留首次成功或碰撞代表轨迹；启动时没有评估、视频或视频专用 rollout。视频在 `views/`，状态在 `replays/`，引用、结果及渲染耗时在已有 validation JSON。视频源为实际 rollout 的状态回放；终止帧与 Home 都保留，编码时长比仿真时间多一个 0.05 s 初态帧。全景复用已有 `ThirdPersonCamera` 的分割可见性选角和机器人／目标联合取景，近景使用同一已有选角器对夹爪／目标联合取景（margin 4 cm）；同站位从相同 Home 选取相同初始角度。


## 分布式采样与集中学习

单卡继续用 `--gpu 0`；多卡用 `--gpu 0 5`（以实时空闲情况选卡）。每卡一个独立 Sampler，Learner 与首卡 Sampler 共享 GPU；CPU Replay 和有界队列集中汇总，启动后布局固定。

```bash
.venv/bin/python scripts/station_map/rl/train.py --gpu 0 5 --num-envs 128 \
  --output outputs/experiments/distributed-sampling/runs/train
```

`--num-envs` 是每个 Sampler 的环境数。配置 `transitions_per_update=128`，全局健康经验前 20000 条预热，此后每 128 条更新一次；默认 batch=256、CPU Replay=100000。每 16 次更新向各 Sampler 发布最新完整 Actor。恢复 checkpoint 时保留历史 transition 横轴，重建 Replay 并重新全局预热。

仅训练结束后的单次 Validation 评分固定 validation 子集；训练期间不暂停采样验证。之后使用同一个 best 策略计算一次全合法站位 Q，写入训练目录 `station-q.json` / `station-q.png`；非法站位保留空值。该图不代表抓取验收通过，acceptance 仍使用独立 evaluate 入口。


## 并行评估与阶段进度（2026-11-13）

`training.validation_stations` 表示选取的站位数，`validation_rollouts_per_station` 表示每站正式试验数；训练入口的评估环境数取二者乘积。单站位配置仍填写 1 / 16，由 16 个环境同时评估，不需要扩大试验预算。外部检查若传入更少环境，会分批完成同样数量的正式试验。

初态 Q 在抓取姿态和动作采样维度合批，样本数不变。结束评估返回的同一冻结 checkpoint 的 Q 结果供最终 station-q.json 复用；全图中的其他站位仍正常评分。缓存只在这次调用内显式传递，不跨 checkpoint 复用。

正式成功率试验不采集视频；视频专用 stochastic / mean 回合只存选中的站位轨迹，GPU 数据在回合结束后批量转到 CPU 并格式化。原先所有回合中挑首次成功/碰撞代表视频的路径不再默认启用，因此视频本身不保证包含正式试验中首次成功或碰撞。正式试验仍保留完整成功/碰撞等数值结果。

`analysis/evaluation-progress-<transitions>.json` 与 stdout 显示 initial_q / rollouts / videos / complete 阶段和正式试验完成数；评估 JSON 的 `metrics.stage_seconds` 记录初态 Q、正式 rollout 与视频阶段耗时。并行试验记录批次 seed 和全局 trial 编号，固定配置和批量布局可重复；向量化后的随机数顺序与旧串行版不同，不承诺旧 seed 的逐轨迹复现。实际验证见[并行评估交付](18-parallel-evaluation.md)。

## 方块接触实验（2026-11-16）

2026-11-17 当前成功与奖励口径见[连续保持0.25秒与历史最高抬升奖励](33-cube-hold-quarter-second-reward.md)，此前调整见[最低点离桌面与接触力门槛](31-cube-clearance-success-reward.md)。历史训练报告保留各次运行的原判定。

当前配置 `configs/station_map/rl/val103_cube_s0246_facing.yaml` 使用 `observation.mode: flat`、65维输入与 `success.version: rl-pick-contact-v2`：双指对方块的接触法向力各严格大于0.5 N、方块最低点距原始桌面至少0.01 m、方块无桌面/场景支撑接触，三项条件须连续保持0.25 s才成功（2026-11-17更新）。逐物理子步累计，任一条件中断即清零重新计时，达标后锁存成功并在策略步末终止。最低点按当前旋转计算，桌面高度固定为场景生成时真实支撑面高度。夹爪动作正值闭爪、负值张开；[旧v1实验](19-cube-contact-single-station.md)的成功仅表示接触。

2026-11-17更新：抓取位置误差除以0.02 m，原始旋转误差除以20°对应的弧度值，不裁剪；奖励仍用原始米和弧度。七项奖励为位置、旋转、碰撞、成功、闭爪、双指接触、抬升，关闭张开奖励；碰撞每策略步最多扣1分。

抓取阶段为严格小于3 cm和20°，首次双指同时承力奖励3分，每回合仅一次；离开阶段再进入不恢复资格。事件逐物理子步采集，策略步末结算。位置奖励为 `20×(d_previous−d_current)`，旋转奖励为 `5×(θ_previous−θ_current)`，分别使用原始米和弧度。闭爪奖励使用实际指间距：`c=clip(1-width/open_grip,0,1)`；对准时进入阶段，以当前实际进度作为基准，持续阶段按峰值增量加权3，重入不补发、整回合累计最多3分。抬升进度为 `Φ=clip(h/0.01,0,1)`，其中 `h=z_center−(size_m/2)×Σ|R_world[2,j]|−z_table`；仅双指有效接触时更新整回合历史峰值，奖励 `8×(Φ_peak_current−Φ_peak_previous)`，峰值初始为0、松开不重置、保持或下降不加分，整回合累计最多8分。抓握奖励、抬升奖励、评估与视频的双指接触条件统一为两指法向力均严格大于0.5 N；评估 `max_lift_m` 和视频 `lift` 均记录最低点离桌面高度。无桌面接触使用既有场景支撑传感器的接触标记，即使法向力很小也中断保持。成功奖励仍为一次性30分、碰撞每策略步最多扣1分；局部reset仅清除对应环境的保持计时、峰值和接触资格。

训练预算为3,000,000条健康transition，batch_size为512，预热20,000，每128条新增经验更新一次；CUDA0–3各128环境时共512环境。最新改动、验证与四卡训练见[阶段峰值奖励与成功率选模](29-cube-stage-peak-success-best-training.md)，上一轮结果见[阶段接触奖励结果](28-cube-stage-contact-b512-results.md)。旧45维和旧尺度65维checkpoint均由输入定义检查拒绝加载到新任务；奖励与batch变化使用新run从头训练。

沿用物理GPU绑定、EGL与独立Sampler/Learner；设置 `PYTHONPATH` 为本实验工作区的 `src`，并使用已安装完整RL依赖的解释器。本次验证复用 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/actor-critic/.venv/bin/python`，不安装或下载依赖。无渲染时设 `MUJOCO_GL=disable`；四卡传 `--gpu 0 1 2 3`，运行前仍核查实时占用。无启动、周期或终点站位Q图评估。正式训练须另建run、固定源码版本；本次仅验证1024条健康transition，不执行正式训练或策略成功率验收。


### 独立评估的并行数

`evaluate.py` 默认最多20个环境并行，使用 `--num-envs N` 覆盖；总试验数和checkpoint配置不变。每回合使用独立seed随机流，输出的 `num_envs` 与 `rng` 记录运行方式。比较checkpoint时使用同一试验清单和seed；旧串行结果的轨迹不保证复现。graph评估保留逐回合完整状态、速度、控制与动作回放。

方块独立评估保持20回合，默认20环境同时执行，默认保存全部20段视频和状态回放；文件名包含全局回合编号，视频元数据包含对应trial和seed。使用训练run中的config.yaml及冻结checkpoint，例如：

```bash
PYTHONPATH="$PWD/src" /data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/actor-critic/.venv/bin/python scripts/station_map/rl/evaluate.py --config <训练run>/config.yaml --checkpoint <训练run>/best.pt --gpu <物理卡号> --output <评估run>
```

渲染前按环境说明设置EGL及native库路径。独立评估不需要station Q scores。

### 2026-11-17T16:41:37+08:00 新判定四卡训练已启动

当前训练run为 `outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-164137-s0246-cube-clearance-1cm-force05-3m-b512-g0123`，源码固定于 `d618bb709b77657a72c2a449cf079079c26eca73`；CUDA0–3各128环境，从零训练300万条健康经验，batch512、seed42，无resume。已核查120,064条健康经验与781次SAC更新、四卡绑定及TensorBoard；本次采用双指>0.5 N、最低点距桌面≥1 cm和姿态权重1。详情见[启动记录](32-cube-clearance-training-launch.md)，此前“仅验证1024条”的描述属于上一轮。

### 2026-11-17T17:53:32.106298+08:00 当前CUDA2–3训练

按用户指令，从头启动当前0.25 s保持协议与新奖励配置。run为 `outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-175136-s0246-cube-hold025-3m-b512-g23`，固定源码 `bcbaaac19a6858856e6d0be3067a5fd1de71a837`；CUDA2–3各128环境，300万健康经验预算、batch512、seed42、无resume。已核查实际采样与更新；[启动记录](34-cube-hold-g23-training-launch.md)。先前CUDA0–3训练已按用户指令停止，旧产物保留。


## 随机站位、物理固定底盘、69D 方块输入

使用 `configs/station_map/rl/val103_cube_random_stations_facing.yaml`。该配置从方块周围的有效 XY 网格均匀抽样，每个位置只取正对方块中心的 yaw；Episode 内底盘无 XY/yaw 自由度，机械臂、升降和夹爪保持可控。站位通过地面支撑与 Home 无碰撞检查，不以 IK 或抓取成功预筛选。

`scene.fixed_base: true` 移除三个底盘关节及驱动器，由 mjlab 的固定运动学根节点在 reset 时设置位姿。`observation.world_state: true` 在原65维后追加底盘世界 x、y（米）、yaw（弧度）、升降高度（米），共69维；原归一化升降高度仍保留。旧单站配置继续使用65维。新任务从零训练，不将65维权重作为69维输入直接复用。奖励与连续保持0.25秒的成功协议沿用当前方块任务。

```bash
# 选卡后替换示例物理 GPU 号；全部从本工作区根目录执行。
PYTHONPATH="$PWD/src" MUJOCO_GL=disable OMP_NUM_THREADS=1 \
  .venv/bin/python tests/smoke/rl_fixed_base.py --gpu 0 \
  --output outputs/experiments/rl-cube-random-stations/runs/<smoke-run>

# 21,024条健康经验，保留20,000条预热，预期8次更新。
PYTHONPATH="$PWD/src" MUJOCO_GL=disable OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cube_random_stations_facing.yaml \
  --gpu 0 --seed 42 --num-envs 16 --max-transitions 21024 \
  --output outputs/experiments/rl-cube-random-stations/runs/<train-run>

# 固定seed选择最多20个不同有效站位，每站1次随机策略rollout，保存全部视频。
PYTHONPATH="$PWD/src" MUJOCO_GL=egl OMP_NUM_THREADS=1 \
LD_LIBRARY_PATH="$PWD/.venv/lib/native${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  .venv/bin/python scripts/station_map/rl/evaluate.py \
  --config configs/station_map/rl/val103_cube_random_stations_facing.yaml \
  --checkpoint outputs/experiments/rl-cube-random-stations/runs/<train-run>/best.pt \
  --gpu 0 --num-envs 16 \
  --output outputs/experiments/rl-cube-random-stations/runs/<train-run>/evaluation
```

评估使用 `evaluation.station_count: 20` 和 `evaluation.rollouts_per_station: 1`，有效池不足20站时全选。`selection.json` 固定站位、动作seed和trial编号；`summary.json` 包含逐站与总体统计，基础设施异常单列。轨迹单独保存 `mocap_pos` / `mocap_quat`，回放同时恢复固定底座位姿与关节状态。旧单站配置仍执行首站20回合。

本轮实际验证和限制见[交付记录](36-random-stations-physical-base.md)。短跑与随机策略评估仅验证实现链路，不作为Actor已学会多站抓取的证据。

## 随机站位移动底盘：75D输入、10D动作

2026-11-18：配置 `configs/station_map/rl/val103_cube_random_stations_mobile.yaml`。`action.mobile_base` 控制Actor是否移动底盘，默认false；启用时须 `scene.fixed_base: false`，使用flat且world_state开启。动作顺序为TCP平移3、TCP旋转3、夹爪1、底盘前后/横移/转向3。平移合速度上限0.2 m/s、转向0.5 rad/s；升降仍由8关节DLS分配，上限0.05 m/s。TCP目标随实测底盘坐标系运动。

保留目标周围2 m半宽方形内的局部场景；机器人或目标任一碰撞几何的世界包围盒越界，回合失败，计入失败分母。初始站位同样检查。观测为75D：上一动作扩展为10D，并追加实际底盘速度3D，按配置速度归一化。移动本身不触发固定底盘漂移失败。世界坐标和底盘速度均由实测状态计算。

```bash
PYTHONPATH="$PWD/src" LD_LIBRARY_PATH="$PWD/.venv/lib/native:${LD_LIBRARY_PATH:-}" \
  MUJOCO_GL=egl .venv/bin/python tests/smoke/rl_mobile.py \
  --config configs/station_map/rl/val103_cube_random_stations_mobile.yaml \
  --gpu 0 --output outputs/experiments/rl-cube-mobile/runs/<control-run>

PYTHONPATH="$PWD/src" LD_LIBRARY_PATH="$PWD/.venv/lib/native:${LD_LIBRARY_PATH:-}" \
  MUJOCO_GL=egl .venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cube_random_stations_mobile.yaml \
  --gpu 0 1 2 3 --max-transitions 10000000 \
  --output outputs/experiments/rl-cube-mobile/runs/<training-run>
```

GPU编号仅作命令示例，启动前按实时占用选卡。移动模式从零训练，target_entropy为-10；旧7D checkpoint可加载用于旧模式，不可resume为10D。每30秒监控新增越界回合比例、最大底盘速度/角速度以及底盘与升降位移；episode JSON另含初始/最终底盘姿态及结束速度。保持当前rl-pick-contact-v2的抓取判定和奖励，不增设移动奖励。移动策略的站位价值表示从该初态出发允许移动后的价值，原固定底盘离线score入口不接受此配置。
