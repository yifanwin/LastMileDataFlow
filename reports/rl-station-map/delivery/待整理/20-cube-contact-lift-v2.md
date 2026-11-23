# 方块双指接触与相对抬升 v2 交付

- 状态：done（实现、定向回归、真实脚本物理验证、四卡短训练完成）
- 日期：2026-11-16
- 替代：当前方块任务的 v1 输入、动作方向、成功判定和奖励；不替代旧实验结果
- 关联：[需求](../requirement/01-fixed-base-sac.md)、[配置](../../../configs/station_map/rl/val103_cube_s0246_facing.yaml)、[运行指南](running.md)、[旧v1结果](19-cube-contact-single-station.md)
- 执行记录时间：2026-11-16T22:34:01+08:00；验收产物核查时间：2026-11-16T22:33:52+08:00

## 目标与实现

已按用户确认改为：同一个原生物理子步左右手指对方块法向力各 >1e-6 N，且**方块中心世界高度相对回合静置初态抬升至少0.0086 m（0.86 cm）**，即锁存成功，策略步末终止。不附加保持、离支撑或姿态门限。协议为 `rl-pick-contact-v2`；保留已有物理碰撞、失败、数值健康检查。

flat输入由45维改为65维：物体位置3、姿态6、局部尺寸3、底部高度1、抓取误差6、线/角速度各3；机器人臂7关节及升降位置/速度各8、TCP位姿9及线/角速度各3、夹爪开度/速度各1、上一动作7。位置、姿态与速度使用底盘系；姿态按旋转矩阵第一列、第二列拼接；底部高度使用旋转后的最低点。目标速度与TCP速度复用同一移动底盘速度变换，避免重复实现。字段顺序和尺度由 `FLAT_FIELDS` 记录并随checkpoint保存。

七项奖励顺序为 `position, orientation, collision, success, opening, closing, grasp`：位置进度系数10、旋转进度系数0.5、任一子步非法碰撞每策略步最多-2、首次成功+30；d>4cm时按负夹爪动作给予0.02倍张开奖励；d<3cm且θ<10°时按正夹爪动作给予0.05倍闭爪奖励，并在双指接触时每步+0.05。不乘dt。奖励用最后物理子步的同步位姿/接触；成功与碰撞跨本策略步锁存。利用上游在末次forward之前计算奖励的顺序，不增加逐子步位姿误差计算与额外缓存。

仅方块协议的夹爪动作改为正值闭爪、负值张开。SAC、Replay、Sampler和TensorBoard复用现有flat及奖励名称机制；去掉方块日志中不再适用的一次性contact_bonus_count，七项奖励保留实际累计值。Cup图模式保持原行为。旧45维模型通过现有输入定义检查被拒绝，不提供旧方块协议兼容分支，需从头训练。

## 验证结果

| 验证 | 实际结果 | 边界 |
|---|---|---|
| 定向RL测试 | 初轮22项通过 | 覆盖输入、奖励、动作方向、Replay、SAC和checkpoint；初轮新增测试自身的共享张量问题已修正 |
| 最新station_map回归 | **85项全部通过，无跳过**，8.669s | 包含图模式、相对底盘速度、倾斜方块底部高度、门限、事件日志与七项TensorBoard分量 |
| 真GPU0脚本抓取抬升 | **成功**，第95策略步/4.75s终止 | 同一套7D动作、DLS和真实物理；不代表学习策略成功 |
| CUDA0–3短训练 | **1024条健康transition、7次更新、Replay1024** | 全局预热128、batch256，每卡128环境；无完整回合，不计算策略成功率 |
| checkpoint独立加载 | 65维、flat、v2协议、七奖励名称均匹配；CPU推理有限 | 已核查latest.pt；短训练best.pt因无合格回合，明确回退最终模型 |

物理初始中心z=0.770270765 m。首次成功位于原生子步索引2364（从0开始），该子步左右手指法向力为5.155894/5.133976 N、相对抬升0.008630514 m，failed/unhealthy均为false。策略步末抬升0.009844005 m。全程collision_free=true、collision_time=0，成功奖励累计恰为30。真实观测核查65维、刚执行动作回填，以及reset后动作清零和成功标记清除。

四卡短训练各Sampler实际入库样本为GPU0=384、GPU1=256、GPU2=256、GPU3=128；采样阶段11.646s（包含Sampler启动等开销，不是稳态吞吐），shutdown丢弃512条在途样本。Actor/Critic/alpha损失和熵均有限，TensorBoard实际记录更新与运行曲线。此短run没有完整episode，因此没有真实episode奖励曲线；七项奖励入日志和TensorBoard的接口由软件测试验证，真实脚本另保存逐步七项奖励。

首次四卡短验证失败：我将临时配置写为JSON内容但使用.yaml后缀，YAML解析器把 `1e-06` 读为字符串，Sampler比较接触阈值时报TypeError。已改用YAML序列化，保留失败run，在新run重跑通过；未为此增加配置兜底或修改仓库配置解析器。

## 环境、产物与复现

工作区 `/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact`；基线commit `cd0e5325466345c8286839cf216c8a42c3059c16`，运行时为dirty，**该commit不能单独代表此次代码**。保留原有19号报告修改，历史运行及checkpoint未删除、搬迁或改写。原生采样摘要记录了运行时Git状态，训练checkpoint记录了有效配置和依赖版本。无依赖安装或下载。

复用已有 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/actor-critic/.venv/bin/python`，`PYTHONPATH=src`、`MUJOCO_GL=disable`、`OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`；缓存沿用 `/tmp/lastmile-{mpl,warp-cache,cuda-cache}`。启动前CUDA0–3均为0%利用率、0MiB显存占用；未使用GPU4–7。

原始产物均位于工作区 `outputs/experiments/scratch/rl-cube-contact/runs/`：

- `20261116-v2-offline/station-tests.log`：最终85项回归日志。
- `20261116-v2-physics-gpu0/`：验证脚本、控制台、逐策略步七奖励、summary.json、native-substeps.npz（同步左右指力、抬升、成功/失败/健康标记及逐策略步qpos）。
- `20261116-v2-short-g0123/`：失败临时配置及完整错误日志。
- `20261116-v2-short-g0123-r02/`：成功短训练的有效配置、metrics.jsonl、TensorBoard、latest.pt/best.pt和verification.json。

软件回归命令：

```bash
MUJOCO_GL=disable PYTHONPATH=src MPLCONFIGDIR=/tmp/lastmile-mpl \
  /data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/actor-critic/.venv/bin/python \
  -m unittest discover -s tests/station_map -t . -v
```

四卡短验证使用上述环境运行 `scripts/station_map/rl/train.py`，参数为：

```text
--config outputs/experiments/scratch/rl-cube-contact/runs/20261116-v2-short-g0123-r02/config.yaml
--gpu 0 1 2 3 --num-envs 128 --max-transitions 1024 --seed 42
--output <新的run目录>
```

已有run不可覆盖。该短配置额外将日志间隔设128、TensorBoard刷新设1s、环境初始化槽位设1；正式仓库配置仍保留原训练预算和30s刷新。

## 限制与后续

本轮没有启动正式长期训练，没有运行新任务学习策略的成功率评估，没有声称Actor已学会抓取抬升，也未将旧v1的5%成功率套用到v2。未运行全仓回归；局部修改以station_map全专题测试和真实短验证验收。后续正式训练使用固定源码版本、新run和65维新模型，另行评价真实策略表现。
