# 固定底盘 mjlab / SAC 研究入口

- 状态：active
- 日期：2026-11-09
- 替代：无
- 关联：../plan/01-implementation.md、../delivery/04-implementation.md

从 actor-critic 工作区根目录运行。实际验证状态以交付记录为准；长训练前先完成脚本动作真实抓取、抬升和保持验证。

## 安装

```bash
scripts/install_rl.sh /home/wenyifan/wenyifan/lmdf/LastMileDataFlow/reference_repos/mjlab cu130
.venv/bin/python scripts/experiments/train_sac.py --help
```

研究环境使用 Python 3.11、本地 editable mjlab 和源码 `.pth`。直接使用 `.venv/bin/python`；如使用 uv，传 `uv run --no-sync`。不要对研究环境执行根项目默认依赖同步。

镜像源可通过 `RL_INDEX_URL` 指定，已下载的 wheel 目录可通过 `RL_WHEEL_DIR` 指定。本次主机缺少 GLVND 用户态库，已将 Ubuntu 包中的库解压至 `.venv/lib/native`，没有改系统驱动；需要 EGL 时运行：

```bash
export LD_LIBRARY_PATH="$PWD/.venv/lib/native${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
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

配置为 `configs/rl/val103_cup30.json`，数据根为 `/data1/wenyifan/lmdf/molmospaces_data`，抓取变换来自该根下的 Cup_30 抓取库固定 row。修改抓取行、奖励、控制或 split 后，作为新的有效配置重新记录。

正式实验先按仓库规则提交源码并固定 revision，在实验单元 README 记录状态、源码、输入、报告；运行期间不改源码。产物单元示例为 `outputs/experiments/val103-cup30-sac-20261109/`，训练结果放其 `runs/train/`。

从 `git archive` 导出的源码启动时，将 `LASTMILE_SOURCE_REVISION` 设为导出时的提交号，并将 `PYTHONPATH` 指向导出目录的 `src/`，避免 checkpoint 把外层工作区后来更新的 HEAD 当作运行版本。

```bash
MUJOCO_GL=disable .venv/bin/python scripts/experiments/train_sac.py \
  --config configs/rl/val103_cup30.json --gpu 4 --seed 42 \
  --output outputs/experiments/val103-cup30-sac-20261109/runs/train \
  --max-transitions 200000
```

输出 `initial.pt`、`latest.pt`、`best.pt` 和 `metrics.jsonl`。初始 checkpoint 用作未训练 Actor 基线；`best.pt` 按独立 validation 成功率、同率时按平均 episode return 选择。checkpoint 包含有效配置、字段、运行版本、两个源码 revision、RNG、优化器和计数器；不保存 Replay / 环境内存。

恢复时增加 `--resume <latest.pt>`，`--max-transitions` 为累计目标；恢复参数 / 优化器后重新 reset 并 warmup Replay，不承诺逐步精确续跑。

## 六项监控与四组图

`metrics.jsonl` 保存 `learner`、`episode`、`evaluation` 三类记录：Actor Loss / Critic Loss / Entropy 为日志间隔内更新均值；Episode Return 是实际回合奖励和；独立评估记录成功数、健康 trial 数、总 trial 数、基础设施失败及 Value-Success Spearman。

默认每 100,000 条有效 transition 做一次独立评估，开始和结束也评估。固定抽取 8 个合法 validation 站位，每站位 2 次 stochastic rollout；使用独立向量环境，暂停参数更新后计算全图 Q 和实际站位成功率。acceptance 集合保留到最终验收。评估 seeds 固定，评估前后的 Torch RNG 恢复，避免改变训练采样序列。

训练目录的 `views/` 持续更新四组 PNG：

| 文件 | 内容 |
|---|---|
| `loss-curves.png` | Actor Loss、Critic Loss |
| `exploration.png` | tanh 策略动作的 `-E[logπ]`，单位 nats，可为负数 |
| `training-effect.png` | 独立 validation 成功率、每回合累计奖励 |
| `station-evaluation.png` | 固定色标的 max-yaw Critic Value Map、真实 Value-Success Scatter 与 Spearman |

每次评估原始数据保存在 `analysis/validation-<transition数>.json`，包含实际 trials、站位成功率与全图原始 Q。全部成功率相同或数据不足时，相关性记录为 null、图中标 N/A；不填成 0。监控曲线本身不代表策略或站位图已通过最终验收。

独立检查监控链路可运行下列真实 smoke；它只做两次诊断更新，不作为训练结果：

```bash
MUJOCO_GL=disable .venv/bin/python tests/smoke/rl.py \
  --gpu 4 --num-envs 8 --steps 5 --random-actions --learner-updates 2 --monitor \
  --output <smoke单元>/runs/smoke.json
```

## 冻结评分与验收

```bash
MUJOCO_GL=disable .venv/bin/python scripts/experiments/score_stations.py \
  --gpu 4 --checkpoint <训练checkpoint> --output <实验单元>/analysis/q
MUJOCO_GL=disable .venv/bin/python scripts/experiments/evaluate_sac.py \
  --gpu 4 --checkpoint <同一训练checkpoint> --scores <实验单元>/analysis/q/station-q.json \
  --output <实验单元>/runs/evaluation
MUJOCO_GL=disable .venv/bin/python scripts/experiments/evaluate_sac.py \
  --gpu 4 --checkpoint <initial.pt> --baseline --scores <实验单元>/analysis/q/station-q.json \
  --output <实验单元>/runs/baseline
```

评分使用在线 Q1/Q2，K=16 是动作采样数。输出原始 Q、动作评分标准差、站位 split 和 invalid mask；图不代表成功概率。

评估先保存 `selection.json`，再保存 trial JSONL、状态 / 动作 NPZ 和统计。训练 Actor 与初始化基线共用评分选点、rollout seeds 和 stochastic Actor 口径。抓取成功、无非法碰撞抓取成功、Q 排序分别判定；不能仅凭图有颜色或 Q 增长通过验收。

## 软件检查

```bash
MUJOCO_GL=disable .venv/bin/python -m unittest \
  tests.policies.test_sac tests.runtime.test_pick_state \
  tests.runtime.test_dls tests.scenes.test_rl_observation \
  tests.data.test_station_q tests.workflows.test_rl_evaluation -v
```

原主链全套软件回归仍使用主 checkout 的原环境，避免 MuJoCo / cuRobo 版本混装。真实 GPU rollout 和训练不随 unittest 自动运行。
